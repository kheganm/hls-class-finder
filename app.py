import json
import os
from pathlib import Path

from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request

from db import get_db

app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
)

CATALOG_PATH = Path(__file__).parent / "courses.json"

# Comma-separated Slack user IDs (e.g. "U01ABC,U02XYZ") allowed to run admin commands
ADMIN_USER_IDS = {
    uid.strip() for uid in os.environ.get("SLACK_ADMIN_USER_IDS", "").split(",") if uid.strip()
}


def ephemeral(respond, text: str):
    """Force ephemeral (only the invoking user sees it)."""
    respond(response_type="ephemeral", text=text)

# ---- Catalog loading ---------------------------------------------------------

with CATALOG_PATH.open(encoding="utf-8") as f:
    CATALOG: list[dict] = json.load(f)

# Indexes for fast lookup
BY_SECTION_ID: dict[str, dict] = {c["section_id"].upper(): c for c in CATALOG}
BY_COURSE_NUM: dict[str, list[dict]] = {}
for c in CATALOG:
    BY_COURSE_NUM.setdefault(c["course_number"], []).append(c)


def _clean(query: str) -> str:
    """Strip wrapping characters Slack commonly adds when users copy formatted text
    (backticks from code spans, angle brackets from auto-linked URLs, smart quotes)."""
    q = query.strip()
    # Repeatedly strip any combo of these leading/trailing chars
    strip_chars = "`<>\"'“”‘’ \t"
    while q and q[0] in strip_chars:
        q = q[1:]
    while q and q[-1] in strip_chars:
        q = q[:-1]
    return q


def find_sections(query: str) -> list[dict]:
    """Resolve a user query to zero, one, or many catalog sections."""
    q = _clean(query)
    if not q:
        return []

    # 1. Exact section_id
    if q.upper() in BY_SECTION_ID:
        return [BY_SECTION_ID[q.upper()]]

    # 2. Exact course number
    if q in BY_COURSE_NUM:
        return list(BY_COURSE_NUM[q])

    # 3. Title substring (case-insensitive)
    q_lower = q.lower()
    matches = [c for c in CATALOG if q_lower in c["title"].lower()]
    return matches


def format_section(c: dict) -> str:
    return (
        f"*{c['title']}* — `{c['section_id']}`\n"
        f"  Course #{c['course_number']} • {c['term']} • {c['credits']} credits\n"
        f"  Faculty: {c['faculty']}\n"
        f"  {c['delivery_mode']} • {c['schedule']}"
    )


def format_section_short(c: dict) -> str:
    return f"`{c['section_id']}` — {c['title']} ({c['faculty']}, {c['term']})"


# ---- Commands ---------------------------------------------------------------


@app.command("/enroll")
def enroll(ack, command, respond):
    ack()
    query = command["text"].strip()
    if not query:
        ephemeral(respond, "Usage: `/enroll <course number | section id | course name>`")
        return

    matches = find_sections(query)

    if not matches:
        ephemeral(respond, f"No courses found matching `{query}`. Try `/coursesearch <keyword>`.")
        return

    if len(matches) > 1:
        lines = [format_section_short(c) for c in matches[:10]]
        extra = f"\n…and {len(matches) - 10} more" if len(matches) > 10 else ""
        ephemeral(respond, 
            f"Multiple sections match `{query}`. Re-run with a specific section id:\n"
            + "\n".join(lines) + extra
        )
        return

    section = matches[0]
    user_id = command["user_id"]
    with get_db() as conn:
        existing = conn.execute(
            "SELECT 1 FROM enrollments WHERE user_id = ? AND section_id = ?",
            (user_id, section["section_id"]),
        ).fetchone()
        if existing:
            ephemeral(respond, f"You're already enrolled in `{section['section_id']}`.")
            return
        conn.execute(
            "INSERT INTO enrollments VALUES (?, ?)",
            (user_id, section["section_id"]),
        )
    ephemeral(respond, f"Enrolled in:\n{format_section(section)}")


@app.command("/unenroll")
def unenroll(ack, command, respond):
    ack()
    query = command["text"].strip()
    if not query:
        ephemeral(respond, "Usage: `/unenroll <course number | section id>`")
        return

    matches = find_sections(query)
    if not matches:
        ephemeral(respond, f"No courses found matching `{query}`.")
        return
    if len(matches) > 1:
        lines = [format_section_short(c) for c in matches[:10]]
        ephemeral(respond, "Multiple sections match. Specify a section id:\n" + "\n".join(lines))
        return

    section = matches[0]
    user_id = command["user_id"]
    with get_db() as conn:
        existing = conn.execute(
            "SELECT 1 FROM enrollments WHERE user_id = ? AND section_id = ?",
            (user_id, section["section_id"]),
        ).fetchone()
        if not existing:
            ephemeral(respond, f"You weren't enrolled in `{section['section_id']}`.")
            return
        conn.execute(
            "DELETE FROM enrollments WHERE user_id = ? AND section_id = ?",
            (user_id, section["section_id"]),
        )
    ephemeral(respond, f"Removed from *{section['title']}* (`{section['section_id']}`).")


