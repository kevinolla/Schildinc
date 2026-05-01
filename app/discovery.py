from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from sqlalchemy.orm import Session

from app.config import settings
from app.matching import apply_matching
from app.models import Prospect, ProspectActivityLog
from app.tiering import apply_bike_tier
from app.utils import email_domain, normalize_domain, normalize_email, normalize_text

LIKELY_PATH_KEYWORDS = ["contact", "about", "about-us", "contact-us", "impressum", "legal", "privacy", "terms"]
REJECT_LOCAL_PARTS = {"noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "support-ticket"}
VENDOR_DOMAINS = {"2moso.com", "shopify.com", "mailchimp.com", "klaviyo.com", "zendesk.com", "salesforce.com"}
EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)


@dataclass
class DiscoveryEmailCandidate:
    email: str
    source_page: str
    confidence: int


@dataclass
class DiscoveryResult:
    status: str
    email: str
    source_page: str
    confidence: int
    linkedin_url: str
    instagram_url: str
    summary: str
    highlights: list[str]
    error: str = ""


def discover_public_contacts_for_prospect(session: Session, prospect: Prospect) -> DiscoveryResult:
    base_url = prospect.website or ""
    if not base_url:
        result = DiscoveryResult("no_website", "", "", 0, prospect.linkedin_url or "", prospect.instagram_url or "", "", [], "")
        _apply_discovery_result(session, prospect, result)
        return result

    website = ensure_http_url(base_url)
    domain = normalize_domain(website)
    visited: list[str] = []
    email_candidates: list[DiscoveryEmailCandidate] = []
    linkedin_links: list[str] = []
    instagram_links: list[str] = []
    snippets: list[str] = []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page(ignore_https_errors=True)
            page.set_default_timeout(settings.playwright_timeout_ms)

            pages_to_visit = [website]
            pages_to_visit.extend(_build_likely_urls(website))

            while pages_to_visit and len(visited) < 6:
                target = pages_to_visit.pop(0)
                if target in visited:
                    continue
                if normalize_domain(target) != domain:
                    continue
                try:
                    page.goto(target, wait_until="domcontentloaded")
                    try:
                        page.wait_for_load_state("networkidle", timeout=2500)
                    except PlaywrightTimeoutError:
                        pass
                except PlaywrightError:
                    continue

                visited.append(target)
                page_info = _extract_visible_page_info(page, target)
                email_candidates.extend(_rank_email_candidates(page_info["emails"], target, domain, prospect.company_name))
                linkedin_links.extend(page_info["linkedin"])
                instagram_links.extend(page_info["instagram"])
                snippets.extend(page_info["snippets"])

                for href in page_info["internal_links"]:
                    if href not in visited and href not in pages_to_visit and len(pages_to_visit) < 8:
                        pages_to_visit.append(href)

            browser.close()
    except Exception as exc:  # noqa: BLE001
        result = DiscoveryResult("error", "", "", 0, "", "", "", [], str(exc))
        _apply_discovery_result(session, prospect, result)
        return result

    best = max(email_candidates, key=lambda item: item.confidence, default=None)
    summary = _summarize_snippets(snippets)
    highlights = _highlights_from_snippets(snippets)
    result = DiscoveryResult(
        status="found" if best else "no_email",
        email=best.email if best else "",
        source_page=best.source_page if best else "",
        confidence=best.confidence if best else 0,
        linkedin_url=_pick_social_link(linkedin_links),
        instagram_url=_pick_social_link(instagram_links),
        summary=summary,
        highlights=highlights,
    )
    _apply_discovery_result(session, prospect, result)
    return result


def _apply_discovery_result(session: Session, prospect: Prospect, result: DiscoveryResult) -> None:
    prospect.email_discovery_status = result.status
    prospect.discovery_error = result.error
    prospect.website_summary = result.summary or prospect.website_summary
    prospect.discovery_highlights = "\n".join(result.highlights[:4])
    if result.linkedin_url:
        prospect.linkedin_url = result.linkedin_url
    if result.instagram_url:
        prospect.instagram_url = result.instagram_url
    if result.linkedin_url or result.instagram_url:
        prospect.social_discovered_at = datetime.utcnow()

    if result.email:
        prospect.email = result.email
        prospect.email_domain = email_domain(result.email)
        prospect.email_source_page = result.source_page
        prospect.email_confidence = result.confidence
        prospect.email_discovered_at = datetime.utcnow()
    else:
        prospect.email_source_page = result.source_page
        prospect.email_confidence = 0

    apply_matching(session, prospect)
    apply_bike_tier(prospect)
    session.add(
        ProspectActivityLog(
            prospect=prospect,
            action_type="email_discovery",
            status=result.status,
            source_url=result.source_page or prospect.website,
            detail=result.error or result.email or "No public business email found.",
        )
    )


