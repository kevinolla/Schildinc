"""
Public-website contact extraction (open-stack, dependency-light).
=================================================================

Purpose
-------
Given a company's public website, fetch its pages and pull out the best
*business* contact channels — primarily a reachable email and phone number —
together with provenance (which page they were found on) and a 0-100
confidence score for each. This is the "crawl + extract" leg of the open
discovery pipeline described in the redesign spec; the orchestrator
(``app/discovery_open.py``) calls :func:`discover_contacts` after a website
has been located.

Design / graceful-fallback behaviour
------------------------------------
Everything here is built to degrade gracefully and to NEVER raise at import
time or into the caller:

* **Heavy/optional deps are lazy-imported inside functions.** ``httpx`` and
  ``trafilatura`` are imported only when a fetch/extraction actually runs. If
  either is missing, the relevant function logs once at INFO and falls back to
  a pure-stdlib path (``urllib`` fetch, regex tag-strip). So the module imports
  and the pure functions work even on a box with neither installed.
* **Playwright is strictly optional** and only used when
  ``settings.web_extract_use_playwright`` is true *and* the static fetch found
  nothing. It reuses the module-level launch lock from ``playwright_search`` so
  it never races other Playwright callers. If Playwright is absent it is simply
  skipped.
* **Config is read defensively** via ``getattr(settings, name, default)`` so
  the module imports even before the integrator adds the new settings.
* **The pure parsing/ranking helpers** (:func:`extract_emails`,
  :func:`extract_phones`, :func:`rank_email`, :func:`normalize_nl_phone`,
  :func:`contact_page_links`, :func:`main_text`) take strings and return
  strings/lists — no network, no DB, fully unit-testable.

Public surface
--------------
* ``fetch_html(url) -> str``
* ``main_text(html) -> str``
* ``contact_page_links(html, base_url) -> list[str]``
* ``extract_emails(text_or_html) -> list[str]``
* ``extract_phones(text, country="NL") -> list[str]``
* ``rank_email(emails, company_name="") -> list[str]``
* ``normalize_nl_phone(s) -> str``
* ``rank_phones(phones, context_by_phone=None) -> list[str]``
* ``discover_contacts(url, company_name="") -> ContactResult``

Reply-To, suppression, persistence, matching and tiering are all handled
elsewhere — this module only reads the web and returns a value object.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import urljoin, urlparse

# NOTE: deliberately NO top-level import of httpx / trafilatura / playwright.
# They are imported lazily inside the functions that need them so this module
# is import-safe on a host where those deps are not installed.

logger = logging.getLogger(__name__)

# Read the configured settings object if it is importable. We never want a
# config problem to break the import of this module, so guard it.
try:  # pragma: no cover - trivial import guard
    from app.config import settings as _settings
except Exception:  # pragma: no cover
    _settings = None  # type: ignore[assignment]


def _cfg(name: str, default):
    """Read a settings attribute defensively.

    The integrator will add new ``settings.*`` attributes (web_extract_*,
    discovery_*). Until they exist we fall back to ``default`` so the module
    imports and runs regardless of config-deploy ordering.
    """
    if _settings is None:
        return default
    return getattr(_settings, name, default)


# ── Tunables (mirror discovery.py constants; overridable via settings) ────────
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; SchildIncWebExtract/1.0; +https://schildinc.com)"
# Local-parts that are never a usable business inbox.
REJECT_LOCAL_PARTS = {
    "noreply",
    "no-reply",
    "no_reply",
    "donotreply",
    "do-not-reply",
    "do_not_reply",
    "mailer-daemon",
    "mailerdaemon",
    "postmaster",
    "abuse",
    "bounce",
    "bounces",
    "system",
    "root",
    "daemon",
    "support-ticket",
}
# Substrings that, if present in the local part, mark the address as machine/junk.
REJECT_LOCAL_SUBSTRINGS = ("noreply", "no-reply", "no_reply", "donotreply", "system", "wpforms", "sentry")
# Third-party SaaS sending domains we should never treat as the company's own.
VENDOR_DOMAINS = {
    "2moso.com",
    "shopify.com",
    "myshopify.com",
    "mailchimp.com",
    "klaviyo.com",
    "zendesk.com",
    "salesforce.com",
    "hubspot.com",
    "wixpress.com",
    "wix.com",
    "squarespace.com",
    "godaddy.com",
    "sentry.io",
    "wordpress.com",
    "wpengine.com",
}
# Free webmail providers — accepted (a real human reads them) but ranked below
# an on-domain mailbox.
FREE_WEBMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "yahoo.com",
    "yahoo.co.uk",
    "icloud.com",
    "me.com",
    "msn.com",
    "ziggo.nl",
    "kpnmail.nl",
    "planet.nl",
    "xs4all.nl",
    "online.nl",
    "home.nl",
    "hetnet.nl",
}
# Image / asset file extensions that sometimes look like an email local part
# when a filename embeds an "@" (e.g. retina assets "logo@2x.png").
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", ".ico", ".bmp", ".avif")

# Ordered preference of generic mailbox prefixes. Index 0 == most preferred.
# info@ > sales@ > contact@ > hello@ > service@ then other generics, then personal.
GENERIC_PREFIX_RANK: list[str] = [
    "info",
    "sales",
    "contact",
    "hello",
    "service",
]
# Additional generics that still beat a personal mailbox but rank below the
# explicitly-ordered five above.
OTHER_GENERIC_PREFIXES = {
    "support",
    "office",
    "team",
    "mail",
    "shop",
    "verkoop",
    "klantenservice",
    "admin",
    "enquiries",
    "hallo",
    "welcome",
}

# Words in surrounding context that boost a phone (header / contact / footer)
# and words that should sink it (fax). Used by rank_phones().
PHONE_GOOD_CONTEXT = ("contact", "phone", "tel", "telefoon", "call", "header", "footer", "mobile", "mobiel")
PHONE_BAD_CONTEXT = ("fax", "telefax")

# Email regexes (case-insensitive). Mirrors discovery.py's EMAIL_PATTERN plus
# obfuscation handling for "info [at] x [dot] nl" style addresses.
EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
MAILTO_PATTERN = re.compile(r"mailto:([^\"'<>?\s]+)", re.I)
# "info [at] domain [dot] nl", "info (at) domain (dot) nl", "info at domain dot nl"
OBFUSCATED_AT_DOT_PATTERN = re.compile(
    r"([A-Z0-9._%+-]+)\s*(?:\[at\]|\(at\)|\{at\}|\s+at\s+|&#64;)\s*"
    r"([A-Z0-9.-]+)\s*(?:\[dot\]|\(dot\)|\{dot\}|\s+dot\s+)\s*([A-Z]{2,})",
    re.I,
)
# "info AT domain DOT nl" where author chained multiple dot tokens is handled
# by the greedy domain group above; a trailing extra "[dot]xx" is uncommon.

# Phone candidate regex — a run of digits/separators long enough to be a real
# number. Kept deliberately permissive; normalize_nl_phone() does validation.
PHONE_CANDIDATE_PATTERN = re.compile(r"\+?\d[\d\s().\-/]{6,}\d")

# Contact-ish path keywords used by contact_page_links().
CONTACT_PATH_KEYWORDS = (
    "contact",
    "contact-us",
    "contactus",
    "about",
    "about-us",
    "aboutus",
    "over-ons",
    "overons",
    "impressum",
    "legal",
    "privacy",
    "terms",
    "customer-service",
    "customerservice",
    "klantenservice",
)

# Status constants.
STATUS_FOUND = "found"          # at least an email
STATUS_PARTIAL = "partial"      # phone/social but no email
STATUS_NO_CONTACTS = "no_contacts"
STATUS_NO_WEBSITE = "no_website"
STATUS_ERROR = "error"


# ──────────────────────────────────────────────────────────────────────────
# Value object
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class ContactResult:
    """Outcome of :func:`discover_contacts` — pure data, no ORM references."""

    website: str = ""
    website_domain: str = ""
    email_public: str = ""
    phone_public: str = ""
    email_source_page: str = ""
    phone_source_page: str = ""
    email_confidence: int = 0
    phone_confidence: int = 0
    website_confidence: int = 0
    status: str = STATUS_NO_WEBSITE
    emails_found: list[str] = field(default_factory=list)
    phones_found: list[str] = field(default_factory=list)
    pages_scanned: list[str] = field(default_factory=list)
    error: str = ""


# ──────────────────────────────────────────────────────────────────────────
# URL helpers (pure)
# ──────────────────────────────────────────────────────────────────────────
def ensure_http_url(value: str) -> str:
    """Prefix a bare host with https:// so urlparse/httpx treat it as a URL."""
    value = (value or "").strip()
    if not value:
        return ""
    return value if value.startswith(("http://", "https://")) else f"https://{value}"


