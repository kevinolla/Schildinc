#!/usr/bin/env python3
"""
Local Browser Email Agent
=========================
Runs on YOUR laptop (residential IP, not Railway). Pulls pending KVK
companies from the production API, opens Google in a real Chrome
window, extracts the email from the search-results snippet, then
posts the result back to the production database.

Why this works when the cloud crawler can't:
- Google/Bing return rich snippets to residential browsers but strip
  them for cloud-host IPs. By running on your machine we get the same
  snippets you'd see typing the query manually in Chrome.

Usage:
    cd "/Users/kevinolla/AI Project/B2B Prospect tool"
    source .venv/bin/activate                  # one-time
    python scripts/email_agent.py              # run

Optional flags:
    --batch 25     # how many records to process per fetch (default 25)
    --headless     # hide the browser (default: visible so you can watch)
    --quiet        # less log output
    --max 100      # stop after N records (default: keep going forever)

Press Ctrl-C any time to stop. Records you've already resolved stay
saved on the server.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from base64 import b64encode
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# ── Config — edit these once ────────────────────────────────────────────────
API_BASE = "https://schild-prospect-engine-production.up.railway.app"
USERNAME = "schild"
PASSWORD = "Schildinc#01"

# ── Email regex + filters (mirror server logic) ─────────────────────────────
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", re.I)
# Common obfuscations — info [at] domain [dot] nl, info(at)domain.nl, etc.
OBFUSCATED_AT = r"\s*(?:\[\s*at\s*\]|\(\s*at\s*\)|\s+at\s+|\{\s*at\s*\}|@)\s*"
OBFUSCATED_DOT = r"\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\s+dot\s+|\{\s*dot\s*\}|\.)\s*"
OBFUSCATED_EMAIL_RE = re.compile(
    rf"([A-Za-z0-9._%+\-]+){OBFUSCATED_AT}([A-Za-z0-9\-]+){OBFUSCATED_DOT}([A-Za-z]{{2,8}})",
    re.I,
)
REJECT_LOCAL = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
    "support-ticket", "webmaster", "postmaster", "abuse", "admin",
}
REJECT_DOMAINS = {
    # Tracking / vendor noise — never a real customer email
    "sentry.io", "wixsite.com", "shopify.com", "mailchimp.com",
    "klaviyo.com", "google.com", "googlemail.com", "wordpress.com",
    "example.com", "example.org", "domain.com", "yourdomain.com",
    "facebook.com", "instagram.com", "linkedin.com", "youtube.com",
    "duckduckgo.com", "bing.com", "schildinc.com",
    # NOTE: free-webmail addresses (gmail/hotmail/outlook/ziggo/kpnmail)
    # used to be rejected. Keeping them now because plenty of small
    # Dutch shops actually do use info@something@gmail.com as their
    # real contact address, and the user wants ANY plausible email.
}
GENERIC_LOCALS = {"info", "contact", "hello", "sales", "verkoop", "winkel",
                  "shop", "klantenservice", "office"}

# ── Phone / WhatsApp / Social regex ─────────────────────────────────────────
# Dutch phones come in many shapes: 0612345678, 020-1234567, +31 6 12 34 56 78
# Match anything with the right digit count after stripping spaces/punct.
PHONE_RE = re.compile(
    r"(?:\+?31[\s\-\.]?|0)(?:\d[\s\-\.]?){8,10}\d"
)
# WhatsApp URLs (wa.me, api.whatsapp.com) — caller phone is in the URL
WHATSAPP_URL_RE = re.compile(
    r"https?://(?:wa\.me/\+?\d[\d\-]{6,}|api\.whatsapp\.com/send\?[^\s\"'<>]+)",
    re.I,
)
INSTAGRAM_URL_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?!p/|reel/|stories/|explore/|accounts/|share/)([A-Za-z0-9._\-]+)/?",
    re.I,
)
LINKEDIN_URL_RE = re.compile(
    r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in|showcase|school)/[A-Za-z0-9._\-%]+",
    re.I,
)


def auth_header() -> dict[str, str]:
    raw = f"{USERNAME}:{PASSWORD}".encode()
    return {"Authorization": "Basic " + b64encode(raw).decode()}


def api_get(path: str, params: dict[str, Any] | None = None, timeout: int = 45) -> Any:
    url = f"{API_BASE}{path}"
    if params:
        url += "?" + urlencode(params)
    req = Request(url, headers=auth_header())
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_post(path: str, data: dict[str, str], timeout: int = 45) -> Any:
    body = urlencode(data).encode()
    req = Request(
        f"{API_BASE}{path}",
        data=body,
        method="POST",
        headers={**auth_header(), "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        return {"ok": False, "error": f"HTTP {exc.code}", "body": exc.read().decode("utf-8", errors="replace")[:200]}
    except URLError as exc:
        return {"ok": False, "error": f"URLError: {exc}"}


def deobfuscate_emails(text: str) -> str:
    """
    Reassemble obfuscated emails like `info [at] example [dot] nl`,
    `info(at)example.nl`, `info {at} example {dot} nl` into normal
    `info@example.nl` form so the main regex picks them up.

    Returns a string of reassembled emails joined by spaces — meant to
    be appended to the regular text blob before email extraction.
    """
    if not text:
        return ""
    out: list[str] = []
    for m in OBFUSCATED_EMAIL_RE.finditer(text):
        local = m.group(1).strip(".-_")
        domain = m.group(2).strip(".-_")
        tld = m.group(3).strip(".-_")
        if local and domain and tld:
            out.append(f"{local}@{domain}.{tld}")
    return " ".join(out)


def filter_emails(text: str) -> list[str]:
    """Run regex over text, drop noise, return ranked list (best first)."""
    # First reassemble any obfuscated `[at]` / `[dot]` style addresses
    text = (text or "") + " " + deobfuscate_emails(text or "")
    seen: set[str] = set()
    found: list[str] = []
    for em in EMAIL_RE.findall(text or ""):
        em = em.strip(".,;:!?\"')(<>").lower()
        if em in seen:
            continue
        seen.add(em)
        local, _, dom = em.partition("@")
        if not dom or "." not in dom or len(local) < 2 or local.isdigit():
            continue
        if local in REJECT_LOCAL:
            continue
        if any(dom == d or dom.endswith("." + d) for d in REJECT_DOMAINS):
            continue
        if any(local.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".svg")):
            continue
        found.append(em)
    return found


def rank_emails(candidates: list[str], company_name: str) -> str:
    """
    Pick best email for the company. Aggressive mode: if any candidate
    survives the noise filter we return the highest-scoring one — even
    without name-token overlap. The noise filter already rejects
    no-reply / vendor / placeholder addresses, so anything left is a
    real business email that's worth saving.

    Scoring (higher = better):
      + name-token overlap with email domain — strong signal it's right
      + generic local part (info@, contact@, sales@)
      + .nl TLD (we're targeting Dutch businesses)
    """
    if not candidates:
        return ""
    name = (company_name or "").lower()
    name_tokens = {t for t in re.split(r"[^a-z0-9]+", name) if len(t) >= 3}
    name_tokens -= {"the", "van", "de", "het", "een", "and", "fiets", "fietsen",
                    "bike", "bikes", "store", "shop", "b.v.", "bv", "v.o.f.",
                    "vof", "rijwiel", "rijwielen"}
    best, best_score = "", -1
    for em in candidates:
        local, _, dom = em.partition("@")
        score = 0
        if local in GENERIC_LOCALS:
            score += 30
        dom_tokens = {t for t in re.split(r"[^a-z0-9]+", dom.replace(".", " ")) if len(t) >= 3}
        overlap = name_tokens & dom_tokens
        if overlap:
            score += 40 + min(20, 10 * len(overlap))
        if dom.endswith(".nl"):
            score += 8
        if score > best_score:
            best_score, best = score, em
    # No threshold — return whatever survived the noise filter. The
    # vendor / no-reply / free-webmail rejection in filter_emails() is
    # already strict enough; anything left is a real biz email.
    return best


# Sentinel returned by search_one when Google challenges with a CAPTCHA.
# Caller treats this differently from "not found" — it skips the record
# without poisoning its server-side status, so we can retry it later.
CAPTCHA_BLOCKED = "__CAPTCHA__"

# Things that signal Google is challenging us, not serving real results
_CAPTCHA_URL_FRAGMENTS = ("/sorry/", "google.com/sorry", "consent.google")
_CAPTCHA_TEXT_FRAGMENTS = (
    "ongebruikelijk verkeer",        # NL: unusual traffic
    "unusual traffic",
    "i'm not a robot",
    "captcha",
    "before you continue to google",
    "voordat je verdergaat",         # NL: consent gate
)


def is_captcha_page(page) -> bool:
    """Detect Google's bot-challenge / consent walls."""
    try:
        url = (page.url or "").lower()
        if any(fr in url for fr in _CAPTCHA_URL_FRAGMENTS):
            return True
        body = (page.evaluate("document.body ? document.body.innerText.slice(0, 800) : ''") or "").lower()
        return any(fr in body for fr in _CAPTCHA_TEXT_FRAGMENTS)
    except Exception:
        return False


def wait_for_human(page, label: str = "Google CAPTCHA") -> None:
    """
    Pause the script and wait for the user to solve the challenge in the
    visible browser window. They press Enter in the terminal once they're
    back on a normal Google search page.
    """
    print()
    print("=" * 60)
    print(f"  ⚠  {label} detected.")
    print(f"  → In the Chrome window: solve the challenge, then return.")
    print(f"  → Press Enter here when you're past the challenge.")
    print("=" * 60)
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        raise
    # Give Google a couple of seconds after the user navigates back
    try:
        page.wait_for_timeout(1500)
    except Exception:
        pass


# JS that walks the entire DOM including shadow roots and grabs every
# text node. Google AI Overview embeds content inside open shadow DOM
# that document.body.innerText doesn't pierce.
_DEEP_TEXT_JS = r"""
(() => {
  const out = [];
  function walk(node) {
    if (!node) return;
    if (node.nodeType === 3) {                      // text node
      const t = (node.textContent || '').trim();
      if (t) out.push(t);
      return;
    }
    if (node.shadowRoot) walk(node.shadowRoot);
    // pick up href / aria-label / title / placeholder attrs that
    // sometimes hold the email
    if (node.attributes) {
      for (const a of node.attributes) {
        const v = (a.value || '').trim();
        if (v && (v.includes('@') || v.startsWith('mailto:'))) out.push(v);
      }
    }
    let c = node.firstChild;
    while (c) { walk(c); c = c.nextSibling; }
  }
  walk(document);
  return out.join(' ');
})()
"""


def _collect_text_from_page(page) -> str:
    """
    Pull every kind of text the user actually sees in the rendered Google
    results page — including shadow DOM (AI Overview), iframes, anchor
    hrefs, and attribute values.
    """
    parts: list[str] = []
    # 1. Deep walk: body.innerText + shadow DOM + attribute values
    try:
        deep = page.evaluate(_DEEP_TEXT_JS) or ""
        parts.append(deep)
    except Exception:
        pass
    # 2. Plain body.innerText as backup
    try:
        body = page.evaluate("document.body ? document.body.innerText : ''") or ""
        parts.append(body)
    except Exception:
        pass
    try:
        hrefs = page.evaluate(
            "Array.from(document.querySelectorAll('a')).map(a => a.href || '').join(' ')"
        ) or ""
        parts.append(hrefs)
    except Exception:
        pass
    # 3. Iframes (AI Overview sometimes lives in one)
    try:
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                ft = frame.evaluate("document.body ? document.body.innerText : ''") or ""
                if ft:
                    parts.append(ft)
            except Exception:
                pass
    except Exception:
        pass
    # 4. Final fallback: raw HTML (catches emails in tag attributes /
    # data-cfemail / structured data even when innerText doesn't surface them)
    try:
        parts.append(page.content() or "")
    except Exception:
        pass
    return " ".join(parts)


def _normalize_phone(raw: str) -> str:
    """Normalize a Dutch phone string. Returns '' if it doesn't look real."""
    if not raw:
        return ""
    digits = re.sub(r"[^\d+]", "", raw)
    # Reject too short / too long
    if not digits:
        return ""
    # Accept +31xxxxxxxxx or 0xxxxxxxxx with 9-10 trailing digits
    if digits.startswith("+31"):
        return digits if 11 <= len(digits) <= 13 else ""
    if digits.startswith("31") and len(digits) in (11, 12):
        return "+" + digits
    if digits.startswith("0") and 10 <= len(digits) <= 11:
        return digits
    return ""


def _extract_phone(text: str) -> str:
    """Find the first plausible Dutch phone number in text."""
    if not text:
        return ""
    for m in PHONE_RE.finditer(text):
        normalized = _normalize_phone(m.group(0))
        if normalized:
            return normalized
    return ""


def _extract_whatsapp(text: str) -> tuple[str, str]:
    """
    Find first WhatsApp URL + extract the embedded phone.
    Returns (number, url), either may be empty.
    """
    if not text:
        return "", ""
    m = WHATSAPP_URL_RE.search(text)
    if not m:
        return "", ""
    url = m.group(0).strip(".,;'\")")
    # Extract phone from wa.me/<phone> or api.whatsapp.com/send?phone=<phone>
    num = ""
    wa_phone = re.search(r"wa\.me/\+?(\d{8,})", url)
    if wa_phone:
        num = "+" + wa_phone.group(1)
    else:
        api_phone = re.search(r"[?&]phone=\+?(\d{8,})", url)
        if api_phone:
            num = "+" + api_phone.group(1)
    return num, url


def _extract_instagram(text: str) -> str:
    """First Instagram profile URL (excludes posts/reels/stories)."""
    if not text:
        return ""
    m = INSTAGRAM_URL_RE.search(text)
    if not m:
        return ""
    handle = m.group(1).strip("._-").lower()
    if not handle or handle in {"explore", "accounts", "p", "reel", "stories", "share"}:
        return ""
    return f"https://www.instagram.com/{handle}/"


def _extract_linkedin(text: str) -> str:
    """First LinkedIn company / person / school URL."""
    if not text:
        return ""
    m = LINKEDIN_URL_RE.search(text)
    if not m:
        return ""
    return m.group(0).strip(".,;'\")")


def _attempt_extract(page, company_name: str) -> tuple[str, str, list[str], str]:
    """One extraction pass. Returns (best_email, website, all_candidates, full_text)."""
    full_text = _collect_text_from_page(page)
    candidates = filter_emails(full_text)
    best = rank_emails(candidates, company_name)
    website = ""
    if best:
        _, _, dom = best.partition("@")
        website = f"https://{dom}"
    return best, website, candidates, full_text


def _empty_result() -> dict:
    """Default empty result dict."""
    return {
        "email": "", "website": "", "phone": "",
        "whatsapp_number": "", "whatsapp_url": "",
        "instagram_url": "", "linkedin_url": "",
    }


def _do_google_query(page, query: str) -> tuple[str, str]:
    """
    Navigate to Google with `query`, wait for results to render
    (including scroll-triggered AI Overview / Knowledge Panel),
    return (full_extracted_text, status). status is "" on success,
    "captcha" if Google challenged us, "error" on exceptions.
    """
    url = "https://www.google.com/search?q=" + urlencode({"q": query})[2:]
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        if is_captcha_page(page):
            return "", "captcha"
        try:
            page.wait_for_selector("div#search, div#rso, div#main", timeout=8000)
        except Exception:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        if is_captcha_page(page):
            return "", "captcha"
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            page.wait_for_timeout(1000)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(400)
        except Exception:
            pass
        # One more wait for late-arriving widgets
        try:
            page.wait_for_timeout(800)
        except Exception:
            pass
        return _collect_text_from_page(page), ""
    except Exception as exc:
        print(f"  ! query error '{query[:40]}…': {exc}")
        return "", "error"


def _merge_extracted(result: dict, text: str, company_name: str) -> None:
    """
    In-place merge: for every empty field in `result`, try to extract
    that channel from `text`. Fields that already have a value are
    left alone — we never overwrite an earlier hit.
    """
    if not text:
        return
    if not result["email"]:
        candidates = filter_emails(text)
        best = rank_emails(candidates, company_name)
        if best:
            result["email"] = best
            if not result["website"]:
                _, _, dom = best.partition("@")
                result["website"] = f"https://{dom}"
    if not result["phone"]:
        result["phone"] = _extract_phone(text)
    if not result["whatsapp_number"]:
        wa_num, wa_url = _extract_whatsapp(text)
        if wa_num or wa_url:
            result["whatsapp_number"] = wa_num
            result["whatsapp_url"] = wa_url
    if not result["instagram_url"]:
        result["instagram_url"] = _extract_instagram(text)
    if not result["linkedin_url"]:
        result["linkedin_url"] = _extract_linkedin(text)


def search_one(page, company_name: str, city: str, debug: bool = False) -> dict | str:
    """
    Run a targeted Google search per missing channel and aggregate the
    results. The query keyword steers Google's snippet selection — a
    'phone' query surfaces the Knowledge Panel sidebar with phone +
    socials, while an 'email' query surfaces the contact-page snippet.

    Returns:
      dict with {email, website, phone, whatsapp_number, whatsapp_url,
                 instagram_url, linkedin_url} — any field can be ""
      CAPTCHA_BLOCKED string when Google challenges us (caller pauses)

    Per-channel early-stop: skips queries we no longer need because
    the previous query already filled that channel.
    """
    result = _empty_result()

    # Run ALL targeted queries every record — no per-channel skip. Each
    # query's keyword steers Google toward a different snippet pattern,
    # so even when the email query already filled some fields, the
    # follow-ups regularly surface phones / socials we'd otherwise miss.
    base = f'"{company_name}" {city}'.strip()
    plan = [
        f'{base} email',                  # contact pages with email
        f'{base} telefoon contact',       # NL "telephone" — Knowledge Panel
        f'{base} instagram',              # IG profile
        f'{base} linkedin',               # LI page
    ]

    last_text = ""
    for query in plan:
        text, status = _do_google_query(page, query)
        if status == "captcha":
            return CAPTCHA_BLOCKED
        last_text = text or last_text
        if text:
            # _merge_extracted is no-clobber — it fills empty fields
            # while leaving already-found values untouched. So running
            # every query unconditionally still yields the same data
            # as the first hit for each channel; we just see more of
            # the channels light up.
            _merge_extracted(result, text, company_name)

    if debug and not any(result.values()):
        sample = (last_text or "")[:800].replace("\n", " ")
        print()
        print(f"     DEBUG (no contacts found after {len(plan)} queries). Last page sample:")
        print(f"     {sample!r}")

    return result


def prompt_manual_email(company_name: str, city: str) -> str:
    """
    Interactive fallback: when the script can't extract an email but the
    user CAN see one in the browser window, let them paste it. Returns
    the typed email, or "" to skip.
    """
    print()
    print("-" * 60)
    print(f"  Can you see an email for `{company_name}` ({city or '-'}) in the browser?")
    print(f"  → Paste the email and press Enter to save it.")
    print(f"  → Press Enter alone to skip this record.")
    print(f"  → Type 'q' to quit the agent.")
    try:
        raw = input("  email: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        raise
    if raw in ("q", "quit", "exit"):
        raise KeyboardInterrupt()
    if not raw:
        return ""
    if "@" not in raw or "." not in raw.split("@", 1)[1]:
        print(f"  ! '{raw}' doesn't look like an email. Skipping.")
        return ""
    return raw


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=25)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--max", type=int, default=0, help="stop after N records (0 = forever)")
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between searches")
    ap.add_argument("--debug", action="store_true",
                    help="when no email is found, print a sample of the page text")
    ap.add_argument("--interactive", action="store_true",
                    help="on miss, pause and let you type the email you see in the browser")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: Playwright not installed. Run:")
        print("  pip install playwright && playwright install chromium")
        return 1

    print(f"=== Local Browser Email Agent ===")
    print(f"  API: {API_BASE}")
    print(f"  Batch: {args.batch}, Delay: {args.delay}s, Headless: {args.headless}")
    print()

    processed = 0
    found_count = 0
    miss_count = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=args.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
            locale="nl-NL",
        )
        page = ctx.new_page()

        try:
            while True:
                if args.max and processed >= args.max:
                    print(f"\nReached --max={args.max}, stopping.")
                    break

                # Fetch next batch from the API
                try:
                    pending = api_get("/api/kvk/agent/pending", {"limit": args.batch})
                except Exception as exc:
                    print(f"! API fetch failed: {exc}. Sleeping 30s.")
                    time.sleep(30)
                    continue

                if not pending:
                    print("\nNo more pending records. Done!")
                    break

                if not args.quiet:
                    print(f"--- Got {len(pending)} pending records ---")

                captcha_count = 0
                for rec in pending:
                    if args.max and processed >= args.max:
                        break
                    cid = rec["id"]
                    name = rec["company_name"]
                    city = rec["city"]
                    if not args.quiet:
                        print(f"  [{processed+1}] #{cid} {name} ({city or '-'}) … ", end="", flush=True)

                    # Inner retry loop — handles CAPTCHA challenges by pausing
                    # for the user, then retrying the SAME record once.
                    captcha_retries_left = 1
                    skipped_due_to_captcha = False
                    extract: dict = _empty_result()
                    while True:
                        out = search_one(page, name, city, debug=args.debug)
                        if out == CAPTCHA_BLOCKED:
                            if captcha_retries_left <= 0:
                                if not args.quiet:
                                    print("⚠ still blocked, skipping (record left pending)")
                                captcha_count += 1
                                skipped_due_to_captcha = True
                                break
                            captcha_retries_left -= 1
                            print()
                            wait_for_human(page, label=f"Google CAPTCHA on #{cid} {name}")
                            print(f"  retrying #{cid} {name} … ", end="", flush=True)
                            continue
                        extract = out  # type: ignore[assignment]
                        break

                    processed += 1

                    if skipped_due_to_captcha:
                        time.sleep(args.delay)
                        continue

                    # Interactive fallback: if extraction missed email but
                    # user can see one, paste it manually
                    manual_source = "browser_agent"
                    if not extract["email"] and args.interactive:
                        print("✗ not auto-detected", end="", flush=True)
                        typed = prompt_manual_email(name, city)
                        if typed:
                            extract["email"] = typed
                            _, _, dom = typed.partition("@")
                            extract["website"] = f"https://{dom}"
                            manual_source = "browser_agent_manual"

                    found_any = any([
                        extract["email"], extract["phone"],
                        extract["whatsapp_number"], extract["whatsapp_url"],
                        extract["instagram_url"], extract["linkedin_url"],
                    ])

                    # Always post — even an empty result marks the record
                    # as "checked but nothing useful found"
                    payload = {
                        "company_id": str(cid),
                        "email": extract["email"],
                        "website": extract["website"],
                        "phone": extract["phone"],
                        "whatsapp_number": extract["whatsapp_number"],
                        "whatsapp_url": extract["whatsapp_url"],
                        "instagram_url": extract["instagram_url"],
                        "linkedin_url": extract["linkedin_url"],
                        "source": manual_source,
                        "confidence": "high",
                    }
                    if not found_any:
                        payload["note"] = "no_contacts_in_snippet"

                    result = api_post("/api/kvk/agent/result", payload)

                    if not result.get("ok"):
                        print(f"  ✗ save failed: {result}")
                        time.sleep(args.delay)
                        continue

                    # Format compact one-line summary of what we got
                    bits = []
                    if extract["email"]:
                        tag = " (M)" if manual_source.endswith("_manual") else ""
                        bits.append(f"📧 {extract['email']}{tag}")
                    if extract["phone"]:
                        bits.append(f"📞 {extract['phone']}")
                    if extract["whatsapp_number"]:
                        bits.append(f"💬 {extract['whatsapp_number']}")
                    if extract["instagram_url"]:
                        bits.append("📷")
                    if extract["linkedin_url"]:
                        bits.append("💼")

                    if found_any:
                        found_count += 1
                        if not args.quiet:
                            print("✓ " + "  ".join(bits))
                    else:
                        miss_count += 1
                        if not args.quiet:
                            print("✗ nothing found")

                    time.sleep(args.delay)

                print(f"\n  >>> Progress: {processed} processed, {found_count} found, {miss_count} miss, {captcha_count} captcha-skipped")

                # If CAPTCHA hit ratio is high, slow down between batches
                if captcha_count >= 3:
                    cool = max(60, args.delay * 30)
                    print(f"  >>> Many CAPTCHAs in this batch — cooling down for {cool}s before next batch")
                    time.sleep(cool)

        except KeyboardInterrupt:
            print(f"\n\n[interrupted] Processed {processed} records, saved {found_count} emails.")
        finally:
            browser.close()

    print(f"\n=== Done ===")
    print(f"  Processed: {processed}")
    print(f"  Emails found: {found_count}  ({100*found_count/max(1,processed):.0f}% hit rate)")
    print(f"  Misses: {miss_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
