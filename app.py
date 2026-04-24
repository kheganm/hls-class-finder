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

# Comma-separated Slack user IDs allowed to run admin commands
ADMIN_USER_IDS = {
    uid.strip() for uid in os.environ.get("SLACK_ADMIN_USER_IDS", "").split(",") if uid.strip()
}


def ephemeral(respond, text=None, blocks=None):
    payload = {"response_type": "ephemeral"}
    if text is not None:
        payload["text"] = text
    if blocks is not None:
        payload["blocks"] = blocks
        payload.setdefault("text", "")  # fallback
    respond(**payload)


# ---- Catalog loading -------------------------------------------------------

with CATALOG_PATH.open(encoding="utf-8") as f:
    CATALOG: list[dict] = json.load(f)

BY_SECTION_ID: dict[str, dict] = {c["section_id"].upper(): c for c in CATALOG}
BY_COURSE_NUM: dict[str, list[dict]] = {}
for c in CATALOG:
    BY_COURSE_NUM.setdefault(c["course_number"], []).append(c)


def _clean(query: str) -> str:
    """Strip wrapping chars Slack adds on copy (backticks, <>, quotes)."""
    q = query.strip()
    strip_chars = "`<>\"'“”‘’ \t"
    while q and q[0] in strip_chars:
        q = q[1:]
    while q and q[-1] in strip_chars:
        q = q[:-1]
    return q


def _faculty_lastnames(faculty: str) -> list[str]:
    """Parse 'Block, Sharon; Vermeule, Adrian' → ['block', 'vermeule']."""
    names = []
    for entry in faculty.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        # "Lastname, Firstname" → take first comma-separated chunk
        last = entry.split(",")[0].strip().lower()
        if last:
            names.append(last)
    return names


def find_sections(query: str, include_subjects: bool = False) -> list[dict]:
    """Resolve a user query to zero, one, or many catalog sections.

    Matching priority (first tier that returns results wins):
      1. Exact section_id (e.g. "2000-Block-2027SP")
      2. Exact course number (e.g. "2000")
      3. All tokens appear in the title
      4. All tokens appear in title + faculty + course# + term
      5. (only if include_subjects=True) All tokens match including subject_areas

    Subject areas are excluded by default because they're long
    semicolon-separated lists that cause too many false positives for
    disambiguation. /coursesearch opts in by passing include_subjects=True.
    """
    q = _clean(query)
    if not q:
        return []

    if q.upper() in BY_SECTION_ID:
        return [BY_SECTION_ID[q.upper()]]

    if q in BY_COURSE_NUM:
        return list(BY_COURSE_NUM[q])

    tokens = [t.lower() for t in q.split() if t]
    if not tokens:
        return []

    def _all_in(hay: str) -> bool:
        return all(t in hay for t in tokens)

    # Tier 1: title-only
    title_matches = [c for c in CATALOG if _all_in(c["title"].lower())]
    if title_matches:
        title_matches.sort(key=lambda c: (c["title"], c["term"]))
        return title_matches

    # Tier 2: title + faculty + course# + term
    def core_hay(c: dict) -> str:
        return f"{c['title']} {c['faculty']} {c['course_number']} {c['term']}".lower()

    core_matches = [c for c in CATALOG if _all_in(core_hay(c))]
    if core_matches or not include_subjects:
        core_matches.sort(key=lambda c: (c["title"], c["term"]))
        return core_matches

    # Tier 3 (coursesearch only): include subject areas
    def broad_hay(c: dict) -> str:
        return f"{c['title']} {c['faculty']} {c['course_number']} {c['term']} {c['subject_areas']}".lower()

    broad_matches = [c for c in CATALOG if _all_in(broad_hay(c))]
    broad_matches.sort(key=lambda c: (c["title"], c["term"]))
    return broad_matches


# ---- Formatting helpers ----------------------------------------------------


def _primary_lastname(faculty: str) -> str:
    names = _faculty_lastnames(faculty)
    return names[0].title() if names else ""


def _fmt_credits(c: float) -> str:
    return str(int(c)) if c == int(c) else f"{c:g}"


def format_section(c: dict) -> str:
    """Two-line course summary used everywhere (detailed view and pickers)."""
    return (
        f"*{c['title']}* — {_primary_lastname(c['faculty'])}\n"
        f"`{c['section_id']}` • #{c['course_number']} • {c['term']} • Cr: {_fmt_credits(c['credits'])}"
    )


