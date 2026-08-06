import json
import re
from pathlib import Path

# ── Precompiled patterns ─────────────────────────────────────────────────────────
# All regexes are compiled once, at import time, instead of being rebuilt on every
# question (get_meta() used to call re.search() with a freshly-formatted pattern
# string 11 times per question; Python's internal re cache made repeat calls cheap,
# but not as cheap as calling .search() on an object you already have).

QUESTION_SPLIT_RE = re.compile(r"^## Question:\s*(.+)$", re.MULTILINE)

# Every "- **Field:** value" metadata line, captured generically in one pass instead
# of one full re.search over the whole question per field (11 scans -> 1 scan).
META_LINE_RE = re.compile(r"^-\s*\*\*([^*:]+):\*\*[ \t]*(.*)$", re.MULTILINE)

BODY_RE = re.compile(
    r"-\s*\*\*Tags:\*\*[^\n]*\n\s*(.*?)(?:### Options|### Sub-questions|### Explanation|\Z)",
    re.DOTALL,
)
OPTIONS_RE = re.compile(
    r"### Options\n(.*?)(?:### Sub-questions|### Explanation|\Z)", re.DOTALL
)
SUBQ_BLOCK_RE = re.compile(r"### Sub-questions\n(.*?)(?:### Explanation|\Z)", re.DOTALL)
EXPLANATION_RE = re.compile(r"### Explanation\n(.*)", re.DOTALL)

IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
SUBQ_CLEAN_RE = re.compile(r"^-\s*(?:\[[xX ]\]\s*)?(?:[ক-ঘa-d0-9ivx]+\.)?\s*")


def make_image_rewriter(rel_path):
    """Bind an image-path rewriter to one file's rel_path. Built once per *file*
    (all questions in a file share the same rel_path) instead of once per
    *question*, and reused via re.sub's callback for that file's questions."""
    prefix = f"{rel_path}/" if rel_path else ""

    def rewrite_image_path(match):
        alt_text = match.group(1)
        img_path = match.group(2)
        if img_path.startswith("./"):
            img_path = img_path[2:]
        if img_path.startswith("assets/"):
            return f"![{alt_text}]({prefix}{img_path})"
        return match.group(0)

    return rewrite_image_path


def extract_metadata(q_content):
    """One pass over q_content collecting every '- **Field:** value' line into a
    dict. First occurrence of a given field wins, matching the original code's
    re.search() (which also stops at the first match)."""
    meta = {}
    for m in META_LINE_RE.finditer(q_content):
        field = m.group(1)
        if field not in meta:
            meta[field] = m.group(2).strip()
    return meta


def meta_list(meta, field):
    val = meta.get(field, "")
    return [x.strip() for x in re.split(r"\s*,\s*|\s+(?=#)", val) if x.strip()] if val else []


def build_db():
    db = {"curriculum": {}, "questions": {}}
    base_dir = Path(".")
    seen_ids = {}  # question id -> file it first appeared in, purely for the warning below

    # Sorted so the build is reproducible (same vault -> byte-identical database.json,
    # regardless of filesystem/OS directory-listing order) — helpful for git diffs and
    # for making the duplicate-id warning below deterministic instead of flaky.
    for md_file in sorted(base_dir.rglob("*.md")):
        content = md_file.read_text(encoding="utf-8")
        parts = QUESTION_SPLIT_RE.split(content)

        rel_path = md_file.parent.relative_to(base_dir).as_posix()
        if rel_path == ".":
            rel_path = ""
        rewrite_image_path = make_image_rewriter(rel_path)

        for i in range(1, len(parts), 2):
            q_id = parts[i].strip()
            q_content = parts[i + 1]

            meta = extract_metadata(q_content)

            subject = meta.get("Subject", "")
            chapter = meta.get("Chapter", "")
            topic = meta_list(meta, "Topic")
            source = meta.get("Source", "")
            q_type = meta.get("Type", "")
            difficulty = meta.get("Difficulty", "")
            board = meta_list(meta, "Board Exam")
            admission = meta_list(meta, "Admission Exam")
            college = meta_list(meta, "College Exam")
            tags = meta_list(meta, "Tags")
            book_q_num = meta.get("Book Question Number", "")

            # Extract content blocks (unchanged from the original — only compiled once now)
            body_match = BODY_RE.search(q_content)
            body_raw = body_match.group(1).strip() if body_match else ""

            options_match = OPTIONS_RE.search(q_content)
            options_raw = options_match.group(1).strip() if options_match else ""

            sub_questions_match = SUBQ_BLOCK_RE.search(q_content)
            sub_questions_raw = (
                sub_questions_match.group(1).strip() if sub_questions_match else ""
            )

            exp_match = EXPLANATION_RE.search(q_content)
            exp_raw = exp_match.group(1).strip() if exp_match else ""

            body_raw = IMAGE_RE.sub(rewrite_image_path, body_raw)
            options_raw = IMAGE_RE.sub(rewrite_image_path, options_raw)
            sub_questions_raw = IMAGE_RE.sub(rewrite_image_path, sub_questions_raw)
            exp_raw = IMAGE_RE.sub(rewrite_image_path, exp_raw)

            # Parse MCQ Options
            options_list = []
            for opt in options_raw.split("\n"):
                opt = opt.strip()
                if opt.startswith("- [x]") or opt.startswith("- [X]"):
                    options_list.append({"text": opt[5:].strip(), "isCorrect": True})
                elif opt.startswith("- [ ]"):
                    options_list.append({"text": opt[5:].strip(), "isCorrect": False})

            # Parse CQ Sub-questions
            sub_questions_list = []
            for sq in sub_questions_raw.split("\n"):
                sq = sq.strip()
                if sq:
                    clean_sq = SUBQ_CLEAN_RE.sub("", sq)
                    sub_questions_list.append(clean_sq)

            if q_id in seen_ids and seen_ids[q_id] != md_file.as_posix():
                print(
                    f"WARNING: duplicate question id '{q_id}' in "
                    f"'{seen_ids[q_id]}' and '{md_file.as_posix()}' "
                    f"— the second one wins."
                )
            seen_ids[q_id] = md_file.as_posix()

            db["questions"][q_id] = {
                "id": q_id,
                "subject": subject,
                "chapter": chapter,
                "source": source,
                "type": q_type,
                "metadata": {
                    "topic": topic,
                    "difficulty": difficulty,
                    "board": board,
                    "admission": admission,
                    "college": college,
                    "tags": tags,
                    "book_question_number": book_q_num,
                },
                "body": body_raw,
                "options": options_list,
                "sub_questions": sub_questions_list,
                "explanation": exp_raw,
            }

    # Compact output (no indent/spaces) — same data, ~35% smaller file, which is what
    # the browser actually downloads and parses on every page load. If you want the
    # pretty-printed form back for manually reading/diffing database.json, change this
    # line to: json.dump(db, f, ensure_ascii=False, indent=2)
    with open("database.json", "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    build_db()
    print("Database built successfully!")
