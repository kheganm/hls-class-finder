"""
Parse the HLS course catalog markdown into courses.json.

Run once whenever the catalog changes:
    python parse_catalog.py <path-to-catalog.md>

Output: courses.json in the project directory.
"""
import json
import re
import sys
from pathlib import Path


def parse_catalog(md_text: str) -> list[dict]:
    # Split on "---" separators, skip the title block
    blocks = [b.strip() for b in md_text.split("---") if b.strip()]
    # Drop the header block ("# Harvard Law School Course Catalog...")
    blocks = [b for b in blocks if not b.lstrip().startswith("#")]

    courses = []
    for block in blocks:
        course = parse_block(block)
        if course:
            courses.append(course)
    return courses


def parse_block(block: str) -> dict | None:
    lines = [ln.rstrip() for ln in block.splitlines() if ln.strip()]
    if not lines:
        return None

    title = lines[0].strip()
    body = " ".join(lines[1:])

    # Course #: 2000 Term: 2027SP Faculty: Block, Sharon Credits: 3.00
    course_num = _grab(r"Course\s*#:\s*(\S+)", body)
    term = _grab(r"Term:\s*(\S+)", body)
    # Faculty ends at "Credits:"
    faculty = _grab(r"Faculty:\s*(.*?)\s+Credits:", body)
    credits_s = _grab(r"Credits:\s*([\d.]+)", body)
    type_ = _grab(r"Type:\s*(.*?)\s+Subject Areas:", body)
    # Subject Areas ends at "Delivery Mode:"
    subjects = _grab(r"Subject Areas:\s*(.*?)\s+Delivery Mode:", body)
    delivery = _grab(r"Delivery Mode:\s*(.*?)\s+Days and Times:", body)
    schedule = _grab(r"Days and Times:\s*Location\s*(.*)$", body)

    if not course_num:
        return None

    # Build a stable section id: course#-lastname-term
    first_faculty_last = ""
    if faculty:
        first = faculty.split(";")[0].strip()
        first_faculty_last = first.split(",")[0].strip()
    section_id = f"{course_num}-{first_faculty_last}-{term}".replace(" ", "")

    return {
        "section_id": section_id,
        "title": title,
        "course_number": course_num,
        "term": term or "",
        "faculty": faculty.strip() if faculty else "",
        "credits": float(credits_s) if credits_s else 0.0,
        "type": type_.strip() if type_ else "",
        "subject_areas": subjects.strip() if subjects else "",
        "delivery_mode": delivery.strip() if delivery else "",
        "schedule": schedule.strip() if schedule else "",
    }


def _grab(pattern: str, text: str) -> str:
    m = re.search(pattern, text, re.DOTALL)
    return m.group(1).strip() if m else ""


def main():
    if len(sys.argv) != 2:
        print("Usage: python parse_catalog.py <path-to-catalog.md>")
        sys.exit(1)

    src = Path(sys.argv[1])
    md = src.read_text(encoding="utf-8")
    courses = parse_catalog(md)

    out = Path(__file__).parent / "courses.json"
    out.write_text(json.dumps(courses, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Parsed {len(courses)} courses -> {out}")


if __name__ == "__main__":
    main()