# Pickers used to use a one-line variant; now identical to format_section.
format_section_short = format_section


def picker_blocks(query: str, matches: list[dict], action_id: str) -> list[dict]:
    """Render a list of matching sections with a button for each. Used for
    /enroll and /classmates disambiguation."""
    blocks = [{
        "type": "section",
        "text": {"type": "mrkdwn", "text":
                 f"*{len(matches)} sections match* `{query}`. Pick one:"}
    }, {"type": "divider"}]

    for c in matches[:5]:
        label = {
            "enroll_pick": "Enroll",
            "classmates_pick": "See classmates",
        }.get(action_id, "Select")
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": format_section_short(c)},
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": label},
                "action_id": action_id,
                "value": c["section_id"],
            },
        })
    if len(matches) > 5:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn",
                          "text": f"_…and {len(matches) - 5} more. Add another keyword (e.g. a faculty last name) to narrow._"}],
        })
    return blocks


def classmates_blocks(section: dict, viewer_id: str, rows: list[tuple]) -> list[dict]:
    """Render the classmates list + a Share-to-channel button."""
    others = [r[0] for r in rows if r[0] != viewer_id]
    you_enrolled = any(r[0] == viewer_id for r in rows)

    lines = []
    if you_enrolled:
        lines.append("• You")
    lines.extend(f"• <@{uid}>" for uid in others)
    body = "\n".join(lines) if lines else "_No one else yet._"

    return [
        {"type": "header",
         "text": {"type": "plain_text", "text": f"👥 {section['title']}"}},
        {"type": "context", "elements": [
            {"type": "mrkdwn",
             "text": f"`{section['section_id']}` • {section['faculty']} • "
                     f"{section['term']} • {len(rows)} enrolled"}
        ]},
        {"type": "section", "text": {"type": "mrkdwn", "text": body}},
        {"type": "actions", "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": "📣 Share to this channel"},
            "action_id": "share_classmates",
            "value": section["section_id"],
        }]},
    ]


# ---- Enroll / resolve helpers shared by commands & actions -----------------


def do_enroll(user_id: str, section_id: str) -> tuple[bool, str]:
    """Returns (newly_enrolled, message)."""
    section = BY_SECTION_ID.get(section_id.upper())
    if not section:
        return False, f"Unknown section id `{section_id}`."

    with get_db() as conn:
        existing = conn.execute(
            "SELECT 1 FROM enrollments WHERE user_id = ? AND section_id = ?",
            (user_id, section["section_id"]),
        ).fetchone()
        if existing:
            return False, f"You're already enrolled in `{section['section_id']}`."
        conn.execute(
            "INSERT INTO enrollments VALUES (?, ?)",
            (user_id, section["section_id"]),
        )
    return True, f"✅ Enrolled in:\n{format_section(section)}"


def do_unenroll(user_id: str, section_id: str) -> tuple[bool, str]:
    section = BY_SECTION_ID.get(section_id.upper())
    if not section:
        return False, f"Unknown section id `{section_id}`."
    with get_db() as conn:
        existing = conn.execute(
            "SELECT 1 FROM enrollments WHERE user_id = ? AND section_id = ?",
            (user_id, section["section_id"]),
        ).fetchone()
        if not existing:
            return False, f"You weren't enrolled in `{section['section_id']}`."
        conn.execute(
            "DELETE FROM enrollments WHERE user_id = ? AND section_id = ?",
            (user_id, section["section_id"]),
        )
    return True, f"Removed from *{section['title']}* (`{section['section_id']}`)."


def fetch_classmates(section_id: str) -> list[tuple]:
    with get_db() as conn:
        return conn.execute(
            "SELECT user_id FROM enrollments WHERE section_id = ? ORDER BY user_id",
            (section_id,),
        ).fetchall()


