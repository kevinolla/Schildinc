# B2B Prospect tool — Windows setup

Continuing on Windows after the Mac transfer. The `.venv/` folder in this
repo was built on macOS and will NOT run on Windows — delete it and rebuild.

## Prerequisites

- Python 3.12 (installed via `winget install Python.Python.3.12`)
- Git for Windows (already installed)
- Google Chrome (for the local Playwright agent)

Verify:
```powershell
python --version   # expects 3.12.x
git --version
```

## First-time setup

Open PowerShell in this folder (`AI Project\B2B Prospect tool`):

```powershell
# 1. Remove the Mac venv (if it's still on disk)
Remove-Item -Recurse -Force .venv -ErrorAction SilentlyContinue

# 2. Create a fresh Windows venv
python -m venv .venv

# 3. Activate it (this shell only)
.venv\Scripts\Activate.ps1

# 4. Install Python deps
pip install -r requirements.txt

# 5. Install Playwright's Chromium browser
python -m playwright install chromium

# 6. Create your local .env
Copy-Item .env.example .env
# then edit .env and fill in the ~110 vars (DB, Gmail OAuth, Meta, Stripe, etc.)

# 7. Run migrations against the local SQLite DB (schildinc.db)
alembic upgrade head
```

If PowerShell blocks the activation script:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## Running the app

```powershell
.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000
```

Then open http://localhost:8000/ (basic auth: `ADMIN_USERNAME` / `ADMIN_PASSWORD`
from `.env`).

## Running the local KVK agent

```powershell
.venv\Scripts\Activate.ps1
python scripts\email_agent.py                       # default
python scripts\email_agent.py --headless --max 100  # quick test
```

## Windows-specific notes

- `scripts/install-agent-daemon.sh` + `com.schildinc.*.plist` are **macOS launchd only**.
  Windows equivalent: use **Task Scheduler**. Create a task that runs
  `.venv\Scripts\python.exe scripts\email_agent.py` at logon or on a schedule.
- The docstring in `scripts/email_agent.py` now shows both Mac and Windows usage.
- `CLAUDE.md` (project memory) still references Mac paths in its examples —
  functional but worth updating as you touch them.
- Playwright driver on Windows lives at `.venv\Lib\site-packages\playwright\driver\`
  — the 113 MB Node binary is normal and gitignored via the venv exclusion.

## Deploy to Railway

Install Railway CLI on Windows:
```powershell
npm i -g @railway/cli
railway login
railway up --service schild-prospect-engine
```