def _domain_of(url: str) -> str:
    """Bare registrable host of a URL, lowercased, without leading www."""
    try:
        netloc = urlparse(ensure_http_url(url)).netloc.lower()
    except ValueError:
        return ""
    netloc = netloc.split("@")[-1].split(":")[0]
    return re.sub(r"^www\.", "", netloc)


def _email_domain(email: str) -> str:
    email = (email or "").strip().lower()
    return email.split("@", 1)[1] if "@" in email else ""


# ──────────────────────────────────────────────────────────────────────────
# Fetch
# ──────────────────────────────────────────────────────────────────────────
def fetch_html(url: str) -> str:
    """Fetch a single URL and return its HTML body (best effort).

    Strategy, in order of preference:

    1. ``httpx`` GET with explicit timeouts + redirect following. Returns ""
       for non-HTML responses or any HTTP/transport error.
    2. If httpx is not installed, fall back to a stdlib ``urllib`` GET.
    3. If ``settings.web_extract_use_playwright`` is true AND steps 1-2 yielded
       no usable HTML, render with Playwright (reusing the shared launch lock).

    Never raises — returns "" on any failure.
    """
    target = ensure_http_url(url)
    if not target:
        return ""

    timeout = float(_cfg("discovery_http_timeout_s", _cfg("web_extract_timeout_s", 6)))
    user_agent = _cfg("discovery_user_agent", DEFAULT_USER_AGENT)

    html = _fetch_with_httpx(target, timeout, user_agent)
    if not html:
        html = _fetch_with_urllib(target, timeout, user_agent)

    if not html and bool(_cfg("web_extract_use_playwright", False)):
        html = _fetch_with_playwright(target, user_agent)

    return html or ""


