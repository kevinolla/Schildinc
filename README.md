# Schild Inc CRM MVP

Railway-ready FastAPI + Postgres app for Schild Inc prospecting, customer matching, public contact discovery, and controlled outreach.

## What this version does

- imports the existing Schild customer database from normalized CSVs
- keeps recent Stripe customers synced through webhooks
- imports or searches Google Maps prospects
- restores public website-based contact discovery with Playwright
- finds visible business email, LinkedIn, and Instagram links from prospect websites
- blocks existing customers from outreach using domain, email, canonical geo-name, and fuzzy matching
- scores bike-shop prospects into Schild-specific tiers
- gives a review-safe daily outreach queue with preview, export, suppression, cooldowns, and send limits
- keeps LinkedIn and Instagram as draft-only outreach, not auto-message channels

## Safety rules built in

- only visible public business contact data is collected
- no hidden email scraping
- no auto-filling website contact forms
- no auto-messaging LinkedIn or Instagram
- no sending to existing customers
- no sending to suppressed or unsubscribed emails
- no continuous endless sending
- all email replies go to `sales@schildinc.com`

## Stack

- FastAPI
- SQLAlchemy
- Alembic
- PostgreSQL
- pandas
- rapidfuzz
- Stripe SDK
- Playwright
- Resend or SMTP abstraction
- Jinja admin UI

## Core workflow

1. Import `normalized_customer_master.csv`
2. Import `normalized_invoice_history.csv`
3. Stripe webhooks keep customers and invoices fresh
4. Import prospects from Google Maps CSV or live Places API
5. Run website discovery for visible email and socials
6. Matching engine removes known customers from outreach
7. Bike tiering suggests priority and sales angle
8. Team reviews and approves true non-customers only
9. Build a daily outreach queue
10. Preview, export, or send approved email safely

## Matching logic

Prospects are checked in this order:

1. exact website domain
2. exact email
3. canonical company name + city + country
4. fuzzy company name match with `rapidfuzz`

If a newly discovered email matches an existing customer, outreach approval is removed automatically.

## Discovery logic

Input:

- `company_name`
- `website`
- `country`
- optional `city` or `state`

Flow:

1. visit homepage
2. read visible content only
3. check likely pages:
   - `contact`
   - `about`
   - `about-us`
   - `contact-us`
   - `impressum`
   - `legal`
   - `privacy`
   - `terms`
4. extract visible emails
5. rank and keep the best candidate
6. save source page and confidence
7. save visible LinkedIn and Instagram URLs if present

## Bike shop tiering

Prospects are scored into:

- `Good Tier`
- `Hard to Reach`
- `Mid Tier`
- `Low Tier`
- `Brand Store`
- `Low Fit`

These tiers influence:

- outreach priority
- approval suggestions
- sales angle
- reporting filters
- queue eligibility

## Project layout

```text
app/
  config.py
  db.py
  models.py
  matching.py
  importers.py
  google_places.py
  discovery.py
  tiering.py
  outreach_templates.py
  emailing.py
  jobs.py
  main.py
  templates/
    dashboard.html
    prospects.html
    prospect_detail.html
    queue.html
    queue_preview.html
    customers.html
    suppression.html
    logs.html
  static/
    styles.css
    email/
      schild-bike-logo.png
      metal-labels.png
      bike-accessories.png
alembic/
  versions/
scripts/
  import_seed_data.py
  build_daily_queue.py
  send_daily_queue.py
```

## Local setup

1. Create a virtualenv and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

2. Copy env file:

```bash
cp .env.example .env
```

3. Set a Postgres database in `DATABASE_URL`

4. Run migrations:

```bash
alembic upgrade head
```

5. Import your customer data:

```bash
python scripts/import_seed_data.py \
  --customers /Users/kevinolla/Downloads/normalized_customer_master.csv \
  --invoices /Users/kevinolla/Downloads/normalized_invoice_history.csv
```

6. Start the app:

```bash
uvicorn app.main:app --reload
```

Open:

```text
http://localhost:8000
```

## Admin UI pages

- `/` dashboard
- `/customers` imported + Stripe-synced customer records
- `/prospects` Google Maps prospects, matching, discovery, tiering, and review
- `/prospects/{id}` prospect detail, drafts, and overrides
- `/queue` daily outreach queue with preview/export/send controls
- `/queue/preview` dry run view for next outreach batch
- `/suppression` suppression and unsubscribe records
- `/logs` email, webhook, and discovery activity logs

## Prospect sources

### Option 1: upload a Google Maps prospect CSV

Upload any CSV containing columns like:

- `company_name` or `name`
- `website`
- `email`
- `city`
- `state`
- `country_code`
- `google_maps_url`

Optional import columns also supported:

- `linkedin_url`
- `instagram_url`
- `notes`

### Option 2: live Google Places search

Set `GOOGLE_PLACES_API_KEY` and use the form on `/prospects`.

The app uses:

```text
POST https://places.googleapis.com/v1/places:searchText
```

## How to run email discovery

From the UI:

1. Open `/prospects`
2. Select one or more prospects
3. Click `Discover Emails for Selected Prospects`

Or open a single prospect and click `Discover Email`.

What gets stored:

- `email_discovery_status`
- `email`
- `email_source_page`
- `email_confidence`
- `email_discovered_at`
- `linkedin_url`
- `instagram_url`
- `website_summary`
- `discovery_highlights`

## How to queue outreach

1. Review prospects and approve only real non-customers
2. Open `/queue`
3. Build queue for a chosen date
4. Open preview to inspect the next 20 contacts
5. Export CSV if needed
6. Send ready items manually or from a scheduled job

Queue eligibility rules:

- prospect has email
- prospect is approved for outreach
- prospect is not an existing customer
- prospect is not suppressed
- prospect is outside cooldown window
- campaign is active
- tier and HQ rules allow email outreach

## How to activate daily sending safely

Recommended rollout:

1. keep `MAIL_PROVIDER=console`
2. build queue and inspect `/queue/preview`
3. confirm matching and suppression behavior
4. switch to `resend` or `smtp`
5. keep a conservative `DAILY_SEND_LIMIT`
6. keep a clear `SEND_WINDOW_START` / `SEND_WINDOW_END`
7. schedule `python scripts/send_daily_queue.py`

Example CLI:

```bash
python scripts/build_daily_queue.py --date 2026-05-01 --limit 20
python scripts/send_daily_queue.py --date 2026-05-01 --limit 10
```

## Email template behavior

Default email is a short Dutch bike-shop template with:

- `company_name`
- `website`
- `sender_name`
- optional `custom_use_case`
- optional `proof_line`

The app also generates:

- LinkedIn draft text
- Instagram draft text
- contact-form-safe version
- follow-up email after 5 business days

Social drafts are for manual use only.

## Stripe webhook

Endpoint:

```text
POST /webhooks/stripe
```

Set `STRIPE_WEBHOOK_SECRET` and point Stripe to:

```text
https://your-app.up.railway.app/webhooks/stripe
```

Useful events:

- `customer.created`
- `customer.updated`
- `invoice.created`
- `invoice.paid`
- `invoice.updated`

## Railway deployment

### Required env vars

```text
DATABASE_URL=postgresql+psycopg://...
ADMIN_USERNAME=schild
ADMIN_PASSWORD=...
APP_BASE_URL=https://your-app.up.railway.app
REPLY_TO_EMAIL=sales@schildinc.com
MAIL_PROVIDER=console|resend|smtp
MAIL_FROM=noreply@schildinc.com
SENDER_NAME=Schild Inc Team
UNSUBSCRIBE_SECRET=...
DAILY_SEND_LIMIT=25
DEFAULT_QUEUE_SIZE=25
CAMPAIGN_ACTIVE=true
SEND_WINDOW_START=08:00
SEND_WINDOW_END=17:30
OUTREACH_COOLDOWN_DAYS=14
PLAYWRIGHT_TIMEOUT_MS=12000
```

### Optional env vars

```text
GOOGLE_PLACES_API_KEY=...
STRIPE_API_KEY=...
STRIPE_WEBHOOK_SECRET=...
RESEND_API_KEY=...
SMTP_HOST=...
SMTP_PORT=587
SMTP_USERNAME=...
SMTP_PASSWORD=...
SMTP_USE_TLS=true
PREVIEW_CONTACT_COUNT=20
OFFICIAL_INSTAGRAM_HANDLE=@schildinc
OFFICIAL_LINKEDIN_URL=https://www.linkedin.com/company/schild-inc/
```

### Deploy flow

1. Create a Railway Postgres database
2. Attach `DATABASE_URL` to the app service
3. Set the env vars above
4. Deploy the repo
5. Run migrations automatically on boot
6. Import the seed CSVs once
7. Add Stripe webhook URL
8. Schedule queue build/send jobs if desired

`nixpacks.toml` installs Chromium so Playwright discovery works on Railway.

## Notes

- Existing legacy Node files from earlier work are not used by this MVP runtime.
- The queue is intentionally conservative and review-first.
- Social outreach is draft-only because the business rules explicitly avoid auto-messaging those platforms.