def fetch_user_sections(user_id: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT section_id FROM enrollments WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    sections = [BY_SECTION_ID.get(r[0].upper()) for r in rows]
    sections = [s for s in sections if s]
    sections.sort(key=lambda c: (c["term"], c["title"]))
    return sections


# ---- Slash Commands --------------------------------------------------------


@app.command("/enroll")
def enroll(ack, command, respond):
    ack()
    query = command["text"].strip()
    if not query:
        ephemeral(respond, "Usage: `/enroll <course number | section id | name | faculty>`")
        return

    matches = find_sections(query)
    if not matches:
        ephemeral(respond, f"No courses found matching `{query}`. Try `/coursesearch <keyword>`.")
        return

    if len(matches) > 1:
        ephemeral(respond, blocks=picker_blocks(query, matches, "enroll_pick"))
        return

    _, msg = do_enroll(command["user_id"], matches[0]["section_id"])
    ephemeral(respond, msg)


@app.action("enroll_pick")
def enroll_pick(ack, body, respond):
    ack()
    section_id = body["actions"][0]["value"]
    user_id = body["user"]["id"]
    _, msg = do_enroll(user_id, section_id)
    respond(response_type="ephemeral", replace_original=True, text=msg)


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
        ephemeral(respond, blocks=picker_blocks(query, matches, "unenroll_pick"))
        return

    _, msg = do_unenroll(command["user_id"], matches[0]["section_id"])
    ephemeral(respond, msg)


@app.action("unenroll_pick")
def unenroll_pick(ack, body, respond, client):
    ack()
    section_id = body["actions"][0]["value"]
    user_id = body["user"]["id"]
    _, msg = do_unenroll(user_id, section_id)
    respond(response_type="ephemeral", replace_original=True, text=msg)
    # If triggered from App Home, refresh
    if body.get("container", {}).get("type") == "view":
        publish_home(user_id, client)


@app.command("/myclasses")
def my_classes(ack, command, respond):
    ack()
    sections = fetch_user_sections(command["user_id"])
    if not sections:
        ephemeral(respond, "You're not enrolled in any classes. Use `/enroll <course>` to add one.")
        return
    blocks = [format_section(s) for s in sections]
    ephemeral(respond, "*Your classes:*\n\n" + "\n\n".join(blocks))


@app.command("/classmates")
def classmates_cmd(ack, command, respond):
    ack()
    query = command["text"].strip()
    if not query:
        ephemeral(respond, "Usage: `/classmates <course number | section id | name>`")
        return

    matches = find_sections(query)
    if not matches:
        ephemeral(respond, f"No courses found matching `{query}`.")
        return
    if len(matches) > 1:
        ephemeral(respond, blocks=picker_blocks(query, matches, "classmates_pick"))
        return

    section = matches[0]
    rows = fetch_classmates(section["section_id"])
    if not rows:
        ephemeral(respond,
                  f"No one is enrolled in *{section['title']}* (`{section['section_id']}`) yet.")
        return
    ephemeral(respond, blocks=classmates_blocks(section, command["user_id"], rows))


@app.action("classmates_pick")
def classmates_pick(ack, body, respond):
    ack()
    section_id = body["actions"][0]["value"]
    section = BY_SECTION_ID.get(section_id.upper())
    if not section:
        respond(response_type="ephemeral", replace_original=True,
                text=f"Unknown section `{section_id}`.")
        return
    rows = fetch_classmates(section["section_id"])
    if not rows:
        respond(response_type="ephemeral", replace_original=True,
                text=f"No one is enrolled in *{section['title']}* yet.")
        return
    respond(response_type="ephemeral", replace_original=True,
            blocks=classmates_blocks(section, body["user"]["id"], rows), text=" ")


@app.action("share_classmates")
def share_classmates(ack, body, client, respond):
    ack()
    section_id = body["actions"][0]["value"]
    section = BY_SECTION_ID.get(section_id.upper())
    if not section:
        return
    rows = fetch_classmates(section["section_id"])
    mentions = " ".join(f"<@{r[0]}>" for r in rows) or "_(no one yet)_"
    channel_id = body.get("channel", {}).get("id")
    if not channel_id:
        respond(response_type="ephemeral", replace_original=False,
                text="Can't share here — run `/classmates` from inside a channel.")
        return
    try:
        client.chat_postMessage(
            channel=channel_id,
            text=f"Students in {section['title']}",
            blocks=[
                {"type": "header",
                 "text": {"type": "plain_text", "text": f"👥 {section['title']}"}},
                {"type": "context", "elements": [{"type": "mrkdwn",
                    "text": f"`{section['section_id']}` • {section['faculty']} • "
                            f"{section['term']} • {len(rows)} enrolled"}]},
                {"type": "section", "text": {"type": "mrkdwn", "text": mentions}},
                {"type": "context", "elements": [{"type": "mrkdwn",
                    "text": f"_Shared by <@{body['user']['id']}> via HLS Class Finder_"}]},
            ],
        )
        respond(response_type="ephemeral", replace_original=False,
                text="✅ Shared to channel.")
    except Exception as e:
        respond(response_type="ephemeral", replace_original=False,
                text=f"Couldn't post to channel: `{e}`. "
                     "Make sure the bot is invited to this channel.")


@app.command("/coursesearch")
def course_search(ack, command, respond):
    ack()
    query = command["text"].strip()
    if not query:
        ephemeral(respond, "Usage: `/coursesearch <keyword>`")
        return

    matches = find_sections(query, include_subjects=True)
    if not matches:
        ephemeral(respond, f"No courses found matching `{query}`.")
        return

    shown = matches[:5]
    blocks = [{"type": "section", "text": {"type": "mrkdwn",
        "text": f"*Found {len(matches)} course(s) matching* `{query}`:"}}]
    for c in shown:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": format_section_short(c)},
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": "Enroll"},
                "action_id": "enroll_pick",
                "value": c["section_id"],
            },
        })
    if len(matches) > 5:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
            "text": f"_…and {len(matches) - 5} more. Add another keyword to narrow._"}]})
    ephemeral(respond, blocks=blocks)


