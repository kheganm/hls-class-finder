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

## Cloud Deploy (Turso + Render)

Run the bot 24/7 without your laptop.

### 1. Create a Turso database

1. Install the Turso CLI (or use the web dashboard at [turso.tech](https://turso.tech)).
2. Create a database:
   ```bash
   turso db create hls-class-finder
   ```
3. Get its URL:
   ```bash
   turso db show hls-class-finder --url
   # libsql://hls-class-finder-<org>.turso.io
   ```
4. Create an auth token:
   ```bash
   turso db tokens create hls-class-finder
   ```

Save both values — you'll paste them into Render.

### 2. Deploy to Render

1. Log in to [render.com](https://render.com) and click **New +** → **Blueprint**.
2. Connect your GitHub and select `kheganm/hls-class-finder`. Render will detect `render.yaml` and create a **Background Worker** named `hls-class-finder`.
3. When prompted, fill in these five environment variables:
   - `SLACK_BOT_TOKEN` — `xoxb-…`
   - `SLACK_APP_TOKEN` — `xapp-…`
   - `SLACK_ADMIN_USER_IDS` — comma-separated Slack user IDs
   - `TURSO_DATABASE_URL` — `libsql://…turso.io`
   - `TURSO_AUTH_TOKEN` — the token you created
4. Click **Apply**. Render builds and starts the worker. Check the logs — you should see `⚡️ Bolt app is running!`.

Every `git push` to `main` will redeploy automatically.

> **Note on Render free tier:** Background Workers on the free plan sleep after inactivity. Because this bot uses Socket Mode (a persistent websocket), Slack keeps the connection active — but if the worker does sleep, Slack commands will fail until it wakes. If you need hard uptime guarantees, upgrade to the Starter plan (~$7/mo).

## Updating the Catalog

`courses.json` is the parsed catalog. To regenerate it from a new markdown dump:

```bash
python parse_catalog.py path/to/HLS_Course_Catalog.md
```

## Files

- `app.py` — Slack app with all slash commands
- `db.py` — database layer (Turso in prod, local SQLite for dev)
- `parse_catalog.py` — one-shot parser: catalog markdown → `courses.json`
- `courses.json` — 395 course sections (committed; regenerate as needed)
- `render.yaml` — Render Blueprint (Background Worker config)
- `enrollments.db` — local SQLite fallback (gitignored)
