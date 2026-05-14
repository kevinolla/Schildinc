"""
Bing HTML Search Scraper
========================
Free alternative to paid search APIs (Brave, Google CSE). Bing's public
HTML search page returns rich snippets — the same meta-descriptions a
human sees in a Chrome search — and unlike DuckDuckGo it doesn't strip
emails from the result page.

No API key, no quota, no billing. Bing's anti-bot is mild enough that
modest, well-paced scraping with a real-browser User-Agent works
reliably.

Used as a free Stage 0 / snippet source in the KVK enrichment pipeline.
"""
from __future__ import annotations

import random
import re
from html import unescape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen


_BING_ENDPOINT = "https://www.bing.com/search"

# Rotate among real-browser UAs to avoid the "Python-urllib" 403 path.
_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) "
    "Gecko/20100101 Firefox/125.0",
]

# Strip HTML tags but keep the inner text — Bing wraps snippets in lots
# of <strong>/<span> markup we don't care about.
_TAG_RE = re.compile(r"<[^>]+>")

# Bing search result block — each result is in <li class="b_algo">. We
# capture the inner HTML so emails embedded in either the title, URL,
# or caption text all get scanned.
_RESULT_BLOCK_RE = re.compile(
    r'<li[^>]*class="[^"]*b_algo[^"]*"[^>]*>([\s\S]*?)</li>', re.I
)


def is_enabled() -> bool:
    """Bing scraping is always enabled — no key required."""
    return True


def bing_search_html(query: str, count: int = 10, market: str = "nl-NL") -> str:
    """
    Fetch the raw Bing HTML for a query and return it.

    Returns an empty string on any failure (network error, 403/429 anti-
    bot block, timeout). The caller should be prepared for that.
    """
    if not (query or "").strip():
        return ""

    # `count` clamped to Bing's per-page max of 30
    params = f"q={quote_plus(query)}&count={max(1, min(30, count))}&cc=NL&setlang=nl&mkt={market}"
    url = f"{_BING_ENDPOINT}?{params}"

    ua = random.choice(_USER_AGENTS)
    req = Request(
        url,
        headers={
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
            # Don't ask for gzip — keeps the response easy to scan without
            # adding a gzip dependency to the hot path.
            "Accept-Encoding": "identity",
        },
    )
    try:
        with urlopen(req, timeout=8) as resp:
            raw = resp.read()
            html = raw.decode("utf-8", errors="replace")
            # Bing returns ~5KB pages when it serves a captcha / interstitial
            # rather than real results — detect that and log it so we know
            # the cloud IP got flagged.
            if len(html) < 8000 or "b_algo" not in html:
                snippet = html[:200].replace("\n", " ")
                print(f"[bing-search] Suspicious response (len={len(html)}) for query={query!r}: {snippet}")
            return html
    except HTTPError as exc:
        # 429 / 403 means Bing flagged the request. Don't crash — caller
        # falls through to the next source.
        print(f"[bing-search] HTTP {exc.code} for query={query!r}")
        return ""
    except (URLError, TimeoutError, Exception) as exc:
        print(f"[bing-search] Error for query={query!r}: {type(exc).__name__}: {exc}")
        return ""


def bing_snippet_text(query: str, count: int = 10) -> str:
    """
    Concatenate the visible text from every Bing result block into a
    single blob the email regex can scan in one pass.

    Falls back to the whole page if structured blocks weren't detected
    (Bing sometimes rolls out new layouts on AB tests).
    """
    html = bing_search_html(query, count=count)
    if not html:
        return ""

    blocks: list[str] = []

    # Primary path: pull each `b_algo` result block
    for match in _RESULT_BLOCK_RE.finditer(html):
        block = match.group(1) or ""
        text = unescape(_TAG_RE.sub(" ", block))
        text = " ".join(text.split())  # collapse whitespace
        if text:
            blocks.append(text)

    # Belt-and-braces: if structured extraction came up empty, scan the
    # whole page. Slightly noisier but the existing email regex filters
    # vendor/placeholder/noreply addresses anyway.
    if not blocks:
        cleaned = unescape(_TAG_RE.sub(" ", html))
        cleaned = " ".join(cleaned.split())
        return cleaned

    return " | ".join(blocks)