@app.command("/classhelp")
def class_help(ack, command, respond):
    ack()
    is_admin = command["user_id"] in ADMIN_USER_IDS
    lines = [
        "*📚 HLS Class Finder — commands*",
        "",
        "• `/enroll <course# | section id | name | faculty>` — add yourself to a class",
        "• `/unenroll <course# | section id>` — remove yourself",
        "• `/myclasses` — list your classes",
        "• `/classmates <course# | section id>` — see who else is in a class (with a button to share the roster to a channel)",
        "• `/coursesearch <keyword>` — browse the catalog",
        "• `/classhelp` — show this message",
    ]
    if is_admin:
        lines.append("• `/popular` — _(admin)_ top 5 most-enrolled classes")
    lines += [
        "",
        "_Tip:_ You can search by faculty name too — `/enroll block admin` narrows "
        "`admin law` to Prof. Block's section. Visit the bot's *Home* tab for a "
        "visual dashboard.",
    ]
    ephemeral(respond, "\n".join(lines))


@app.command("/popular")
def popular(ack, command, respond):
    ack()
    if command["user_id"] not in ADMIN_USER_IDS:
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

    ephemeral(respond, "*🏆 Top 5 most popular classes:*\n" + "\n".join(lines))


# ---- App Home --------------------------------------------------------------


def publish_home(user_id: str, client):
    """(Re)publish the App Home view for a user."""
    sections = fetch_user_sections(user_id)

    blocks: list[dict] = [
        {"type": "header",
         "text": {"type": "plain_text", "text": "📚 HLS Class Finder"}},
        {"type": "context", "elements": [{"type": "mrkdwn",
            "text": "Register your classes. Find classmates. "
                    "Use `/classhelp` anywhere in Slack to see commands."}]},
        {"type": "divider"},
        {"type": "header",
         "text": {"type": "plain_text", "text": "Your classes"}},
    ]

    if not sections:
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": "_You haven't enrolled in any classes yet._"}})
    else:
        for s in sections:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": format_section(s)},
                "accessory": {
                    "type": "button",
                    "style": "danger",
                    "text": {"type": "plain_text", "text": "Unenroll"},
                    "action_id": "unenroll_pick",
                    "value": s["section_id"],
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Unenroll?"},
                        "text": {"type": "mrkdwn",
                                 "text": f"Remove yourself from *{s['title']}*?"},
                        "confirm": {"type": "plain_text", "text": "Unenroll"},
                        "deny": {"type": "plain_text", "text": "Cancel"},
                    },
                },
            })

    blocks += [
        {"type": "divider"},
        {"type": "header",
         "text": {"type": "plain_text", "text": "Quick actions"}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": "Use these commands anywhere in Slack:\n"
                    "• `/enroll <course>` — join a class\n"
                    "• `/coursesearch <keyword>` — browse the catalog\n"
                    "• `/classmates <course>` — see who's in a class\n"
                    "• `/classhelp` — full command list"}},
    ]

    try:
        client.views_publish(user_id=user_id, view={"type": "home", "blocks": blocks})
    except Exception as e:
        print(f"views_publish failed for {user_id}: {e}")


@app.event("app_home_opened")
def on_home_opened(event, client):
    if event.get("tab") != "home":
        return
    publish_home(event["user"], client)


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
