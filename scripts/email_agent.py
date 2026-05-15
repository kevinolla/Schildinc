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
    "sentry.io", "wixsite.com", "shopify.com", "mailchimp.com",
    "klaviyo.com", "google.com", "googlemail.com", "wordpress.com",
    "example.com", "example.org", "domain.com", "yourdomain.com",
    "gmail.com", "hotmail.com", "outlook.com", "yahoo.com",
    "ziggo.nl", "kpnmail.nl", "live.nl", "icloud.com",
    "facebook.com", "instagram.com", "linkedin.com", "youtube.com",
    "duckduckgo.com", "bing.com",
}
GENERIC_LOCALS = {"info", "contact", "hello", "sales", "verkoop", "winkel",
                  "shop", "klantenservice", "office"}


def auth_header() -> dict[str, str]:
    raw = f"{USERNAME}:{PASSWORD}".encode()
    return {"Authorization": "Basic " + b64encode(raw).decode()}


def api_get(path: str, params: dict[str, Any] | None = None) -> Any:
    url = f"{API_BASE}{path}"
    if params:
        url += "?" + urlencode(params)
    req = Request(url, headers=auth_header())
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_post(path: str, data: dict[str, str]) -> Any:
    body = urlencode(data).encode()
    req = Request(
        f"{API_BASE}{path}",
        data=body,
        method="POST",
        headers={**auth_header(), "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(req, timeout=15) as resp:
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
    """Pick best email for the company by name-token overlap with domain."""
    if not candidates:
        return ""
    name = (company_name or "").lower()
    name_tokens = {t for t in re.split(r"[^a-z0-9]+", name) if len(t) >= 3}
    name_tokens -= {"the", "van", "de", "het", "een", "and", "fiets", "fietsen",
                    "bike", "bikes", "store", "shop"}
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
    return best if best_score >= 20 else ""


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


def search_one(page, company_name: str, city: str, debug: bool = False) -> tuple[str, str]:
    """
    Open Google in the Playwright page, search "{name}" {city} email,
    return (email, source_url).

    Aggressively waits for late-rendered content (AI Overview takes
    several seconds to appear after initial load) and polls the page
    multiple times in case more results stream in.

    Returns:
      (email, website_url) — match found
      ("", "")             — searched OK but no email in snippet
      (CAPTCHA_BLOCKED, "")— Google challenged us; caller should pause
    """
    query = f'"{company_name}" {city} email'.strip()
    url = "https://www.google.com/search?q=" + urlencode({"q": query})[2:]
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        if is_captcha_page(page):
            return CAPTCHA_BLOCKED, ""

        # Wait for main results container
        try:
            page.wait_for_selector("div#search, div#rso, div#main", timeout=8000)
        except Exception:
            pass
        # Wait for network quiet — AI Overview loads via XHR after this
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        if is_captcha_page(page):
            return CAPTCHA_BLOCKED, ""

        # Trigger lazy-loaded widgets (AI Overview, "More results"
        # cards, Knowledge Panel) by scrolling down — Google won't
        # render them for a "user" who never moved the page.
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            page.wait_for_timeout(1200)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(400)
        except Exception:
            pass

        # Multi-pass extraction: try every 1.5s up to 4 times so AI
        # Overview / Knowledge Panel content that streams in late still
        # gets captured. Stop early on the first valid match.
        best, website, candidates, full_text = "", "", [], ""
        for attempt in range(4):
            best, website, candidates, full_text = _attempt_extract(page, company_name)
            if best:
                return best, website
            try:
                page.wait_for_timeout(1500)
            except Exception:
                break

        # Nothing found after 4 polls + scroll
        if debug:
            sample = full_text[:800].replace("\n", " ")
            print()
            print(f"     DEBUG (no email found). Page text sample (first 800 chars):")
            print(f"     {sample!r}")
            if candidates:
                print(f"     Raw candidates that didn't pass ranking: {candidates[:8]}")
            else:
                print(f"     No emails matched in extracted text at all.")
        return "", ""
    except Exception as exc:
        print(f"  ! search error: {exc}")
        return "", ""


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
    ap.add_argument("--delay", type=float, default=2.0, help="seconds between searches")
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
                    email, website = "", ""
                    while True:
                        email, website = search_one(page, name, city, debug=args.debug)
                        if email == CAPTCHA_BLOCKED:
                            if captcha_retries_left <= 0:
                                # Give up on this record without poisoning it —
                                # leaves status alone so it stays in /agent/pending
                                if not args.quiet:
                                    print("⚠ still blocked, skipping (record left pending)")
                                captcha_count += 1
                                skipped_due_to_captcha = True
                                email, website = "", ""
                                break
                            captcha_retries_left -= 1
                            print()  # break the trailing "…"
                            wait_for_human(page, label=f"Google CAPTCHA on #{cid} {name}")
                            print(f"  retrying #{cid} {name} … ", end="", flush=True)
                            continue
                        break

                    processed += 1

                    if skipped_due_to_captcha:
                        # Don't write back — record stays in /agent/pending for retry
                        time.sleep(args.delay)
                        continue

                    # Interactive fallback: if extraction missed but the
                    # user CAN see an email in the visible Chrome window,
                    # let them paste it. Confidence stays "high" since
                    # they verified it personally.
                    manual_source = "browser_agent"
                    if not email and args.interactive:
                        print("✗ not auto-detected", end="", flush=True)
                        typed = prompt_manual_email(name, city)
                        if typed:
                            email = typed
                            _, _, dom = typed.partition("@")
                            website = f"https://{dom}"
                            manual_source = "browser_agent_manual"

                    if email:
                        result = api_post("/api/kvk/agent/result", {
                            "company_id": str(cid),
                            "email": email,
                            "website": website,
                            "source": manual_source,
                            "confidence": "high",
                        })
                        if result.get("ok"):
                            found_count += 1
                            tag = " (manual)" if manual_source.endswith("_manual") else ""
                            if not args.quiet:
                                print(f"✓ {email}{tag}")
                        else:
                            print(f"✗ save failed: {result}")
                    else:
                        # Mark as no-result so we don't re-process it next batch
                        api_post("/api/kvk/agent/result", {
                            "company_id": str(cid),
                            "email": "",
                            "source": "browser_agent",
                            "note": "no_email_in_snippet",
                        })
                        miss_count += 1
                        if not args.quiet:
                            print("✗ not found")

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