@app.command("/myclasses")
def my_classes(ack, command, respond):
    ack()
    user_id = command["user_id"]
    with get_db() as conn:
        rows = conn.execute(
            "SELECT section_id FROM enrollments WHERE user_id = ?",
            (user_id,),
        ).fetchall()

    if not rows:
        ephemeral(respond, "You're not enrolled in any classes. Use `/enroll <course>` to add one.")
        return

    sections = [BY_SECTION_ID.get(r[0].upper()) for r in rows]
    sections = [s for s in sections if s]
    sections.sort(key=lambda c: (c["term"], c["title"]))

    blocks = [format_section(s) for s in sections]
    ephemeral(respond, "*Your classes:*\n\n" + "\n\n".join(blocks))


@app.command("/classmates")
def classmates(ack, command, respond):
    ack()
    query = command["text"].strip()
    if not query:
        ephemeral(respond, "Usage: `/classmates <course number | section id>`")
        return

    matches = find_sections(query)
    if not matches:
        ephemeral(respond, f"No courses found matching `{query}`.")
        return
    if len(matches) > 1:
        lines = [format_section_short(c) for c in matches[:10]]
        ephemeral(respond, "Multiple sections match. Specify a section id:\n" + "\n".join(lines))
        return

    section = matches[0]
    user_id = command["user_id"]
    with get_db() as conn:
        rows = conn.execute(
            "SELECT user_id FROM enrollments WHERE section_id = ? ORDER BY user_id",
            (section["section_id"],),
        ).fetchall()

    if not rows:
        ephemeral(respond, f"No one is enrolled in *{section['title']}* (`{section['section_id']}`) yet.")
        return

    others = [r[0] for r in rows if r[0] != user_id]
    you_enrolled = any(r[0] == user_id for r in rows)

    lines = []
    if you_enrolled:
        lines.append("• You")
    lines.extend(f"• <@{uid}>" for uid in others)

    header = f"*Students in {section['title']}* (`{section['section_id']}`):"
    ephemeral(respond, header + "\n" + "\n".join(lines))


@app.command("/coursesearch")
def course_search(ack, command, respond):
    ack()
    query = command["text"].strip().lower()
    if not query:
        ephemeral(respond, "Usage: `/coursesearch <keyword>`")
        return

    matches = [
        c for c in CATALOG
        if query in c["title"].lower()
        or query in c["faculty"].lower()
        or query in c["subject_areas"].lower()
    ]

    if not matches:
        ephemeral(respond, f"No courses found matching `{query}`.")
        return

    matches.sort(key=lambda c: c["title"])
    shown = matches[:15]
    lines = [format_section_short(c) for c in shown]
    extra = f"\n…and {len(matches) - 15} more. Narrow your search." if len(matches) > 15 else ""
    ephemeral(respond, f"*Found {len(matches)} course(s) matching `{query}`:*\n" + "\n".join(lines) + extra)


@app.command("/classhelp")
def class_help(ack, command, respond):
    ack()
    is_admin = command["user_id"] in ADMIN_USER_IDS
    lines = [
        "*HLS Class Finder — available commands:*",
        "",
        "• `/enroll <course# | section id | name>` — add yourself to a class",
        "• `/unenroll <course# | section id>` — remove yourself from a class",
        "• `/myclasses` — list your classes with full details",
        "• `/classmates <course# | section id>` — see who else is in a class",
        "• `/coursesearch <keyword>` — search the catalog by title, faculty, or subject",
        "• `/classhelp` — show this message",
    ]
    if is_admin:
        lines.append("• `/popular` — _(admin)_ top 5 most-enrolled classes")
    lines += [
        "",
        "_Tip:_ Section ids look like `2000-Block-2027SP`. If a course has multiple sections, "
        "`/enroll` will list them and ask you to pick one.",
    ]
    ephemeral(respond, "\n".join(lines))


@app.command("/popular")
def popular(ack, command, respond):
    ack()
    user_id = command["user_id"]
    if user_id not in ADMIN_USER_IDS:
        ephemeral(respond, ":lock: This command is restricted to admins.")
        return

    with get_db() as conn:
        rows = conn.execute(
            "SELECT section_id, COUNT(*) AS n "
            "FROM enrollments GROUP BY section_id "
            "ORDER BY n DESC, section_id LIMIT 5"
        ).fetchall()

    if not rows:
        ephemeral(respond, "No enrollments yet.")
        return

    lines = []
    for i, (section_id, count) in enumerate(rows, 1):
        section = BY_SECTION_ID.get(section_id.upper())
        title = section["title"] if section else "(unknown)"
        faculty = section["faculty"] if section else ""
        lines.append(f"{i}. *{title}* — {count} student(s)  `{section_id}` _{faculty}_")

    ephemeral(respond, "*Top 5 most popular classes:*\n" + "\n".join(lines))


# ---- HTTP server (Render Web Service) --------------------------------------

flask_app = Flask(__name__)
_handler = SlackRequestHandler(app)


@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return _handler.handle(request)


@flask_app.route("/", methods=["GET"])
def health():
    return "OK", 200


if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 3000)))