def _fetch_with_httpx(url: str, timeout: float, user_agent: str) -> str:
    """Primary fetch path. Lazy-imports httpx; returns "" if absent or on error."""
    try:
        import httpx  # lazy: optional dep
    except Exception:
        logger.info("web_extract: httpx not installed, falling back to urllib")
        return ""
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en,nl;q=0.8,de;q=0.6",
    }
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers=headers,
            verify=False,  # SMB sites often have broken/expired certs
        ) as client:
            response = client.get(url)
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type.lower() and content_type:
            return ""
        return response.text or ""
    except Exception as exc:  # noqa: BLE001 — never propagate a fetch error
        logger.info("web_extract: httpx fetch failed for %s: %s", url, exc)
        return ""


def _fetch_with_urllib(url: str, timeout: float, user_agent: str) -> str:
    """Stdlib fallback so discovery still works if httpx is unavailable."""
    from urllib.request import Request, urlopen  # stdlib, safe at any time

    request = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 — controlled URL
            content_type = response.headers.get("Content-Type", "")
            if content_type and "html" not in content_type.lower():
                return ""
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except Exception as exc:  # noqa: BLE001
        logger.info("web_extract: urllib fetch failed for %s: %s", url, exc)
        return ""


def _fetch_with_playwright(url: str, user_agent: str) -> str:
    """Optional JS-render fallback. Reuses the shared launch lock; never raises."""
    try:
        from playwright.sync_api import sync_playwright  # lazy: optional dep
    except Exception:
        logger.info("web_extract: playwright requested but not installed; skipping")
        return ""

    # Reuse the global launch lock from playwright_search so we never race other
    # Playwright callers (sync_playwright from multiple threads explodes).
    lock = None
    try:
        from app.playwright_search import _LAUNCH_LOCK as lock  # type: ignore[attr-defined]
    except Exception:
        lock = None

    timeout_ms = int(_cfg("playwright_timeout_ms", 6000))

    def _run() -> str:
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
                )
                try:
                    page = browser.new_page(ignore_https_errors=True, user_agent=user_agent)
                    page.set_default_timeout(timeout_ms)
                    page.goto(url, wait_until="domcontentloaded")
                    return page.content() or ""
                finally:
                    browser.close()
        except Exception as exc:  # noqa: BLE001
            logger.info("web_extract: playwright render failed for %s: %s", url, exc)
            return ""

    if lock is not None:
        with lock:
            return _run()
    return _run()


