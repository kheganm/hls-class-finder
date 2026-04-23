# HLS Class Finder Slack App

A Slack app that lets Harvard Law students register their 2026–2027 classes and find classmates. Backed by the official HLS course catalog (395 sections).

## Commands

| Command | Description |
|---|---|
| `/enroll <course# or section id or name>` | Add yourself to a class. Disambiguates multisection courses. |
| `/unenroll <course# or section id>` | Remove yourself from a class |
| `/myclasses` | List all your classes with full details |
| `/classmates <course# or section id>` | See who else is in a class |
| `/coursesearch <keyword>` | Search the catalog by title, faculty, or subject area |

### Section IDs

Because some courses have multiple sections (e.g., Administrative Law with Block vs. Vermeule), each section has a unique id formatted as `<course#>-<faculty lastname>-<term>`. Examples:

- `2000-Block-2027SP` — Admin Law with Block, Spring 2027
- `2000-Vermeule-2027SP` — Admin Law with Vermeule, Spring 2027

If you run `/enroll 2000`, the bot will list both sections and ask you to pick one.

## Setup

### 1. Create a Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Enable **Socket Mode**, generate an **App-Level Token** with `connections:write` → this is your `SLACK_APP_TOKEN`
3. Under **OAuth & Permissions**, add Bot Token Scopes:
   - `commands`
   - `chat:write`
4. Under **Slash Commands**, create: `/enroll`, `/unenroll`, `/myclasses`, `/classmates`, `/coursesearch`
5. Install to workspace → copy the **Bot User OAuth Token** → this is your `SLACK_BOT_TOKEN`

### 2. Run

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in your tokens
```

Windows PowerShell:
```powershell
$env:SLACK_BOT_TOKEN="xoxb-..."
$env:SLACK_APP_TOKEN="xapp-..."
python app.py
```

Socket Mode means no public URL is needed.

## Updating the Catalog

`courses.json` is the parsed catalog. To regenerate it from a new markdown dump:

```bash
python parse_catalog.py path/to/HLS_Course_Catalog.md
```

## Files

- `app.py` — Slack app with all slash commands
- `parse_catalog.py` — one-shot parser: catalog markdown → `courses.json`
- `courses.json` — 395 course sections (committed; regenerate as needed)
- `enrollments.db` — SQLite DB of student enrollments (gitignored)