def _extract_visible_page_info(page, current_url: str) -> dict:
    links = page.locator("a").evaluate_all(
        """els => els
            .filter(el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length))
            .map(el => ({href: el.href || '', text: (el.innerText || '').trim()}))"""
    )
    try:
        body_text = page.locator("body").inner_text()
    except PlaywrightError:
        body_text = ""
    headings = page.locator("h1, h2, h3").all_inner_texts()
    visible_text = " ".join([body_text, *headings])
    emails = {normalize_email(match.group(0)) for match in EMAIL_PATTERN.finditer(visible_text)}
    for item in links:
        href = item.get("href", "") or ""
        text = item.get("text", "") or ""
        if href.startswith("mailto:"):
            emails.add(normalize_email(href.replace("mailto:", "").split("?")[0]))
        if text and EMAIL_PATTERN.search(text):
            emails.update(normalize_email(match.group(0)) for match in EMAIL_PATTERN.finditer(text))

    internal_links = []
    social_linkedin = []
    social_instagram = []
    for item in links:
        href = item.get("href", "") or ""
        if "linkedin.com" in href.lower():
            social_linkedin.append(href)
        elif "instagram.com" in href.lower():
            social_instagram.append(href)
        elif _is_likely_internal_follow_link(href, current_url):
            internal_links.append(href)

    snippets = [clean_snippet(text) for text in headings if clean_snippet(text)]
    snippets.extend(clean_snippet(line) for line in body_text.split("\n")[:8] if clean_snippet(line))

    return {
        "emails": sorted(emails),
        "internal_links": _dedupe(internal_links),
        "linkedin": _dedupe(social_linkedin),
        "instagram": _dedupe(social_instagram),
        "snippets": _dedupe([item for item in snippets if item]),
    }


def _rank_email_candidates(emails: list[str], source_page: str, website_domain: str, company_name: str) -> list[DiscoveryEmailCandidate]:
    results: list[DiscoveryEmailCandidate] = []
    company_tokens = {token for token in normalize_text(company_name).split() if len(token) > 2}
    for email in emails:
        if not _looks_valid_business_email(email, website_domain, company_tokens):
            continue
        local = email.split("@", 1)[0]
        score = 30
        if email_domain(email) == website_domain:
            score += 35
        elif any(token in email_domain(email) for token in company_tokens):
            score += 18
        else:
            score -= 10

        if local in {"info", "sales", "contact", "hello", "service"}:
            score += 30
        elif local.startswith(("info", "sales", "contact", "hello", "service")):
            score += 24
        elif "." in local or "-" in local:
            score += 10

        if any(part in source_page.lower() for part in LIKELY_PATH_KEYWORDS):
            score += 8

        results.append(DiscoveryEmailCandidate(email=email, source_page=source_page, confidence=max(0, min(score, 100))))
    return results


def _looks_valid_business_email(email: str, website_domain: str, company_tokens: set[str]) -> bool:
    if not email or "@" not in email:
        return False
    local, domain = email.split("@", 1)
    if local in REJECT_LOCAL_PARTS or any(token in local for token in REJECT_LOCAL_PARTS):
        return False
    if any(local.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif")):
        return False
    if domain in VENDOR_DOMAINS and domain != website_domain:
        return False
    if "example." in domain or "test." in domain:
        return False
    if domain != website_domain and company_tokens and not any(token in domain for token in company_tokens):
        return False
    return True


def _build_likely_urls(website: str) -> list[str]:
    parsed = urlparse(website)
    root = f"{parsed.scheme}://{parsed.netloc}"
    return [urljoin(root + "/", path) for path in LIKELY_PATH_KEYWORDS]


def _is_likely_internal_follow_link(href: str, current_url: str) -> bool:
    if not href or href.startswith(("mailto:", "tel:", "#")):
        return False
    current = urlparse(current_url)
    target = urlparse(href)
    if target.netloc and normalize_domain(target.netloc) != normalize_domain(current.netloc):
        return False
    haystack = href.lower()
    return any(keyword in haystack for keyword in LIKELY_PATH_KEYWORDS)


def _pick_social_link(links: list[str]) -> str:
    for link in links:
        if "/company/" in link or "/business/" in link:
            return link
    return links[0] if links else ""


def _summarize_snippets(snippets: list[str]) -> str:
    useful = [item for item in snippets if len(item) > 20]
    return " ".join(useful[:2]).strip()


def _highlights_from_snippets(snippets: list[str]) -> list[str]:
    return [item for item in snippets if len(item) > 14][:4]


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output


def ensure_http_url(value: str) -> str:
    return value if value.startswith(("http://", "https://")) else f"https://{value}"


def clean_snippet(value: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if len(text) > 180:
        text = text[:177].rsplit(" ", 1)[0] + "..."
    return text
