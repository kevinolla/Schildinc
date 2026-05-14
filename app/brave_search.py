"""
Brave Search API Wrapper
========================
Replaces the deprecated Google CSE "Search the entire web" path.

Brave Search is a privacy-focused web index with a generous free tier
and snippets that closely match what a human sees in Chrome. Perfect
for our Stage 0 email extraction — they routinely include the email
right in the meta description of bike-shop contact pages.

Pricing (as of May 2026):
  - Free tier: 2,000 queries/month, 1 query/sec
  - Paid (Pro): from $3 per 1000 queries, 20 q/s
  - For 3990 KVK records ≈ 3990 queries → fits paid plan at ~$12, or
    spread across 2 months free if needed

Setup (one time, ~3 min):
  1. Sign up at https://api.search.brave.com/
  2. Add a Pay-as-you-go plan (no charge until you exceed the free quota)
  3. Generate an API key under "API Keys"
  4. Set BRAVE_API_KEY env var in Railway
That's it — no programmable engine to configure, no toggles, no domain
restrictions, no deprecation traps.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from app.config import settings


_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


def is_enabled() -> bool:
    """True when an API key is configured."""
    return bool(getattr(settings, "brave_api_key", ""))


def brave_search(query: str, count: int = 5, country: str = "NL") -> list[dict[str, Any]]:
    """
    Run one Brave web search and return the parsed result items.

    Each returned dict contains at minimum:
        title       — page title (rich, often includes brand name)
        description — meta-description excerpt (where emails appear)
        url         — full URL of the result
        meta_url    — host + path components

    Returns [] on any failure (missing key, network error, rate limit,
    quota). Caller never has to think about exceptions.
    """
    if not is_enabled() or not (query or "").strip():
        return []

    params = {
        "q": query,
        "count": str(max(1, min(20, count))),
        "country": country or "NL",
        "safesearch": "off",  # we need contact pages, even on edgy queries
    }
    url = f"{_BRAVE_ENDPOINT}?" + "&".join(f"{k}={quote_plus(v)}" for k, v in params.items())
    req = Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": settings.brave_api_key,
        },
    )
    try:
        with urlopen(req, timeout=8) as resp:
            raw = resp.read()
            # Brave honors Accept-Encoding: gzip
            if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            data = json.loads(raw.decode("utf-8", errors="replace"))
    except HTTPError as exc:
        # Brave returns gzipped error bodies too — decompress before printing
        try:
            raw_body = exc.read()
            if exc.headers.get("Content-Encoding", "").lower() == "gzip":
                import gzip
                raw_body = gzip.decompress(raw_body)
            body = raw_body.decode("utf-8", errors="replace")[:200]
        except Exception:
            body = ""
        # 402 = no active subscription, 403 = key invalid, 429 = rate limit
        print(f"[brave-search] HTTP {exc.code} for query={query!r}: {body}")
        return []
    except (URLError, TimeoutError, Exception):
        return []

    web = (data.get("web") or {}).get("results") or []
    return list(web)


def brave_snippet_text(query: str, count: int = 5) -> str:
    """
    Concatenate title + description + url + extra_snippets from every
    Brave result into a single text blob the email regex can scan in
    one pass.

    Brave puts emails in different places depending on the source page:
    - `description` — the most common (just like Chrome shows)
    - `extra_snippets` — additional pulled excerpts when relevant
    - `url` — when the email is encoded as a mailto link in the listing
    """
    items = brave_search(query, count=count)
    if not items:
        return ""

    parts: list[str] = []
    for item in items:
        for key in ("title", "description", "url"):
            val = item.get(key) or ""
            if isinstance(val, str) and val:
                parts.append(val)
        # Brave sometimes returns extra_snippets — short excerpts with hits
        extras = item.get("extra_snippets") or []
        if isinstance(extras, list):
            for snippet in extras:
                if isinstance(snippet, str) and snippet:
                    parts.append(snippet)
        # meta_url block has hostname + path — useful for domain matching
        meta = item.get("meta_url") or {}
        for k in ("hostname", "path"):
            v = meta.get(k)
            if isinstance(v, str) and v:
                parts.append(v)
    return " | ".join(parts)
