"""
Playwright-based Google search snippet scraper
==============================================
Why this exists: bare urllib hits to Google/Bing from cloud IPs (Railway)
get either a CAPTCHA stub or a JS-only SPA shell — no usable snippets.
A real headless Chromium with proper viewport + UA + locale gets the
same rendered DOM a human sees in Chrome, including the meta-description
text where emails appear.

Slow path (~4-8s per query because of browser launch + page load) so
it's intended as a per-record fallback, not a per-query default.

Used by `_emails_from_snippets` in `kvk_enrichment.py`.
"""
from __future__ import annotations

import random
import re
from urllib.parse import quote_plus

from playwright.sync_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

# Real-browser UAs for Chromium-flavored browsers — Google fingerprints UA
# strings tightly so anything that smells synthetic gets a CAPTCHA.
_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

_GOOGLE_RESULT_SELECTOR = "div#search, div#rso, div.g, div[data-async-context]"

# Per-call hard cap so a slow Google response can never freeze a worker
# longer than this. 12s = launch (~2s) + goto (~5s) + render (~3s) + headroom.
_PAGE_TIMEOUT_MS = 12_000


def is_enabled() -> bool:
    """Always available — Playwright is a hard dependency of the project."""
    return True


def google_snippet_text(query: str) -> str:
    """
    Open Google in a headless Chromium, run the query, return the rendered
    page text (visible body innerText). Empty string on any failure —
    caller is expected to fall through gracefully.
    """
    if not (query or "").strip():
        return ""

    url = f"https://www.google.com/search?q={quote_plus(query)}&hl=nl&gl=nl"
    ua = random.choice(_USER_AGENTS)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            try:
                ctx = browser.new_context(
                    user_agent=ua,
                    locale="nl-NL",
                    viewport={"width": 1366, "height": 800},
                    java_script_enabled=True,
                )
                page = ctx.new_page()
                page.set_default_timeout(_PAGE_TIMEOUT_MS)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS)
                except PlaywrightTimeoutError:
                    return ""
                # Wait briefly for results to render; ignore if selector not present
                try:
                    page.wait_for_selector(_GOOGLE_RESULT_SELECTOR, timeout=4_000)
                except PlaywrightTimeoutError:
                    pass

                # Detect CAPTCHA / consent wall — bail without burning more time
                page_text_short = ""
                try:
                    page_text_short = page.evaluate(
                        "document.body ? document.body.innerText.slice(0, 600) : ''"
                    ) or ""
                except PlaywrightError:
                    return ""
                low = page_text_short.lower()
                if any(flag in low for flag in [
                    "ongebruikelijk verkeer",         # NL: unusual traffic
                    "unusual traffic",
                    "before you continue",
                    "voordat je verdergaat",          # NL: consent gate
                    "i'm not a robot",
                    "captcha",
                ]):
                    return ""

                # Pull the full rendered text — cheaper than full HTML and
                # contains the snippets we care about
                try:
                    body_text = page.evaluate(
                        "document.body ? document.body.innerText : ''"
                    ) or ""
                except PlaywrightError:
                    body_text = ""

                # Also pull all anchor hrefs — emails sometimes only appear
                # as `mailto:` links that don't show in innerText
                try:
                    hrefs = page.evaluate(
                        "Array.from(document.querySelectorAll('a')).map(a => a.href).join(' ')"
                    ) or ""
                except PlaywrightError:
                    hrefs = ""

                return body_text + " " + hrefs
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except PlaywrightError as exc:
        print(f"[playwright-search] Playwright error for query={query!r}: {exc}")
        return ""
    except Exception as exc:
        print(f"[playwright-search] Error for query={query!r}: {type(exc).__name__}: {exc}")
        return ""
