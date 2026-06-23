All facts confirmed. `mail_provider`, `resend_api_key`, `smtp_*`, `reply_to_email` already exist (the email_current map's note about `emailing.py` having a provider stub is accurate). Now I have everything verified. Here is the redesign document.

---

# Schild Prospect Engine — Non-Google Discovery + Provider-Abstracted Email: Redesign Spec

**Author:** Senior architect
**Date:** 2026-06-23
**Current migration HEAD (verified):** `0020_attachments_notifications` (CLAUDE.md is stale at 0017)
**Stack (verified):** FastAPI 0.115 / SQLAlchemy 2.0 / Alembic 1.15 / psycopg 3 / Postgres on Railway
**Design rule for this whole document:** every new capability is **config-gated** and **additive**. With all new env vars unset, the app behaves exactly as it does today. Nothing throws at import.

---

## 1. Architecture proposal

### 1.1 Target data flow

```
                        ┌─────────────────────────────────────────────────────────┐
                        │  IMPORT (existing importers.py — unchanged)             │
                        │  customers / KVK / leads / prospects via CSV + webform  │
                        └──────────────────────────┬──────────────────────────────┘
                                                   │  rows land with website="" / email=""
                                                   ▼
            ┌──────────────────────────────────────────────────────────────────────┐
            │  DISCOVERY (OPEN STACK)  app/discovery_open.py                        │
            │  Orchestrates per company/prospect:                                   │
            │   1. name+city -> website         app/search_client.py  (SearXNG)    │
            │   2. address  -> lat/lon/region   app/geocode.py        (Photon)     │
            │   3. crawl+extract contacts       app/web_extract.py    (httpx +     │
            │                                     Trafilatura + reuse of regex/rank)│
            │   4. fallback info@domain (MX)    app/email_guesser.py  (EXISTING)   │
            │  Writes website_confidence + discovery_query_used + input_type +     │
            │  last_discovery_attempt_at. Sets enrichment_status.                   │
            │  GOOGLE path kept behind DISCOVERY_BACKEND=google fallback flag.      │
            └──────────────────────────┬───────────────────────────────────────────┘
                                       ▼
            ┌──────────────────────────────────────────────────────────────────────┐
            │  SUPPRESSION + MATCH  app/suppression.py + app/matching.py            │
            │  - is_suppressed(): SuppressionEntry (email/domain) + unsubscribes    │
            │  - match_kvk_company(): STRICT (email OR name+country) UNCHANGED      │
            │  - NEW richer review-only candidate scorer (does NOT auto-flag Klant) │
            └──────────────────────────┬───────────────────────────────────────────┘
                                       ▼
            ┌──────────────────────────────────────────────────────────────────────┐
            │  TIERING  app/tiering.py (EXISTING, unchanged)                        │
            │  apply_bike_tier / score_kvk_company_tier -> 6 tiers + priority       │
            └──────────────────────────┬───────────────────────────────────────────┘
                                       ▼
            ┌──────────────────────────────────────────────────────────────────────┐
            │  REVIEW (human-in-the-loop, additive routes)                          │
            │  /review/discovery  -> low-confidence website/email queue            │
            │  /review/match      -> possible-Klant candidates (NOT auto-suppressed)│
            │  /review/tier       -> "Manual Review" tier rows                      │
            │  Owner approves -> sets approved_for_outreach = True                  │
            └──────────────────────────┬───────────────────────────────────────────┘
                                       ▼
            ┌──────────────────────────────────────────────────────────────────────┐
            │  SEND  app/email_engine.py -> app/email_providers.py (NEW factory)    │
            │  MAIL_PROVIDER selects: gmail | resend | brevo | smtp | console      │
            │  Reply-To FORCED to sales@schildinc.com at provider layer.           │
            │  Daily cap + spacing + send-time suppression re-check UNCHANGED.     │
            └──────────────────────────────────────────────────────────────────────┘
```

### 1.2 Key principles

- **Discovery confidence is a first-class number (0–100), not a string.** It drives the review queues. Anything below `DISCOVERY_REVIEW_THRESHOLD` goes to manual review instead of being silently trusted.
- **Suppression and Klant-matching stay STRICT.** The richer fuzzy scorer from the code map is implemented as a *review-surfacing* helper only — it never writes `already_client_flag`. This preserves the hard-won "127 true klants" invariant.
- **Open backends are pluggable and degrade to the old Google path**, which itself degrades to nothing. Three layers of graceful fallback.

---

## 2. Exact NEW files to add

| Path | One-line responsibility |
|---|---|
| `app/search_client.py` | SearXNG JSON-API client: `company name + city -> ranked candidate website URLs`; lazy `httpx`, returns `[]` when unconfigured. |
| `app/geocode.py` | Photon/Pelias forward-geocode: `address -> lat/lon/postal/region`; lazy `httpx`, returns `None` when unconfigured. |
| `app/web_extract.py` | Fetch (httpx) + boilerplate-strip (Trafilatura) + extract/rank emails, phones, socials by **reusing** `discovery.py` rankers/validators and `email_guesser.py`. |
| `app/discovery_open.py` | Orchestrator: name→site→geocode→crawl→guess for one Prospect/KvkCompany; writes confidence + provenance columns; honors `DISCOVERY_BACKEND`. |
| `app/suppression.py` | Single home for `is_suppressed()` + `add_suppression()` + review-only fuzzy customer candidate scorer (`find_possible_customers()`). |
| `app/email_providers.py` | Provider abstraction + factory: `get_provider()`, `EmailProvider` ABC, `GmailProvider`/`ResendProvider`/`BrevoSmtpProvider`/`SmtpProvider`/`ConsoleProvider`, forces Reply-To. |
| `tests/conftest.py` | Pytest fixtures: in-memory SQLite session, settings monkeypatch helper, fake httpx transport. |
| `tests/test_search_client.py` | SearXNG parsing/ranking + unconfigured fallback. |
| `tests/test_geocode.py` | Photon parsing + unconfigured fallback. |
| `tests/test_web_extract.py` | Email/phone/social extraction + junk rejection on fixture HTML. |
| `tests/test_discovery_open.py` | End-to-end orchestration with mocked search/geocode/fetch; confidence + provenance assertions. |
| `tests/test_suppression.py` | Suppression hit/miss + that fuzzy scorer never sets `already_client_flag`. |
| `tests/test_email_providers.py` | Factory selection per `MAIL_PROVIDER`; Reply-To forcing; console fallback; Gmail path preserved. |

> Note: `app/suppression.py` consolidates the `is_suppressed()` logic that currently lives inline in `email_engine.py`. To stay rollback-safe, `email_engine.py` will `from app.suppression import is_suppressed` while keeping its existing function as a thin re-export shim for one release, so no other importer breaks.

---

## 3. DB schema changes

Only what the code map flags as **genuinely missing**. Everything additive (nullable / server-default), so a down-migration is a clean drop and rollback never loses required data.

### New revision: `0021_open_discovery` (revises `0020_attachments_notifications`)

**Table `kvk_companies`** — has `match_confidence`, `best_match_reason`, `last_enrichment_attempt_at` already; missing the open-discovery provenance trio:

| Table | Column | Type | Server default | Nullable |
|---|---|---|---|---|
| `kvk_companies` | `website_confidence` | Integer | `0` | yes |
| `kvk_companies` | `discovery_query_used` | Text | NULL | yes |
| `kvk_companies` | `discovery_input_type` | Text | NULL | yes |
| `kvk_companies` | `discovery_backend` | Text | NULL | yes |

**Table `prospects`** — missing the unified discovery audit + the match fields that already exist on KvkCompany:

| Table | Column | Type | Server default | Nullable |
|---|---|---|---|---|
| `prospects` | `website_confidence` | Integer | `0` | yes |
| `prospects` | `discovery_query_used` | Text | NULL | yes |
| `prospects` | `discovery_input_type` | Text | NULL | yes |
| `prospects` | `discovery_backend` | Text | NULL | yes |
| `prospects` | `last_discovery_attempt_at` | DateTime(timezone=True) | NULL | yes |
| `prospects` | `match_confidence` | Text | NULL | yes |
| `prospects` | `best_match_reason` | Text | NULL | yes |

**Table `email_campaign_recipients`** — provider audit (the model only stores `gmail_message_id`):

| Table | Column | Type | Server default | Nullable |
|---|---|---|---|---|
| `email_campaign_recipients` | `provider` | Text | NULL | yes |
| `email_campaign_recipients` | `provider_message_id` | Text | NULL | yes |

`gmail_message_id` is left intact and continues to be written by the Gmail path; new providers write `provider`+`provider_message_id`. No data migration, no backfill required.

**Indexes:** add a non-unique index on `kvk_companies.website_confidence` and `prospects.website_confidence` (review queues filter/sort on them). Indexes are additive and droppable.

> Deliberately **not** adding: `email_source_page`/`phone_source_page` on Prospect (the existing `email_source_url`/`phone_source_url` on KvkCompany cover provenance; Prospect already has `email_discovered_at`/`email_discovery_status`). Keeping the migration minimal reduces rollback surface. These can be a later additive migration if the review UI proves it needs them.

---

## 4. Per-module specs

All dataclasses are `@dataclass(frozen=True)` where they are pure value objects. All heavy deps (`httpx`, `trafilatura`) are imported **inside** functions. Every "unconfigured" path returns empty/None and logs at INFO — never raises.

### 4.1 `app/search_client.py`

```python
@dataclass(frozen=True)
class WebsiteCandidate:
    url: str
    domain: str
    title: str
    snippet: str
    score: int          # 0-100 confidence this is the company's own site
    engine: str         # which searx engine surfaced it

def is_configured() -> bool:
    """True iff settings.searxng_url is set."""

def find_website(
    company_name: str,
    city: str = "",
    country_code: str = "",
    *,
    limit: int = 5,
) -> list[WebsiteCandidate]:
    """Query SearXNG /search?format=json. Rank candidates by name-token
    overlap with the result domain/title (rapidfuzz), demote directories
    (facebook/instagram/linkedin/marktplaats/yelp/kvk.nl), boost ccTLD
    matching country_code. Returns [] if not configured or on any error."""

def best_website(company_name: str, city: str = "", country_code: str = "") -> WebsiteCandidate | None:
    """Top candidate if its score >= settings.discovery_review_threshold, else None."""
```

- **Config consumed:** `searxng_url`, `searxng_timeout_s`, `searxng_engines` (comma list, default `"google,bing,duckduckgo,brave"` — these run *inside* the self-hosted SearXNG, not from our IP), `discovery_review_threshold`.
- **Reuse:** `rapidfuzz.WRatio` (already a dependency), `normalize_domain` from existing utils.
- **Fallback:** `is_configured()==False` → `find_website` returns `[]`. The orchestrator then tries the Google backend if `DISCOVERY_BACKEND=searxng` allows fallback, else marks the row for manual review.

### 4.2 `app/geocode.py`

```python
@dataclass(frozen=True)
class GeoResult:
    lat: float
    lon: float
    postal_code: str
    city: str
    region: str          # province / state
    country_code: str    # ISO-2
    confidence: int      # 0-100
    raw_label: str

def is_configured() -> bool:
    """True iff settings.geocoder_url is set."""

def geocode_address(
    address: str,
    city: str = "",
    postal_code: str = "",
    country_code: str = "",
) -> GeoResult | None:
    """Forward-geocode via Photon (/api?q=) or Pelias (/v1/search) depending on
    settings.geocoder_kind. Returns None if not configured / no hit / error."""
```

- **Config consumed:** `geocoder_url`, `geocoder_kind` (`"photon"`|`"pelias"`, default `"photon"`), `geocoder_timeout_s`, `pelias_api_key` (optional, only for hosted Pelias).
- **Use in pipeline:** fills `primary_postal_code`/`province_code` on KvkCompany when CSV lacked them, and provides region for future geo-targeted campaigns. Purely enrichment — never blocks discovery.
- **Fallback:** unconfigured → `None`; orchestrator skips geo enrichment silently.

### 4.3 `app/web_extract.py`

```python
@dataclass(frozen=True)
class ExtractedContacts:
    email: str
    email_confidence: int
    email_source_url: str
    emails_found: list[str]
    phone: str
    whatsapp_number: str
    whatsapp_url: str
    linkedin_url: str
    instagram_url: str
    pages_scanned: list[str]
    status: str          # found | partial | no_contacts | no_website | error
    error: str = ""

def extract_contacts(
    website: str,
    company_name: str = "",
    city: str = "",
    *,
    max_pages: int | None = None,
    use_browser: bool | None = None,
) -> ExtractedContacts:
    """httpx GET seed + contextual URLs, strip boilerplate with trafilatura,
    run EXISTING regex extractors, then EXISTING _rank_email_candidates /
    _looks_valid_business_email / _pick_best_phone_number / _pick_social_link.
    Optionally render with Playwright (reusing playwright_search lock) only when
    httpx found nothing AND use_browser is True. Falls back to email_guesser
    best_guess(domain) when no on-page email. Never raises."""
```

- **Config consumed:** `discovery_max_pages` (default 8, mirrors `MAX_CRAWL_PAGES`), `discovery_use_browser` (default True), `discovery_http_timeout_s` (default 6), `discovery_user_agent`.
- **Reuse (do NOT rebuild — per code map):** `discovery._rank_email_candidates`, `_looks_valid_business_email`, `_pick_best_phone_number`, `_pick_best_whatsapp_number/url`, `_pick_social_link`, `_is_valid_social_profile_url`, `_prioritize_internal_links`, `_build_contextual_likely_urls`, `_merge_page_info`; and `email_guesser.best_guess`.
- **Replace (per code map):** raw `urllib` fetch → `httpx` with explicit timeouts/retries; regex-only body parse → `trafilatura.extract` for clean text before regex (cuts false positives from nav/footer junk). Playwright becomes strictly optional.
- **Fallback:** if `trafilatura`/`httpx` import fails (deps absent) → log once, fall back to the existing `discovery.py` raw fetch path so discovery still works. If website empty → `status="no_website"`.

### 4.4 `app/discovery_open.py`

```python
@dataclass(frozen=True)
class OpenDiscoveryOutcome:
    website: str
    website_confidence: int
    contacts: ExtractedContacts
    geo: GeoResult | None
    query_used: str
    input_type: str       # "name_city" | "existing_website" | "address"
    backend: str          # "searxng" | "google" | "manual"
    needs_review: bool

def discover_open_for_kvk(session: Session, company: KvkCompany) -> OpenDiscoveryOutcome:
    """Full open pipeline for one KVK row. Persists website, website_domain,
    website_confidence, email_public + confidence + source_url, phone_public,
    socials, primary_postal_code/province_code (from geo if missing),
    discovery_query_used, discovery_input_type, discovery_backend,
    last_enrichment_attempt_at, enrichment_status. Then apply_kvk_matching()
    (STRICT, unchanged). Sets enrichment_status='needs_review' when
    website_confidence < threshold."""

def discover_open_for_prospect(session: Session, prospect: Prospect) -> OpenDiscoveryOutcome:
    """Same for a Prospect; persists to prospect fields incl. the NEW
    last_discovery_attempt_at / website_confidence / discovery_* columns,
    then apply_bike_tier()."""

def backend_in_use() -> str:
    """Resolve effective backend from settings.discovery_backend with
    capability check: 'searxng' if search_client.is_configured() else
    ('google' if settings.google_places_api_key and fallback allowed else 'manual')."""
```

- **Config consumed:** `discovery_backend` (`"searxng"`|`"google"`|`"auto"`, default `"auto"`), `discovery_google_fallback` (bool, default True), `discovery_review_threshold` (int, default 60).
- **Decision logic:**
  1. If `prospect.website` already present → `input_type="existing_website"`, skip search, go straight to extract (confidence inherited 100).
  2. Else `find_website(name, city, country)`; if a candidate clears threshold → use it.
  3. Else, if `discovery_google_fallback` and Google key present and backend != `"searxng"`-strict → try existing `kvk_enrichment._google_places_lookup`.
  4. Else → `backend="manual"`, `needs_review=True`, persist `enrichment_status="needs_review"`, return without extracting.
- **Fallback:** any sub-step failure degrades to the next; total failure → `needs_review=True`. Never raises into the scheduler.

### 4.5 `app/suppression.py`

```python
def is_suppressed(session: Session, email: str, company_name: str = "") -> tuple[bool, str]:
    """Active SuppressionEntry by normalized email OR domain. Moved verbatim
    from email_engine.py; email_engine re-imports it. Returns (True, reason)
    or (False, '')."""

def add_suppression(session: Session, *, email: str = "", domain: str = "",
                    company_name: str = "", reason: str, source: str = "manual") -> SuppressionEntry:
    """Idempotent upsert of a SuppressionEntry (active=True)."""

@dataclass(frozen=True)
class CustomerCandidate:
    customer_id: int
    score: int
    reason: str          # fuzzy_name_city_country | domain_overlap | ...

def find_possible_customers(session: Session, company: KvkCompany,
                            *, min_score: int = 80) -> list[CustomerCandidate]:
    """REVIEW-ONLY fuzzy scorer (the cascade from the code map). NEVER writes
    already_client_flag / matched_customer_id. Surfaces candidates to the
    /review/match queue for human confirmation. Strict match_kvk_company stays
    the only thing that sets Klant status."""
```

- **Config consumed:** `match_review_min_score` (default 80).
- **Fallback:** empty inputs → `[]`. No external calls; cannot fail on infra.

### 4.6 `app/email_providers.py`

```python
@dataclass
class EmailSendResult:
    ok: bool
    message_id: str = ""
    error: str = ""
    transient: bool = False
    provider: str = ""

class EmailProvider(ABC):
    name: str
    @abstractmethod
    def send(self, session: Session, *, to_email: str, subject: str,
             body_html: str, body_text: str = "", from_alias: str = "",
             from_name: str = "", reply_to: str = "",
             list_unsubscribe: str = "", thread_id: str = "",
             in_reply_to: str = "") -> EmailSendResult: ...

class GmailProvider(EmailProvider):      # wraps existing gmail_sender.send_message — zero behavior change
class ResendProvider(EmailProvider):     # Resend REST API (reuses emailing.py logic), lazy httpx
class BrevoSmtpProvider(EmailProvider):  # smtplib to smtp-relay.brevo.com:587
class SmtpProvider(EmailProvider):       # generic smtplib (settings.smtp_*)
class ConsoleProvider(EmailProvider):    # logs payload, returns ok=True

def get_provider(session: Session) -> EmailProvider:
    """Factory keyed on settings.mail_provider.lower():
    gmail|resend|brevo|smtp|console. Unknown/empty -> ConsoleProvider.
    If chosen provider is unconfigured (e.g. gmail not connected, no resend
    key) -> ConsoleProvider with a logged warning (graceful degrade)."""

_REPLY_TO = "sales@schildinc.com"  # hard constant, see §5
```

- **Config consumed:** `mail_provider`, `resend_api_key`, `smtp_*`, `brevo_smtp_user`, `brevo_smtp_key`, `gmail_*`, `reply_to_email`.
- **Reply-To forcing:** see §5 — every provider's `send()` overwrites `reply_to` with the forced value before building headers.
- **Fallback:** every provider catches its own exceptions → `EmailSendResult(ok=False, transient=...)`; `GmailNotConnected` is caught in factory and downgraded to Console so a campaign never crashes the scheduler.

---

## 5. Email provider plan

### Selection
`email_engine.send_campaign_batch` changes exactly one call site (per the code map, line ~330):

```python
# before:  result = send_message(session, ...)
provider = get_provider(session)
result = provider.send(session, to_email=..., subject=..., body_html=..., body_text=...,
                       from_alias=sender_alias, from_name=sender_name,
                       reply_to=reply_to, list_unsubscribe=unsubscribe_url_for(token))
```

All surrounding logic — `sent_today()` daily cap, `gmail_send_spacing_seconds` spacing, build-time + send-time `is_suppressed()`, status transitions — is **untouched**. On success, store `recipient.provider = result.provider` and `recipient.provider_message_id = result.message_id`; for the Gmail provider also keep writing `recipient.gmail_message_id` for backward compatibility.

### Reply-To is ALWAYS sales@schildinc.com
Three enforcement layers, defense-in-depth:
1. **Provider constant override:** every `EmailProvider.send()` begins with `reply_to = settings.reply_to_email or "sales@schildinc.com"` — the caller's `reply_to` argument is ignored for the header. This guarantees that even a mis-built campaign (`campaign.reply_to` set to something else) cannot leak a wrong Reply-To.
2. **Default already correct:** `settings.reply_to_email` defaults to `sales@schildinc.com` (verified in config.py:53).
3. **Merge field:** `{{reply_to}}` continues to resolve to the same value in `render_for_recipient`.

> This is a behavior change from today (where `campaign.reply_to` could override). It is intentional and matches the requirement "reply-to ALWAYS sales@schildinc.com." If per-campaign Reply-To is ever needed it would be re-added behind an explicit `ALLOW_CAMPAIGN_REPLY_TO=true` flag.

### Gmail path stays working
`GmailProvider.send()` is a thin wrapper that calls the existing `gmail_sender.send_message()` and maps `GmailSendResult` → `EmailSendResult(provider="gmail")`. The Gmail OAuth connect flow, token refresh, send-as alias, and inbound poller are all untouched. With `MAIL_PROVIDER=gmail` (or unset, since we'll keep `gmail` as the recommended default for the owner), the system behaves identically to today.

---

## 6. Config / env vars to add

All have safe defaults so unset = today's behavior.

```python
# --- Open discovery ---
searxng_url: str                 = os.getenv("SEARXNG_URL", "")            # "" => disabled
searxng_engines: str             = os.getenv("SEARXNG_ENGINES", "google,bing,duckduckgo,brave")
searxng_timeout_s: float         = float(os.getenv("SEARXNG_TIMEOUT_S", "8"))

geocoder_url: str                = os.getenv("GEOCODER_URL", "")           # "" => disabled
geocoder_kind: str               = os.getenv("GEOCODER_KIND", "photon")    # photon|pelias
geocoder_timeout_s: float        = float(os.getenv("GEOCODER_TIMEOUT_S", "8"))
pelias_api_key: str              = os.getenv("PELIAS_API_KEY", "")

discovery_backend: str           = os.getenv("DISCOVERY_BACKEND", "auto")  # auto|searxng|google
discovery_google_fallback: bool  = _as_bool(os.getenv("DISCOVERY_GOOGLE_FALLBACK"), True)
discovery_review_threshold: int  = int(os.getenv("DISCOVERY_REVIEW_THRESHOLD", "60"))
discovery_max_pages: int         = int(os.getenv("DISCOVERY_MAX_PAGES", "8"))
discovery_use_browser: bool      = _as_bool(os.getenv("DISCOVERY_USE_BROWSER"), True)
discovery_http_timeout_s: float  = float(os.getenv("DISCOVERY_HTTP_TIMEOUT_S", "6"))
discovery_user_agent: str        = os.getenv("DISCOVERY_USER_AGENT",
                                             "Mozilla/5.0 (compatible; SchildBot/1.0)")
match_review_min_score: int      = int(os.getenv("MATCH_REVIEW_MIN_SCORE", "80"))

# --- Email providers (mail_provider, resend_api_key, smtp_*, reply_to_email ALREADY EXIST) ---
brevo_smtp_user: str             = os.getenv("BREVO_SMTP_USER", "")
brevo_smtp_key: str              = os.getenv("BREVO_SMTP_KEY", "")
brevo_smtp_host: str             = os.getenv("BREVO_SMTP_HOST", "smtp-relay.brevo.com")
brevo_smtp_port: int             = int(os.getenv("BREVO_SMTP_PORT", "587"))

# --- Worker (see §9) ---
discovery_use_rq: bool           = _as_bool(os.getenv("DISCOVERY_USE_RQ"), False)
redis_url: str                   = os.getenv("REDIS_URL", "")
```

> Recommendation: keep `MAIL_PROVIDER` default as it is today (`console`) in code, but set `MAIL_PROVIDER=gmail` in Railway so production behavior is unchanged on day one.

---

## 7. UI/UX plan (additive routes + templates only)

Follows the verified pattern: static routes before parameterized ones; `templates.TemplateResponse`; new `<details class="nav-group">` in `base.html`; reuse `.card`, `.data-table`, `.badge`, `.btn-sm`, `.filter-form`, pagination block. **No frontend rewrite.** New nav group "Outreach Pipeline".

| Route | Template | Purpose | Key actions (POST) |
|---|---|---|---|
| `GET /import` | `import.html` | Single landing page surfacing existing `/admin/import/*` endpoints + a "what happens next" explainer for the owner. | reuses existing import POSTs |
| `GET /review/discovery` | `review_discovery.html` | Queue of rows with `enrichment_status='needs_review'` OR `website_confidence < threshold`. Shows candidate site, snippet, confidence badge. | `/admin/review/discovery/{id}/accept`, `/reject`, `/set-website` |
| `GET /review/match` | `review_match.html` | `find_possible_customers()` candidates per company — human confirms/denies Klant. | `/admin/review/match/{id}/confirm` (sets flag), `/dismiss` |
| `GET /review/tier` | `review_tier.html` | Rows where `recommended_contact_type='Manual Review'` (Low Fit / Niche). | `/admin/review/tier/{id}/override` |
| `GET /outreach-ready` | `outreach_ready.html` | Approved + has-email + not-suppressed + not-Klant rows, grouped by tier/sector/country; "Create campaign from these" passes `?ids=`. | links to `/emails/campaigns/new?ids=` |
| `GET /emails/campaigns/{id}/preview` | `email_campaign_preview.html` | Render merged subject/body for first N recipients + provider + forced Reply-To + estimated days-to-complete at daily cap. | none (read-only) |
| `GET /companies/{id}` | reuse `kvk_company_detail.html` | Add a "Discovery provenance" card: `discovery_backend`, `discovery_query_used`, `website_confidence`, source URLs. | existing enrich/verify buttons |

Each review page is a filtered list using the documented pagination + `.filter-form` skeleton. Confidence rendered as `.badge-green` (≥80), `.badge-amber` (60–79), `.badge-red` (<60).

`base.html` nav addition:
```html
<details class="nav-group" data-key="pipeline" {% if path.startswith('/review') or path.startswith('/outreach') or path.startswith('/import') %}open{% endif %}>
  <summary class="nav-section">Outreach Pipeline</summary>
  <a href="/import">Import</a>
  <a href="/review/discovery">Discovery review</a>
  <a href="/review/match">Match review</a>
  <a href="/review/tier">Tier review</a>
  <a href="/outreach-ready">Outreach-ready</a>
</details>
```

---

## 8. Test plan

Run offline (no network) via mocked httpx transports and in-memory SQLite. `tests/conftest.py` provides `db_session`, `set_settings(**overrides)` (monkeypatches the frozen dataclass via `dataclasses.replace` on the module-level `settings`), and `fake_http(routes)`.

| Test file | Asserts |
|---|---|
| `test_search_client.py` | (a) JSON parse → ranked `WebsiteCandidate`s; (b) directories (facebook/marktplaats/kvk.nl) demoted below own-domain; (c) ccTLD matching country boosts score; (d) `SEARXNG_URL` unset → `find_website()==[]` and no exception. |
| `test_geocode.py` | (a) Photon feature → `GeoResult` with correct lat/lon/postal/ISO-2; (b) Pelias shape parsed when `GEOCODER_KIND=pelias`; (c) unset URL → `None`; (d) HTTP 500 → `None` not raise. |
| `test_web_extract.py` | (a) `info@domain` on fixture page ranked above `noreply@`; (b) vendor email (`shopify.com`) rejected; (c) phone normalized to ≥8 digits w/ `+`; (d) Instagram `/reel/` rejected, `/company/` LinkedIn accepted; (e) no on-page email → falls back to `best_guess`; (f) trafilatura import-fail path still extracts via legacy fetch. |
| `test_discovery_open.py` | (a) name+city → site → contacts persisted with provenance columns set; (b) low candidate score → `enrichment_status='needs_review'`, `needs_review=True`; (c) existing website skips search (`input_type='existing_website'`); (d) `DISCOVERY_BACKEND=searxng` + searx unconfigured + fallback off → `backend='manual'`, nothing extracted, no raise; (e) STRICT `apply_kvk_matching` still the only thing setting `already_client_flag`. |
| `test_suppression.py` | (a) email + domain hits return `(True, reason)`; (b) inactive entry ignored; (c) `find_possible_customers` returns scored candidates but the KvkCompany row's `already_client_flag` is still False afterward (review-only invariant). |
| `test_email_providers.py` | (a) factory returns correct class per `MAIL_PROVIDER`; (b) **every** provider's outgoing Reply-To == `sales@schildinc.com` even when `reply_to` arg is `evil@x.com`; (c) `MAIL_PROVIDER=gmail` routes through `gmail_sender.send_message` (mocked) and maps result; (d) unconfigured provider downgrades to Console, `ok=True`, logs warning; (e) unknown provider name → Console. |
| `test_migration_0021.py` (integration) | `alembic upgrade head` then `downgrade -1` round-trips on SQLite/PG; new columns nullable; no data loss on existing rows. |

---

## 9. Deployment plan (Railway)

### Migrations
Start command is unchanged and already runs migrations synchronously:
```
alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
```
`0021_open_discovery` runs automatically on next deploy. Because it is additive + nullable, the running old container is forward-compatible during the rollover.

### Dependencies (`requirements.txt` additions)
```
httpx==0.28.0          # open discovery fetch + Resend/SearXNG/geocode clients
trafilatura==1.8.1     # boilerplate-stripped text extraction
# optional, only if DISCOVERY_USE_RQ=true:
redis==5.2.1
rq==2.0.1
```
`nixpacks` rebuild already reinstalls Playwright; these are pure-Python wheels (fast).

### Workers — two options, config-gated
- **Default (threaded, zero infra):** keep the existing daemon-thread schedulers from `lifespan`. Add one new scheduler `start_open_discovery_scheduler()` that polls `enrichment_status IN ('pending')` and calls `discover_open_for_kvk` with bounded concurrency (reuse the 3-worker pattern from `kvk_enrichment`). This is the **rollout default** — no Redis required.
- **Optional (Redis + RQ) when `DISCOVERY_USE_RQ=true` and `REDIS_URL` set:** the scheduler enqueues discovery jobs instead of running them inline; a separate Railway "worker" process runs `rq worker discovery`. Selection is a lazy `try: import rq` guarded by the flag, exactly per the code map's hybrid recommendation. With the flag off, RQ is never imported, so missing Redis cannot crash the app.

Worker process (only if RQ enabled), as a second Railway service sharing the repo:
```
rq worker -u $REDIS_URL discovery
```

### Infra to provision (owner-facing, see §11)
- A **SearXNG** instance (Railway template or a small VPS) → set `SEARXNG_URL`.
- A **Photon** instance (or hosted Pelias) → set `GEOCODER_URL`.
- Optionally **Redis** add-on → set `REDIS_URL` + `DISCOVERY_USE_RQ=true`.

---

## 10. Rollout plan + low-confidence fallback

### Phased rollout
1. **Ship dark (additive only):** deploy `0021` + all new modules with every new env var **unset**. Behavior identical to today. Run `pytest`. Confirm `/emails` still sends via Gmail.
2. **Flip email layer first (lowest risk):** set `MAIL_PROVIDER=gmail` explicitly. No change. Then optionally test `MAIL_PROVIDER=console` in staging to confirm the factory path, then `resend`/`brevo` once domains/SPF/DKIM are verified. Reply-To forcing is covered by tests.
3. **Stand up SearXNG**, set `SEARXNG_URL`, `DISCOVERY_BACKEND=auto`, `DISCOVERY_GOOGLE_FALLBACK=true`. Run open discovery on a **small batch** (e.g. 50 KVK rows via the `/review/discovery` flow) and eyeball confidence vs. reality.
4. **Add geocoder**, then **tune `DISCOVERY_REVIEW_THRESHOLD`** based on observed precision.
5. **Cut the Google fallback** to optional/off (`DISCOVERY_GOOGLE_FALLBACK=false`) once open precision is acceptable. Google path remains in code, re-enableable by flag — full rollback safety.

### Fallback when discovery confidence is low
- Any row with `website_confidence < DISCOVERY_REVIEW_THRESHOLD`, or `backend='manual'`, or no email after guesser → `enrichment_status='needs_review'` and lands in `/review/discovery`. It is **never** auto-added to a campaign (outreach-ready requires `approved_for_outreach=True` + email present).
- Possible-Klant fuzzy hits go to `/review/match` for human confirmation; they are **not** auto-suppressed and **not** auto-flagged, preserving the strict-matching invariant.
- Existing KVK browser-agent endpoints (`/api/kvk/agent/pending` + `/result`) remain the manual escape hatch for hard rows.

### Rollback
- Code: every new code path is behind a flag → set flags back to defaults/unset.
- DB: `alembic downgrade -1` drops the 13 additive nullable columns + 2 indexes with no data loss to existing required fields.

---

## 11. Ready now vs needs infra / manual review (honest)

### Ready to build and ship now (no external infra)
- `app/email_providers.py` factory + Gmail/Resend/Brevo/SMTP/Console providers and the one-line swap in `email_engine.py`. **Resend** and **Brevo SMTP** only need an API key + verified sending domain (SPF/DKIM) — no servers to run.
- `app/suppression.py` (consolidation + review-only fuzzy scorer) — pure DB, no infra.
- `app/web_extract.py` using `httpx` + `trafilatura` — pip-only, runs in the existing dyno.
- Migration `0021`, all review-queue routes/templates, the threaded `start_open_discovery_scheduler`, and the full test suite.
- `email_guesser` MX fallback (already exists) keeps producing `info@domain` candidates regardless of search backend.

### Needs infra the owner must provision
- **SearXNG** (`SEARXNG_URL`) — required for non-Google name→website. Until it exists, `discovery_open` with `DISCOVERY_BACKEND=auto` falls back to the existing Google/DDG path; with `=searxng` strict it routes rows to manual review. **This is the one piece that genuinely requires the owner to stand up a service** (Railway template ~15 min, or a small VPS).
- **Photon / Pelias** (`GEOCODER_URL`) — needed only for geocode enrichment; everything else works without it. Lowest priority.
- **Redis** (`REDIS_URL` + `DISCOVERY_USE_RQ=true`) — *optional* scale-out. The threaded fallback covers the owner's current single-dyno volume; Redis is only worth it if discovery throughput becomes a bottleneck.

### Inherently manual review (by design, not a gap)
- Low-confidence website matches and possible-Klant fuzzy hits — these are routed to the new review queues on purpose to protect the strict-matching invariant and avoid emailing the wrong company. A non-technical owner operates these via the additive pages in §7; no code knowledge required.
- LinkedIn cold outreach stays manual (ToS), unchanged.

---

### Relevant file paths (all absolute)
- New code: `/Users/kevinolla/AI Project/B2B Prospect tool/app/{search_client,geocode,web_extract,discovery_open,suppression,email_providers}.py`
- Migration: `/Users/kevinolla/AI Project/B2B Prospect tool/alembic/versions/0021_open_discovery.py` (revises `0020_attachments_notifications`)
- Touched (minimal): `/Users/kevinolla/AI Project/B2B Prospect tool/app/email_engine.py` (one send-call swap + `is_suppressed` re-import), `app/config.py` (new settings), `app/models.py` (new columns), `app/main.py` (new routes + `start_open_discovery_scheduler`), `app/templates/base.html` (nav group), `requirements.txt`.
- Tests: `/Users/kevinolla/AI Project/B2B Prospect tool/tests/`
- One correctness note for follow-up: **CLAUDE.md migration table is stale** — it lists HEAD as 0017, but the real HEAD is `0020_attachments_notifications` (verified in `alembic/versions/`). Worth correcting so future sessions chain new revisions from the right parent.