# Command — Owner-Enrichment Agent + Dedicated Google Sending + Brand-safe Cold Email

> Goal: personalize cold outreach with the **owner's name** (and socials),
> send it from a **dedicated, reputation-safe Google identity**, using
> **professional, good-manner templates** that protect the Schild Inc brand.
>
> Status: Part 3 (templates) ✅ built. Parts 1–2 specced below, pending one decision each.

---

## Part 1 — Owner-Enrichment Agent (find the decision-maker)

**Why:** a cold email that opens "Hi Jan," (the owner) instead of "Hi there,"
gets far higher reply rates and reads like a human, not a blast — which is
exactly what protects the brand. The email engine is already wired: it renders
`{{greeting_name}}` = owner first name → company name → "there". We just need to
**fill the owner name** on KVK/contact records.

### How to get owner info — be clear-eyed about risk
| Method | Verdict | Notes |
|---|---|---|
| **Authenticated LinkedIn / Instagram scraping** | ❌ **Not recommended** | Against both platforms' ToS. Scraping from Schild Inc's own accounts (or at volume on residential IP) risks **permanent account bans** and **GDPR exposure** (scraping personal data on EU citizens). This is the *opposite* of protecting brand reputation. |
| **Public Google-snippet extraction** (RECOMMENDED, free) | ✅ | Extend the existing residential-IP agent (`scripts/email_agent.py`) to read **public Google result snippets** — which routinely surface "Jan de Vries – Owner at Velo Amsterdam \| LinkedIn", IG bio lines, and About/Team pages. No login to LinkedIn/IG, no ToS breach — same technique the agent already uses for emails. |
| **Compliant enrichment API** (paid) | ✅ best quality | Dropcontact (EU/GDPR-first), Kaspr, Hunter, Apollo. ~€0.02–0.10/lookup. Run on targeted batches to stay inside the <€50/mo budget. |

### Build (recommended free path)
- **Data:** reuse `Contact.contact_person` / `KvkCompany` — add `owner_name`,
  `owner_role`, `owner_source`, `owner_status` (pending/found/none) to
  `kvk_companies` (small migration 0018). Already-present `instagram_url` /
  `linkedin_url` columns get filled too.
- **Agent endpoints** (mirror the existing `/api/kvk/agent/*` pattern):
  - `GET /api/enrich/owner/pending` — records with `owner_status='pending'`,
    has website/socials, prioritized least-attempted first.
  - `POST /api/enrich/owner/result` — agent posts `{kvk_id, owner_name,
    owner_role, linkedin_url, instagram_url, source_url}`; always increments attempts.
- **Local agent** (`scripts/owner_agent.py` or extend `email_agent.py`):
  hands-free, residential IP, **read-only public search**, CAPTCHA → skip,
  `--dry-run` (print, write nothing), `--max N`, `--debug` (explain misses).
- **Write-back:** found owner → `KvkCompany.owner_name` + `Contact.contact_person`
  (via the contact backfill link), so campaigns auto-personalize.
- **Test:** `python scripts/owner_agent.py --dry-run --max 20 --debug`.

> Personalization is already live — the moment `contact_person` is filled, every
> template greets the owner by name with a graceful fallback when unknown.

---

## Part 2 — Dedicated Google Sending Account (protect reputation)

**Why:** sending cold mail from the owner's everyday mailbox stakes *that*
mailbox's reputation on cold outreach. A dedicated identity isolates the risk so
a bad week never touches your primary inbox or domain.

| Option | Verdict | Notes |
|---|---|---|
| **Dedicated Workspace mailbox on a send-subdomain** (RECOMMENDED) | ✅ best | e.g. `hello@send.schildinc.com` or a separate domain `schild-bikes.com`. ~€6/user/mo (within budget). Full **SPF + DKIM + DMARC** control, 2,000/day cap, keeps the primary domain's reputation ring-fenced. |
| **New free Gmail + verified send-as alias** | ✅ works, weaker | Plugs into the engine today (set `GMAIL_SEND_AS`), but consumer Gmail can't sign DKIM for your domain and caps ~500/day — fine to start, weaker for brand/deliverability. |
| **Keep current account** | ⚠️ | Simplest, but cold volume rides on your main reputation. |

### Deliverability checklist — *this is what actually protects the brand*
1. **SPF, DKIM, DMARC** on the sending domain (Workspace makes this easy).
2. **Warm-up ramp** — already built: `GMAIL_DAILY_LIMIT` 80 → 150 → 250 → 400 weekly.
3. **List hygiene** — verified emails only (MX-validate via existing
   `email_guesser`), honor suppression + one-click unsubscribe (already enforced).
4. **Modest pace** — 8s spacing + gradual daemon (already).
5. **Compliant footer** — real legal name + **physical address** + opt-out +
   monitored reply-to (now in every template; set the env vars below).
6. **Custom tracking domain** (optional enhancement) — serve open/click links
   from `schildinc.com` instead of the Railway host for cleaner, on-brand URLs.
7. **Monitor** — enable **Google Postmaster Tools** on the sending domain.

### Plug-in (already supported)
Set `GMAIL_SEND_AS` to the dedicated alias and connect that Google account at
`/emails`. (Multi-account rotation = a future enhancement: a `sending_accounts`
table the daemon round-robins.)

---

## Part 3 — Brand-safe cold email standards ✅ BUILT

The 5 starter templates were rewritten (seed v2 — auto-re-seed on deploy):
- **Personalized greeting** `{{greeting_name}}` (owner first name → company → "there") — never "Hi ,".
- **Consultative, low-pressure** copy; **one soft CTA**; short and scannable.
- **Real signature** — name + title + company + website.
- **Compliant footer** — legal name + **physical address** + **one-click
  unsubscribe** + monitored **reply-to** (CAN-SPAM / GDPR).
- **Lean HTML, few links** → better inbox placement.

Set these in Railway so the footer is accurate:
```
COMPANY_LEGAL_NAME=Schild Inc B.V.
COMPANY_ADDRESS=Schild Inc, <street>, <postcode> <city>, Netherlands
COMPANY_WEBSITE=https://schildinc.com
COMPANY_PHONE=+31 ...
SENDER_TITLE=Sales
GMAIL_SENDER_NAME=<real person name>   # a human name out-performs a brand name
```

---

## Decisions needed
1. **Owner enrichment** — Google-snippet agent (free, safe) · compliant paid API · (not recommended) direct LinkedIn/IG scraping.
2. **Sending account** — dedicated Workspace subdomain (recommended) · new free Gmail + alias · keep current.