# ──────────────────────────────────────────────────────────────────────────
# main_text — boilerplate-stripped readable text (pure)
# ──────────────────────────────────────────────────────────────────────────
def main_text(html: str) -> str:
    """Return clean, boilerplate-stripped text from an HTML document.

    Uses ``trafilatura`` when available (best signal-to-noise). Falls back to a
    regex tag-strip identical in spirit to discovery.py's ``_extract_readable_text``
    when trafilatura is not installed or returns nothing. Pure / no network.
    """
    raw = str(html or "")
    if not raw.strip():
        return ""

    try:
        import trafilatura  # lazy: optional dep
    except Exception:
        return _strip_tags(raw)

    try:
        extracted = trafilatura.extract(
            raw,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
    except Exception as exc:  # noqa: BLE001 — never let extraction blow up
        logger.info("web_extract: trafilatura.extract failed: %s", exc)
        extracted = None

    if extracted and extracted.strip():
        return " ".join(extracted.split()).strip()
    # trafilatura found nothing usable (e.g. tiny page) — fall back to tag-strip
    return _strip_tags(raw)


def _strip_tags(html: str) -> str:
    """Pure-stdlib tag stripper (fallback when trafilatura is unavailable)."""
    text = str(html or "")
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<noscript[\s\S]*?</noscript>", " ", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(
        r"</(p|li|div|span|a|h1|h2|h3|h4|h5|h6|footer|section|article|ul|ol|td|tr)>",
        " ",
        text,
        flags=re.I,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return " ".join(text.split()).strip()


# ──────────────────────────────────────────────────────────────────────────
# contact_page_links (pure)
# ──────────────────────────────────────────────────────────────────────────
def contact_page_links(html: str, base_url: str) -> list[str]:
    """Find same-domain links that look like contact/about/legal pages.

    Returns absolute URLs, de-duplicated, ordered by keyword priority
    (contact pages first). Cross-domain links and asset links are dropped.
    Pure — operates on the given HTML string only.
    """
    base_domain = _domain_of(base_url)
    seen: set[str] = set()
    scored: list[tuple[int, str]] = []
    for match in re.finditer(r"<a[^>]+href=[\"']([^\"'#]+)[\"']", str(html or ""), re.I):
        href = unescape(match.group(1).strip())
        if not href or href.lower().startswith(("mailto:", "tel:", "javascript:", "data:")):
            continue
        try:
            absolute = urljoin(ensure_http_url(base_url), href)
        except ValueError:
            continue
        # strip fragment
        absolute = absolute.split("#", 1)[0].rstrip("/")
        if not absolute or absolute in seen:
            continue
        # same registrable domain only
        if base_domain and _domain_of(absolute) != base_domain:
            continue
        path = urlparse(absolute).path.lower()
        if re.search(r"\.(pdf|jpg|jpeg|png|gif|svg|webp|zip|js|css|woff2?|ttf|ico)$", path):
            continue
        # rank by which keyword matches (earlier keyword == higher priority)
        rank = _contact_path_rank(absolute)
        if rank is None:
            continue
        seen.add(absolute)
        scored.append((rank, absolute))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [url for _, url in scored]


def _contact_path_rank(url: str) -> int | None:
    """Lower number == higher priority; None if the URL is not contact-ish."""
    haystack = url.lower()
    for index, keyword in enumerate(CONTACT_PATH_KEYWORDS):
        if keyword in haystack:
            return index
    return None


# ──────────────────────────────────────────────────────────────────────────
# extract_emails (pure)
# ──────────────────────────────────────────────────────────────────────────
def extract_emails(text_or_html: str) -> list[str]:
    """Extract candidate email addresses from raw text OR HTML.

    Handles:
      * plain ``user@domain.tld`` addresses,
      * ``mailto:`` hrefs,
      * de-obfuscated ``info [at] domain [dot] nl`` / ``(at)`` / ``&#64;`` forms.

    Returns a de-duplicated, lowercased list in first-seen order. Junk
    (image filenames, etc.) is left to :func:`rank_email` / validation —
    extraction stays permissive, ranking is strict.
    """
    source = str(text_or_html or "")
    if not source:
        return []
    decoded = unescape(source)

    found: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        email = (value or "").strip().strip(".,;:<>()[]\"'").lower()
        if email and email not in seen and "@" in email:
            seen.add(email)
            found.append(email)

    # mailto: hrefs first (highest signal)
    for match in MAILTO_PATTERN.finditer(decoded):
        _add(match.group(1).split("?")[0])
    # de-obfuscated forms
    for match in OBFUSCATED_AT_DOT_PATTERN.finditer(decoded):
        _add(f"{match.group(1)}@{match.group(2)}.{match.group(3)}")
    # plain addresses
    for match in EMAIL_PATTERN.finditer(decoded):
        _add(match.group(0))
    return found


def _is_image_filename_email(email: str) -> bool:
    """True if the local part is actually an image asset filename (e.g. logo@2x.png)."""
    local = email.split("@", 1)[0]
    domain = _email_domain(email)
    return any(domain.endswith(ext) or local.endswith(ext) for ext in IMAGE_EXTENSIONS)


def _is_junk_email(email: str) -> bool:
    """Reject machine/system/vendor/image-filename addresses outright."""
    if not email or "@" not in email:
        return True
    local, domain = email.split("@", 1)
    if not local or not domain or "." not in domain:
        return True
    if _is_image_filename_email(email):
        return True
    if local in REJECT_LOCAL_PARTS:
        return True
    if any(sub in local for sub in REJECT_LOCAL_SUBSTRINGS):
        return True
    if domain in VENDOR_DOMAINS:
        return True
    if "example." in domain or "test." in domain or domain.endswith(".example"):
        return True
    if "sentry" in domain or "wixpress" in domain:
        return True
    return False


# ──────────────────────────────────────────────────────────────────────────
# rank_email (pure)
# ──────────────────────────────────────────────────────────────────────────
def rank_email(emails: list[str], company_name: str = "") -> list[str]:
    """Rank candidate emails best-first and drop junk.

    Ordering preference (highest first):
        info@ > sales@ > contact@ > hello@ > service@
        > other generics (support@, office@, team@, ...)
        > personal mailbox on the company's own / a custom domain
        > personal mailbox on free webmail (gmail/outlook/...)

    Junk addresses (noreply/no-reply/system/vendor/image-filename) are rejected.
    Pure — no network. ``company_name`` lets us mildly favour a domain whose
    tokens overlap the company name when scores would otherwise tie.
    """
    company_tokens = {tok for tok in re.split(r"[^a-z0-9]+", company_name.lower()) if len(tok) > 2}

    scored: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for raw in emails or []:
        email = (raw or "").strip().lower()
        if not email or email in seen:
            continue
        if _is_junk_email(email):
            continue
        seen.add(email)
        scored.append((-_email_score(email, company_tokens), len(scored), email))

    scored.sort(key=lambda item: (item[0], item[1]))
    return [email for _, _, email in scored]


def _email_score(email: str, company_tokens: set[str]) -> int:
    """0-100 desirability score for a (already non-junk) email."""
    local, domain = email.split("@", 1)
    base = local.split("+", 1)[0]  # strip plus-addressing for prefix matching

    # Base kept below the prefix+domain boosts so the company-token tiebreaker
    # has headroom before the 100 ceiling clamps it away.
    score = 30
    # Prefix preference — explicit ordered five get a big, descending boost.
    if base in GENERIC_PREFIX_RANK:
        score += 50 - (GENERIC_PREFIX_RANK.index(base) * 6)  # info=50 ... service=26
    elif base in OTHER_GENERIC_PREFIXES:
        score += 18
    elif any(base.startswith(p) for p in GENERIC_PREFIX_RANK + list(OTHER_GENERIC_PREFIXES)):
        score += 12
    else:
        # personal mailbox (jan.devries@, john@)
        score += 4

    # Domain quality.
    if domain in FREE_WEBMAIL_DOMAINS:
        score -= 22  # a human reads it, but it's weaker than an on-domain mailbox
    else:
        score += 10  # custom domain → likely the company's own
        # mild tie-breaker: company name tokens appear in the domain. We compare
        # by substring (not exact token equality) so a concatenated domain like
        # "bikecity" still matches the company tokens {"bike", "city"}.
        domain_bare = re.sub(r"[^a-z0-9]", "", domain.split(".", 1)[0])
        if company_tokens and any(tok in domain_bare for tok in company_tokens):
            score += 6

    return max(0, min(100, score))


def email_confidence(email: str, website_domain: str = "", company_name: str = "") -> int:
    """Public 0-100 confidence for a single chosen email.

    Higher when the mailbox is a generic alias on the site's own domain.
    Used by :func:`discover_contacts` to fill ``email_confidence``.
    """
    if not email or _is_junk_email(email):
        return 0
    company_tokens = {tok for tok in re.split(r"[^a-z0-9]+", company_name.lower()) if len(tok) > 2}
    score = _email_score(email, company_tokens)
    site_domain = re.sub(r"^www\.", "", (website_domain or "").lower())
    if site_domain and _email_domain(email) == site_domain:
        score = min(100, score + 12)  # mailbox is on the exact site domain
    return max(0, min(100, score))


# ──────────────────────────────────────────────────────────────────────────
# Phone extraction + NL normalization (pure)
# ──────────────────────────────────────────────────────────────────────────
def normalize_nl_phone(s: str) -> str:
    """Normalize a phone-like string to E.164-ish form, biased to NL.

    Rules:
      * Strip everything except digits and a single leading '+'.
      * ``+31...`` / ``0031...`` / ``31...`` (with NL national length) → ``+31...``.
      * A national ``0xxxxxxxxx`` (NL, 9 digits after the 0) → ``+31xxxxxxxxx``.
      * Anything already starting with ``+`` keeps its country code.
      * Returns "" if the result is too short to be a real phone (<8 digits).

    Pure / deterministic — heavily unit-tested.
    """
    raw = str(s or "").strip()
    if not raw:
        return ""
    has_plus = raw.lstrip().startswith("+")
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 8:
        return ""

    # 00 international prefix → treat as '+'
    if digits.startswith("00"):
        digits = digits[2:]
        has_plus = True
        if len(digits) < 8:
            return ""

    if has_plus:
        return "+" + digits

    # No plus sign — infer NL where the shape fits.
    if digits.startswith("31") and 11 <= len(digits) <= 12:
        # 31 + 9/10 national digits
        return "+" + digits
    if digits.startswith("0") and len(digits) == 10:
        # NL national format 0XXXXXXXXX → +31XXXXXXXXX
        return "+31" + digits[1:]
    if digits.startswith("0") and len(digits) == 11:
        # Some mobiles written 06-12345678 etc. drop the leading 0
        return "+31" + digits[1:]

    # Unknown country / already-internationalized without '+': leave as digits
    # but only if plausibly long enough to be E.164.
    if 8 <= len(digits) <= 15:
        return "+" + digits if len(digits) >= 11 else digits
    return ""


def extract_phones(text: str, country: str = "NL") -> list[str]:
    """Extract and normalize phone numbers from text (de-duplicated, in order).

    ``country`` currently only influences NL normalization (the only locale we
    special-case); other inputs still get generic E.164-ish cleanup. Pure.
    """
    source = str(text or "")
    if not source:
        return []
    out: list[str] = []
    seen: set[str] = set()
    normalizer = normalize_nl_phone  # only NL has a dedicated normalizer today
    for match in PHONE_CANDIDATE_PATTERN.finditer(source):
        normalized = normalizer(match.group(0)) if country.upper() == "NL" else _normalize_generic_phone(match.group(0))
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def _normalize_generic_phone(s: str) -> str:
    """Locale-agnostic cleanup for non-NL numbers."""
    raw = str(s or "").strip()
    has_plus = raw.lstrip().startswith("+")
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 8:
        return ""
    if digits.startswith("00"):
        return "+" + digits[2:]
    return "+" + digits if has_plus else digits


def rank_phones(phones: list[str], context_by_phone: dict[str, str] | None = None) -> list[str]:
    """Rank phones best-first, preferring contact/header/footer context and
    de-prioritising fax numbers.

    ``context_by_phone`` optionally maps a phone string to the surrounding text
    it was found in, so we can boost numbers near "contact"/"phone" and sink
    numbers near "fax". With no context map, ordering is stable (first-seen).
    Pure.
    """
    context_by_phone = context_by_phone or {}
    scored: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for raw in phones or []:
        phone = (raw or "").strip()
        if not phone or phone in seen:
            continue
        seen.add(phone)
        ctx = (context_by_phone.get(phone) or "").lower()
        score = 50
        if any(word in ctx for word in PHONE_GOOD_CONTEXT):
            score += 25
        if any(word in ctx for word in PHONE_BAD_CONTEXT):
            score -= 40  # fax — keep but push to the bottom
        # prefer internationalized numbers
        if phone.startswith("+"):
            score += 5
        scored.append((-score, len(scored), phone))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [phone for _, _, phone in scored]


def _phone_context_window(text: str, raw_phone: str, radius: int = 40) -> str:
    """Return the text immediately surrounding the first occurrence of raw_phone.

    Used to drive rank_phones() context scoring inside discover_contacts().
    Pure helper.
    """
    idx = text.find(raw_phone)
    if idx == -1:
        return ""
    start = max(0, idx - radius)
    end = min(len(text), idx + len(raw_phone) + radius)
    return text[start:end]


# ──────────────────────────────────────────────────────────────────────────
# Orchestration — discover_contacts
# ──────────────────────────────────────────────────────────────────────────
def discover_contacts(url: str, company_name: str = "") -> ContactResult:
    """Crawl a website's home + contact-ish pages and return best contacts.

    Steps:
      1. Fetch the home page (httpx → urllib → optional Playwright).
      2. Find contact/about/impressum links and fetch up to ``discovery_max_pages``.
      3. Extract + rank emails and phones across all fetched pages, tracking
         which page each best value came from (provenance).
      4. Fall back to ``email_guesser.best_guess(domain)`` (MX-validated
         ``info@domain``) when no on-page email was found.

    Always returns a :class:`ContactResult`; never raises into the caller.
    Confidence numbers are 0-100. ``website_confidence`` reflects whether we
    could actually fetch the site (100 reachable, 0 unreachable) — the
    orchestrator may overwrite it with the search-match confidence.
    """
    website = ensure_http_url(url)
    if not website:
        return ContactResult(status=STATUS_NO_WEBSITE)

    domain = _domain_of(website)
    result = ContactResult(website=website, website_domain=domain, status=STATUS_NO_CONTACTS)

    max_pages = int(_cfg("discovery_max_pages", 8))

    try:
        home_html = fetch_html(website)
    except Exception as exc:  # noqa: BLE001 — defensive; fetch_html already guards
        result.status = STATUS_ERROR
        result.error = str(exc)
        result.website_confidence = 0
        return result

    if not home_html:
        # Could not fetch anything — site unreachable. Try a pure MX guess so a
        # totally-unreachable but mail-enabled domain still yields an info@.
        result.website_confidence = 0
        _apply_email_guess_fallback(result, domain, company_name)
        result.status = STATUS_FOUND if result.email_public else STATUS_NO_CONTACTS
        return result

    result.website_confidence = 100  # we reached the site
    pages_scanned: list[str] = [website]

    # Collect per-page extraction so we keep provenance (source page).
    email_hits: list[tuple[str, str]] = []   # (email, source_page)
    phone_hits: list[tuple[str, str]] = []    # (raw_phone, source_page)
    phone_context: dict[str, str] = {}

    def _harvest(html: str, page_url: str) -> None:
        if not html:
            return
        text = main_text(html)
        # Emails: scan both the clean text and the raw HTML (mailto/obfuscation
        # often live in attributes the text extractor drops).
        for email in extract_emails(html) + extract_emails(text):
            email_hits.append((email, page_url))
        # Phones: scan the readable text (avoids matching asset hashes in HTML).
        for raw_phone in extract_phones(text, country="NL"):
            phone_hits.append((raw_phone, page_url))
            if raw_phone not in phone_context:
                phone_context[raw_phone] = _phone_context_window(text, raw_phone)

    _harvest(home_html, website)

    # Follow contact-ish links (bounded).
    for link in contact_page_links(home_html, website):
        if len(pages_scanned) >= max_pages:
            break
        if link in pages_scanned:
            continue
        sub_html = fetch_html(link)
        pages_scanned.append(link)
        _harvest(sub_html, link)

    result.pages_scanned = pages_scanned

    # ── Rank emails, keep provenance ────────────────────────────────────────
    source_by_email: dict[str, str] = {}
    for email, page_url in email_hits:
        source_by_email.setdefault(email, page_url)
    ranked_emails = rank_email([e for e, _ in email_hits], company_name)
    result.emails_found = ranked_emails

    if ranked_emails:
        best_email = ranked_emails[0]
        result.email_public = best_email
        result.email_source_page = source_by_email.get(best_email, website)
        result.email_confidence = email_confidence(best_email, domain, company_name)
    else:
        _apply_email_guess_fallback(result, domain, company_name)

    # ── Rank phones, keep provenance ────────────────────────────────────────
    source_by_phone: dict[str, str] = {}
    for raw_phone, page_url in phone_hits:
        normalized = normalize_nl_phone(raw_phone)
        if normalized and normalized not in source_by_phone:
            source_by_phone[normalized] = page_url
            # carry context forward under the normalized key for ranking
            phone_context.setdefault(normalized, phone_context.get(raw_phone, ""))
    ranked_phones = rank_phones(list(source_by_phone.keys()), phone_context)
    result.phones_found = ranked_phones

    if ranked_phones:
        best_phone = ranked_phones[0]
        result.phone_public = best_phone
        result.phone_source_page = source_by_phone.get(best_phone, website)
        # crude confidence: contact-context numbers score higher
        ctx = (phone_context.get(best_phone) or "").lower()
        result.phone_confidence = 80 if any(w in ctx for w in PHONE_GOOD_CONTEXT) else 60

    # ── Final status ────────────────────────────────────────────────────────
    if result.email_public:
        result.status = STATUS_FOUND
    elif result.phone_public:
        result.status = STATUS_PARTIAL
    else:
        result.status = STATUS_NO_CONTACTS
    return result


def _apply_email_guess_fallback(result: ContactResult, domain: str, company_name: str) -> None:
    """Fill the email via the MX-validated info@domain guesser when none found.

    Lazy/guarded import so a missing dnspython or guesser never breaks discovery.
    """
    if not domain:
        return
    try:
        from app.email_guesser import best_guess  # local import: keeps module light
    except Exception:
        return
    try:
        guessed = best_guess(domain, require_mx=True)
    except Exception as exc:  # noqa: BLE001
        logger.info("web_extract: email guess failed for %s: %s", domain, exc)
        guessed = None
    if guessed and guessed.email and not _is_junk_email(guessed.email):
        result.email_public = guessed.email
        result.email_source_page = f"pattern:{guessed.pattern}@ (MX:{guessed.mx_host[:40]})"
        # Guessed addresses are inherently less certain than scraped ones.
        result.email_confidence = min(int(getattr(guessed, "confidence", 60)), 75)
        if guessed.email not in result.emails_found:
            result.emails_found = [guessed.email, *result.emails_found]
