# Schild Inc CRM — Complete Setup & Operating Guide

Follow these in order. Steps 1–6 get you fully live; 7–8 are optional channels.
Each step says **where** to do it: 🖥️ your Mac · ☁️ Railway · 🔵 Google · 🟢 Meta · 🌐 DNS.

---

## 0. Prerequisites (🖥️ your Mac) — one time
```bash
cd "/Users/kevinolla/AI Project/B2B Prospect tool"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

---

## 1. Install the always-on local agents (🖥️ your Mac)
These run on your laptop (residential IP) and feed the production DB:
- **kvk-agent** — finds emails/phones/socials (always-on)
- **owner-agent** — finds owner names for personalization (login + every 6h)

```bash
bash scripts/install-agent-daemon.sh
```
Verify + watch logs:
```bash
launchctl list | grep schildinc
tail -f ~/Library/Logs/schild-owner-agent.log
```
**Try the owner agent once by hand first (safe, writes nothing):**
```bash
source .venv/bin/activate
python scripts/owner_agent.py --dry-run --max 20 --debug
```
Happy with the names it finds? It's already scheduled — or run live once now:
```bash
python scripts/owner_agent.py --max 100
```

---

## 2. Create a dedicated sending mailbox (🔵 Google Workspace)
Sending cold mail from a dedicated identity protects your main inbox's reputation.
1. In Google Workspace Admin, create a mailbox on a **send-subdomain**, e.g.
   `hello@send.schildinc.com` (or a separate domain). ~€6/user/mo.
2. (Optional) Add `sales@schildinc.com` as a **"Send mail as"** alias on it and verify it
   (Gmail → Settings → Accounts and Import → Send mail as).

> Free alternative: a new dedicated Gmail + verified `sales@schildinc.com` alias.
> Works, but no DKIM for your domain and ~500/day cap.

---

## 3. Authenticate the sending domain (🌐 DNS) — protects deliverability
Add these DNS records for the sending domain (Workspace gives exact values):
- **SPF**: `v=spf1 include:_spf.google.com ~all`
- **DKIM**: enable in Workspace Admin → Apps → Gmail → Authenticate email; paste the TXT record it gives you.
- **DMARC**: TXT `_dmarc` → `v=DMARC1; p=none; rua=mailto:dmarc@schildinc.com` (start with `p=none`, tighten later).
- Turn on **Google Postmaster Tools** for the domain to watch reputation.

---

## 4. Create the Gmail OAuth client (🔵 Google Cloud Console)
1. console.cloud.google.com → create/select a project → **Enable the Gmail API**.
2. APIs & Services → Credentials → **Create credentials → OAuth client ID → Web application**.
3. Authorized redirect URI (exactly):
   `https://schild-prospect-engine-production.up.railway.app/emails/gmail/callback`
4. Copy the **Client ID** + **Client secret**.

---

## 5. Set environment variables (☁️ Railway → service → Variables)
```
# Gmail sending
GMAIL_CLIENT_ID=<from step 4>
GMAIL_CLIENT_SECRET=<from step 4>
GMAIL_SEND_AS=hello@send.schildinc.com      # the dedicated mailbox/alias
GMAIL_SENDER_NAME=Kevin Olla                # a REAL human name out-performs a brand
GMAIL_DAILY_LIMIT=80                         # warm-up start (raise weekly)

# Brand-safe footer (legal requirement + reputation)
COMPANY_LEGAL_NAME=Schild Inc B.V.
COMPANY_ADDRESS=Schild Inc, <street>, <postcode> <city>, Netherlands
COMPANY_WEBSITE=https://schildinc.com
COMPANY_PHONE=+31 ...
SENDER_TITLE=Sales

# Sessions (roles)
SESSION_SECRET=<paste a long random string>
```
Railway redeploys on variable change. Then visit
`/emails` → **Connect Gmail** and authorize the dedicated account
(grant the read scope too, so inbound replies thread into `/inbox`).

---

## 6. Populate contacts + send your first campaign
1. **Build the contact hub:** open `/contacts` → **Build / refresh contacts**
   (merges customers + KVK + leads + prospects; safe to re-run).
2. **Let the agents run** a while so owner names + emails fill in.
3. **Create a campaign:** `/emails` → **New Campaign** → pick *Cold — Initial
   Outreach* → audience *KVK companies* (filter tier/country) → review → **send a
   test to yourself** → **Send**. It sends gradually (80/day, 8s apart).
4. **Watch results** on the campaign page + `/reports`. Replies land in `/inbox`.

**Warm-up ramp** (raise `GMAIL_DAILY_LIMIT` weekly): 80 → 150 → 250 → 400.

---

## 7. (Optional) WhatsApp (🟢 Meta) — <€50/mo, when ready
1. Meta Business → WhatsApp → add a number → get **phone number ID** + a
   **permanent access token**; note your **app secret**.
2. ☁️ Railway vars: `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`,
   `WHATSAPP_VERIFY_TOKEN` (pick a random string), `WHATSAPP_APP_SECRET`,
   `WHATSAPP_BUSINESS_ACCOUNT_ID`.
3. Meta → Webhooks → callback `https://schild-prospect-engine-production.up.railway.app/webhooks/whatsapp`
   with your verify token; subscribe to **messages**.
4. Register your approved template names in `/inbox/settings`. WhatsApp now
   sends/receives inside `/inbox`.

---

## 8. (Optional) Add teammates with roles
`/inbox/settings` → add teammate (email + role + password). They sign in at
`/login`. Admins can delete campaigns, manage teammates, and see `/audit`;
agents are limited. The owner (HTTP Basic) is always admin.

---

## Daily operating routine
- **Agents** run themselves (laptop on + logged in). Check logs occasionally.
- **`/inbox`** — reply to leads (live unread badge in the nav).
- **`/emails`** — monitor campaign open/click; create follow-ups.
- **`/reports`** — weekly: open/click rates, replies per agent, response time.
- **Deliverability** — watch Postmaster Tools; keep volume gradual; never buy lists.

---

## Deploying code changes (🖥️ → ☁️)
```bash
cd "/Users/kevinolla/AI Project/B2B Prospect tool"
railway up --service schild-prospect-engine    # runs alembic migrations on boot
```

## Quick health checks
```bash
curl -s -u "schild:Schildinc#01" https://schild-prospect-engine-production.up.railway.app/api/me
curl -s -u "schild:Schildinc#01" https://schild-prospect-engine-production.up.railway.app/api/enrich/owner/pending?limit=1
```
