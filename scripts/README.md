# Local Browser Email Agent

A Python script that runs on **your laptop** (not Railway) to find emails
for KVK companies that the cloud crawler couldn't resolve.

## Why it exists

Search engines (Google, Bing) serve rich snippets — including emails right
in the meta-description — to **residential IPs** but only stub HTML to
**cloud datacenter IPs**. The Railway crawler hits the second case. This
script runs in the first.

## How it works

1. Polls `https://schild-prospect-engine-production.up.railway.app/api/kvk/agent/pending`
   for the next batch of records still missing `email_public`
2. For each one, opens Google in a real Chrome window (your laptop, your IP)
   and searches `"Company Name" City email`
3. Extracts email addresses from the rendered page, filters out noise,
   ranks by domain match
4. POSTs the best email back to `…/api/kvk/agent/result` — saved with
   `source=browser_agent`, `confidence=high`

## Setup (one time)

You already have everything installed if you've been running the project.
If not:

```bash
cd "/Users/kevinolla/AI Project/B2B Prospect tool"
source .venv/bin/activate
pip install playwright
playwright install chromium
```

## Run it

```bash
cd "/Users/kevinolla/AI Project/B2B Prospect tool"
source .venv/bin/activate
python scripts/email_agent.py
```

A Chrome window will pop up and start cycling through searches. You'll see
output like:

```
=== Local Browser Email Agent ===
  API: https://schild-prospect-engine-production.up.railway.app
  Batch: 25, Delay: 2.0s, Headless: False

--- Got 25 pending records ---
  [1] #4 Rijwielhandel Jan Jonkman (Drachten) … ✓ info@janjonkman.nl
  [2] #7 E. Gielliet (Moarre) … ✗ not found
  [3] #9 Bicycle Solutions (Amersfoort) … ✓ info@bicyclesolutions.nl
  ...
```

Every `✓` line is a new email saved to your live Railway database.
Refresh `/kvk` in the browser — it'll show up.

## Options

| Flag | Default | Purpose |
|---|---|---|
| `--batch 25` | 25 | Records per fetch from API |
| `--headless` | off | Hide the browser window (still works, just no UI) |
| `--quiet` | off | Less per-record log output |
| `--max 100` | 0 (forever) | Stop after N records |
| `--delay 2.0` | 2.0 | Seconds between Google searches (don't lower below 1.5 — Google may rate-limit) |

Examples:

```bash
# Run silently, processing only 50 records
python scripts/email_agent.py --max 50 --quiet

# Run hidden in background, faster pace
python scripts/email_agent.py --headless --delay 1.5
```

## What gets stored

When the agent finds a match it saves to `kvk_companies`:
- `email_public` = the email it found
- `email_source_url` = `"browser_agent"`
- `email_confidence` = `"high"`
- `enrichment_status` = `"discovered"`
- `website` + `website_domain` = derived from the email domain (if blank before)

When it doesn't find a match it sets `enrichment_status = "no_contacts"`
so the same record won't come back in the next `pending` batch.

## Stopping

Press `Ctrl-C` any time. Every result is committed to the database as
soon as it's found, so nothing is lost.

## Realistic expectations

- **Google snippet hit rate**: ~50-70% of records will have a usable
  email visible in the snippet
- **Speed**: ~25-30 records/minute (limited by Google rate-limiting more
  than search latency)
- **Quality**: emails that match are very accurate — they appear in
  the live snippet exactly as Google indexed them
- **Cost**: $0 — uses your residential connection, no API keys

For a 3000-record run figure ~2-3 hours of laptop time. You can go AFK
while it runs; it just keeps cycling.

## If Google starts blocking you

After a few hundred queries Google may show a "I'm not a robot" CAPTCHA.
Two options:

1. Solve it once in the browser window — agent picks back up automatically
2. Restart with a longer `--delay 3.5` to slow the pace
