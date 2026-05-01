# Schild Inc CRM MVP

Railway-ready FastAPI + Postgres MVP for:

- importing the existing Schild customer database from normalized CSVs
- keeping recent Stripe customers synced through webhooks
- importing or searching Google Maps prospects
- matching prospects against existing customers
- reviewing only true non-customers for outreach
- maintaining a controlled daily send queue with suppression and unsubscribe support

## Stack

- FastAPI
- SQLAlchemy
- Alembic
- PostgreSQL
- pandas
- rapidfuzz
- Stripe SDK
- Resend or SMTP abstraction
- Jinja admin UI

## Core workflow

1. Import `normalized_customer_master.csv`
2. Import `normalized_invoice_history.csv`
3. Stripe webhooks keep customers/invoices fresh in near real time
4. Import prospects from Google Maps CSV or live Places API search
5. Matching engine classifies each prospect as:
   - `existing_customer`
   - `possible_match`
   - `new_prospect`
6. Team reviews prospects and approves only true non-customers
7. Daily outreach queue is generated for approved non-customers only
8. Emails always use `Reply-To: sales@schildinc.com`
9. Suppression list, unsubscribe link, logs, and send limits stay in control

## Matching logic

Prospects are checked in this order:

1. exact website domain
2. exact email
3. canonical company name + city + country
4. fuzzy company name match with `rapidfuzz`

This keeps already-known customers out of outreach while still surfacing edge cases for review.

## Project layout

```text
app/
  config.py
  db.py
  models.py
  matching.py
  importers.py
  google_places.py
  stripe_sync.py
  emailing.py
  jobs.py
  main.py
  templates/
  static/
alembic/
scripts/
```

## Local setup

1. Create a virtualenv and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
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
- `/prospects` Google Maps prospects and matching status
- `/queue` daily outreach queue
- `/suppression` suppression and unsubscribe records
- `/logs` email + webhook logs

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

### Option 2: live Google Places search

Set `GOOGLE_PLACES_API_KEY` and use the form on `/prospects`.

The app uses:

```text
POST https://places.googleapis.com/v1/places:searchText
```

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

## Outreach queue

Daily queue items are created only for:

- `review_status = approved`
- `match_status = new_prospect`
- not suppressed
- with an email present

Build the queue from UI or CLI:

```bash
python scripts/build_daily_queue.py --date 2026-05-01 --limit 25
```

## Sending

Supported providers:

- `MAIL_PROVIDER=console`
- `MAIL_PROVIDER=resend`
- `MAIL_PROVIDER=smtp`

All emails use:

```text
Reply-To: sales@schildinc.com
```

Each sent email gets an unsubscribe link tied to the recipient email. Clicking it creates a suppression entry automatically.

## Railway deployment

### Required env vars

```text
DATABASE_URL=postgresql+psycopg://...
ADMIN_USERNAME=schild
ADMIN_PASSWORD=...
REPLY_TO_EMAIL=sales@schildinc.com
MAIL_PROVIDER=console|resend|smtp
MAIL_FROM=noreply@schildinc.com
UNSUBSCRIBE_SECRET=...
DAILY_SEND_LIMIT=25
DEFAULT_QUEUE_SIZE=25
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
APP_BASE_URL=https://your-app.up.railway.app
```

### Deploy flow

1. Create a Railway Postgres database
2. Attach the `DATABASE_URL` to the app service
3. Set the env vars above
4. Deploy the repo
5. Run migrations:

```bash
alembic upgrade head
```

6. Import the seed CSVs once
7. Add Stripe webhook URL
8. Optional: create a Railway cron or scheduled job to run:

```bash
python scripts/build_daily_queue.py
```

## Notes

- The current UI is intentionally simple and admin-focused.
- Matching is deterministic first, fuzzy second, so the review queue stays understandable.
- Queue sending is controlled; nothing auto-sends just because a prospect exists.
- Existing legacy Node files from earlier work are not used by this MVP runtime.
