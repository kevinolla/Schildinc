from __future__ import annotations

import re
import json
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from sqlalchemy.orm import Session

from app.config import settings
from app.matching import apply_matching
from app.models import Prospect, ProspectActivityLog
from app.tiering import apply_bike_tier
from app.utils import email_domain, normalize_domain, normalize_email, normalize_text

LIKELY_PATH_KEYWORDS = [
    "contact",
    "about",
    "about-us",
    "over-ons",
    "contact-us",
    "impressum",
    "legal",
    "privacy",
    "terms",
    "faq",
    "support",
    "service",
    "customer-service",
    "customer-care",
    "team",
    "shop",
    "repair",
    "reparatie",
    "verhuur",
    "winkel",
    "company",
    "solutions",
]
MAX_CRAWL_PAGES = 12
MAX_QUEUE_LINKS = 20
REJECT_LOCAL_PARTS = {"noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "support-ticket"}
VENDOR_DOMAINS = {"2moso.com", "shopify.com", "mailchimp.com", "klaviyo.com", "zendesk.com", "salesforce.com"}
EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
SOCIAL_URL_PATTERN = re.compile(r"https?://(?:www\.)?(instagram\.com/[^\s\"'<>]+|linkedin\.com/[^\s\"'<>]+)", re.I)
MAILTO_PATTERN = re.compile(r"mailto:([^\"'<>?\s]+)", re.I)
JSON_EMAIL_PATTERN = re.compile(r'"email"\s*:\s*"([^"]+@[^"]+)"', re.I)
WHATSAPP_URL_PATTERN = re.compile(
    r"(https?://(?:wa\.me/\+?\d[\d-]{5,}|api\.whatsapp\.com/send\?[^\"'\s<>]+)|whatsapp://send\?phone=\+?\d[\d-]{5,})",
    re.I,
)
VISIBLE_WHATSAPP_PATTERN = re.compile(r"(?:whatsapp|whats app)[^+\d]{0,24}(\+?\d[\d\s()./-]{6,}\d)", re.I)
OBFUSCATED_EMAIL_PATTERN = re.compile(
    r"([A-Z0-9._%+-]+)\s*(?:\[at\]|\(at\)| at )\s*([A-Z0-9.-]+)\s*(?:\[dot\]|\(dot\)| dot )\s*([A-Z]{2,})",
    re.I,
)


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
    emails_found: list[str]
    pages_scanned: list[str]
    whatsapp_number: str
    whatsapp_url: str
    linkedin_url: str
    instagram_url: str
    summary: str
    highlights: list[str]
    error: str = ""


def discover_public_contacts_for_prospect(session: Session, prospect: Prospect) -> DiscoveryResult:
    base_url = prospect.website or ""
    if not base_url:
        result = DiscoveryResult(
            "no_website",
            "",
            "",
            0,
            [],
            [],
            prospect.whatsapp_number or "",
            prospect.whatsapp_url or "",
            prospect.linkedin_url or "",
            prospect.instagram_url or "",
            "",
            [],
            "",
        )
        _apply_discovery_result(session, prospect, result)
        return result

    website = ensure_http_url(base_url)
    domain = normalize_domain(website)
    visited: list[str] = []
    email_candidates: list[DiscoveryEmailCandidate] = []
    whatsapp_numbers: list[str] = []
    whatsapp_links: list[str] = []
    linkedin_links: list[str] = []
    instagram_links: list[str] = []
    snippets: list[str] = []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page(ignore_https_errors=True)
            page.set_default_timeout(settings.playwright_timeout_ms)

            pages_to_visit = _seed_pages_to_visit(website)

            while pages_to_visit and len(visited) < MAX_CRAWL_PAGES:
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
                whatsapp_numbers.extend(page_info["whatsapp_numbers"])
                whatsapp_links.extend(page_info["whatsapp_urls"])
                linkedin_links.extend(page_info["linkedin"])
                instagram_links.extend(page_info["instagram"])
                snippets.extend(page_info["snippets"])

                for href in page_info["internal_links"]:
                    if href not in visited and href not in pages_to_visit and len(pages_to_visit) < MAX_QUEUE_LINKS:
                        pages_to_visit.append(href)

            browser.close()
    except Exception as exc:  # noqa: BLE001
        result = DiscoveryResult("error", "", "", 0, [], [], "", "", "", "", [], str(exc))
        _apply_discovery_result(session, prospect, result)
        return result

    best = max(email_candidates, key=lambda item: item.confidence, default=None)
    best_whatsapp_number = _pick_best_whatsapp_number(whatsapp_numbers, prospect.phone)
    best_whatsapp_url = _pick_best_whatsapp_url(whatsapp_links, best_whatsapp_number)
    summary = _summarize_snippets(snippets)
    highlights = _highlights_from_snippets(snippets)
    has_any_contact = bool(best or best_whatsapp_number or linkedin_links or instagram_links)
    result = DiscoveryResult(
        status="found" if best else ("partial" if has_any_contact else "no_contacts"),
        email=best.email if best else "",
        source_page=best.source_page if best else "",
        confidence=best.confidence if best else 0,
        emails_found=_dedupe([item.email for item in sorted(email_candidates, key=lambda item: (-item.confidence, item.email))]),
        pages_scanned=visited,
        whatsapp_number=best_whatsapp_number,
        whatsapp_url=best_whatsapp_url,
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
    prospect.emails_found = "|".join(result.emails_found[:20])
    prospect.pages_scanned = "|".join(result.pages_scanned[:20])
    if result.whatsapp_number:
        prospect.whatsapp_number = result.whatsapp_number
    if result.whatsapp_url:
        prospect.whatsapp_url = result.whatsapp_url
    if result.linkedin_url:
        prospect.linkedin_url = result.linkedin_url
    if result.instagram_url:
        prospect.instagram_url = result.instagram_url
    if result.linkedin_url or result.instagram_url or result.whatsapp_number:
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
            detail=result.error or _build_discovery_log_detail(result),
        )
    )


def _extract_visible_page_info(page, current_url: str) -> dict:
    links = page.locator("a").evaluate_all(
        """els => els
            .map(el => ({
              href: el.href || '',
              text: (el.innerText || '').trim(),
              label: (el.getAttribute('aria-label') || '').trim(),
              title: (el.getAttribute('title') || '').trim()
            }))"""
    )
    try:
        body_text = page.locator("body").inner_text()
    except PlaywrightError:
        body_text = ""
    try:
        raw_html = page.content()
    except PlaywrightError:
        raw_html = ""
    headings = page.locator("h1, h2, h3").all_inner_texts()
    footer_bits = _collect_visible_section_text(page, "footer, [class*='footer'], [id*='footer']")
    contact_bits = _collect_visible_section_text(page, "[class*='contact'], [id*='contact'], [class*='about'], [id*='about']")
    meta_text = _read_meta_description(page)
    structured_data = _extract_structured_data(page)
    raw_source = _extract_public_source_data(raw_html)
    readable_text = _extract_readable_text(raw_html)
    combined_text = " ".join([body_text, readable_text, *headings, *footer_bits, *contact_bits, meta_text])
    emails = {normalize_email(match.group(0)) for match in EMAIL_PATTERN.finditer(combined_text)}
    emails.update(_extract_obfuscated_visible_emails(combined_text))
    emails.update(structured_data["emails"])
    emails.update(raw_source["emails"])
    for item in links:
        href = item.get("href", "") or ""
        text = " ".join([item.get("text", "") or "", item.get("label", "") or "", item.get("title", "") or ""]).strip()
        if href.startswith("mailto:"):
            emails.add(normalize_email(href.replace("mailto:", "").split("?")[0]))
        if text and EMAIL_PATTERN.search(text):
            emails.update(normalize_email(match.group(0)) for match in EMAIL_PATTERN.finditer(text))

    internal_links = []
    whatsapp_numbers = []
    whatsapp_urls = []
    social_linkedin = []
    social_instagram = []
    raw_internal_links = []
    for item in links:
        href = item.get("href", "") or ""
        anchor_text = item.get("text", "") or ""
        label_text = " ".join([item.get("text", "") or "", item.get("label", "") or "", item.get("title", "") or ""]).lower()
        href_lower = href.lower()
        if _is_whatsapp_url(href):
            whatsapp_urls.append(href)
            parsed_number = _extract_whatsapp_number_from_url(href)
            if parsed_number:
                whatsapp_numbers.append(parsed_number)
        elif "linkedin.com" in href_lower or "linkedin" in label_text:
            social_linkedin.append(href)
        elif "instagram.com" in href_lower or "instagram" in label_text:
            social_instagram.append(href)
        else:
            normalized_link = _normalize_internal_link(href, current_url)
            if normalized_link:
                raw_internal_links.append({"url": normalized_link, "anchor_text": anchor_text})
        if "whatsapp" in label_text and item.get("text"):
            whatsapp_text_number = _normalize_phone_like_value(item.get("text", ""))
            if whatsapp_text_number:
                whatsapp_numbers.append(whatsapp_text_number)
    internal_links = _prioritize_internal_links(raw_internal_links, current_url)
    whatsapp_numbers.extend(_extract_visible_whatsapp_numbers(combined_text))
    whatsapp_numbers.extend(raw_source["whatsapp_numbers"])
    whatsapp_urls.extend(raw_source["whatsapp"])
    social_linkedin.extend(structured_data["linkedin"])
    social_instagram.extend(structured_data["instagram"])
    social_linkedin.extend(raw_source["linkedin"])
    social_instagram.extend(raw_source["instagram"])

    snippets = [clean_snippet(text) for text in headings if clean_snippet(text)]
    snippets.extend(clean_snippet(line) for line in readable_text.split(". ")[:8] if clean_snippet(line))
    snippets.extend(clean_snippet(line) for line in footer_bits[:3] if clean_snippet(line))
    if meta_text:
        snippets.append(clean_snippet(meta_text))

    return {
        "emails": sorted(emails),
        "whatsapp_numbers": _dedupe([item for item in whatsapp_numbers if item]),
        "whatsapp_urls": _dedupe([item for item in whatsapp_urls if item]),
        "internal_links": internal_links,
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


def _seed_pages_to_visit(website: str) -> list[str]:
    ordered = [website]
    for link in _build_likely_urls(website):
        if link not in ordered:
            ordered.append(link)
    return ordered


def _normalize_internal_link(href: str, current_url: str) -> str:
    if not href or href.startswith(("mailto:", "tel:", "#", "javascript:")):
        return ""
    current = urlparse(current_url)
    try:
        target = urlparse(urljoin(current_url, href))
    except ValueError:
        return ""
    if target.netloc and normalize_domain(target.netloc) != normalize_domain(current.netloc):
        return ""
    if re.search(r"\.(pdf|jpg|jpeg|png|gif|svg|webp|zip)$", target.path, re.I):
        return ""
    target = target._replace(fragment="", query=target.query)
    return target.geturl()


def _prioritize_internal_links(links: list[dict[str, str]], base_url: str) -> list[str]:
    base = _normalize_internal_link(base_url, base_url)
    seen: set[str] = set()
    scored: list[tuple[int, str]] = []
    priorities = [
        ("contact", 12),
        ("about", 9),
        ("over", 9),
        ("team", 8),
        ("service", 8),
        ("shop", 7),
        ("repair", 7),
        ("reparatie", 7),
        ("verhuur", 7),
        ("winkel", 7),
        ("company", 6),
        ("solutions", 6),
        ("faq", 4),
    ]
    for link in links:
        url = str(link.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        haystack = f"{url.lower()} {(link.get('anchor_text') or '').lower()}"
        score = 0
        for keyword, points in priorities:
            if keyword in haystack:
                score += points
        if url.rstrip("/") == str(base).rstrip("/"):
            score -= 20
        if score > 0:
            scored.append((score, url))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [url for _, url in scored[:MAX_QUEUE_LINKS]]


def _pick_social_link(links: list[str]) -> str:
    for link in links:
        if "/company/" in link or "/business/" in link:
            return link
    return links[0] if links else ""


def _collect_visible_section_text(page, selector: str) -> list[str]:
    try:
        return page.locator(selector).evaluate_all(
            """els => els
                .filter(el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length))
                .map(el => (el.innerText || '').trim())
                .filter(Boolean)
                .slice(0, 8)"""
        )
    except PlaywrightError:
        return []


def _read_meta_description(page) -> str:
    try:
        value = page.locator("meta[name='description']").first.get_attribute("content")
        return (value or "").strip()
    except PlaywrightError:
        return ""


def _extract_structured_data(page) -> dict[str, list[str]]:
    try:
        raw_scripts = page.locator("script[type='application/ld+json']").evaluate_all(
            """els => els.map(el => el.textContent || '').filter(Boolean)"""
        )
    except PlaywrightError:
        raw_scripts = []

    emails: list[str] = []
    linkedin_links: list[str] = []
    instagram_links: list[str] = []
    for raw in raw_scripts:
        for payload in _iter_json_ld_payloads(raw):
            _walk_schema_value(payload, emails, linkedin_links, instagram_links)
    return {
        "emails": _dedupe([normalize_email(item) for item in emails if item]),
        "linkedin": _dedupe(linkedin_links),
        "instagram": _dedupe(instagram_links),
    }


def _iter_json_ld_payloads(raw: str) -> list[object]:
    text = (raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return parsed
    return [parsed]


def _walk_schema_value(value: object, emails: list[str], linkedin_links: list[str], instagram_links: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_name = str(key).lower()
            if key_name == "email" and isinstance(item, str):
                emails.append(item.replace("mailto:", "").strip())
            elif key_name == "sameas":
                if isinstance(item, list):
                    for link in item:
                        _collect_social_from_string(link, linkedin_links, instagram_links)
                elif isinstance(item, str):
                    _collect_social_from_string(item, linkedin_links, instagram_links)
            else:
                _walk_schema_value(item, emails, linkedin_links, instagram_links)
    elif isinstance(value, list):
        for item in value:
            _walk_schema_value(item, emails, linkedin_links, instagram_links)


def _collect_social_from_string(value: object, linkedin_links: list[str], instagram_links: list[str]) -> None:
    link = str(value or "").strip()
    lower = link.lower()
    if "linkedin.com" in lower:
        linkedin_links.append(link)
    elif "instagram.com" in lower:
        instagram_links.append(link)


def _extract_public_source_data(raw_html: str) -> dict[str, list[str]]:
    text = raw_html or ""
    emails = [normalize_email(unquote(match.group(1))) for match in MAILTO_PATTERN.finditer(text)]
    emails.extend(normalize_email(match.group(1)) for match in JSON_EMAIL_PATTERN.finditer(text))
    emails.extend(_extract_obfuscated_visible_emails(unquote(text)))

    whatsapp_links = [match.group(1) for match in WHATSAPP_URL_PATTERN.finditer(text)]
    whatsapp_numbers = [_extract_whatsapp_number_from_url(link) for link in whatsapp_links]
    linkedin_links: list[str] = []
    instagram_links: list[str] = []
    for match in SOCIAL_URL_PATTERN.finditer(text):
        href = match.group(0)
        if "linkedin.com" in href.lower():
            linkedin_links.append(href)
        elif "instagram.com" in href.lower():
            instagram_links.append(href)
    return {
        "emails": _dedupe([item for item in emails if item]),
        "whatsapp": _dedupe([item for item in whatsapp_links if item]),
        "whatsapp_numbers": _dedupe([item for item in whatsapp_numbers if item]),
        "linkedin": _dedupe(linkedin_links),
        "instagram": _dedupe(instagram_links),
    }


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


def _extract_readable_text(html: str) -> str:
    raw = str(html or "")
    without_scripts = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.I)
    without_scripts = re.sub(r"<style[\s\S]*?</style>", " ", without_scripts, flags=re.I)
    without_scripts = re.sub(r"<noscript[\s\S]*?</noscript>", " ", without_scripts, flags=re.I)
    meta_descriptions = [match.group(1) for match in re.finditer(r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"']([^\"']+)[\"'][^>]*>", raw, re.I)]
    titles = [match.group(2) for match in re.finditer(r"<(title|h1|h2|h3|h4)[^>]*>([\s\S]*?)</\1>", raw, re.I)]
    paragraphs = [match.group(2) for match in re.finditer(r"<(p|li)[^>]*>([\s\S]*?)</\1>", without_scripts, re.I)]
    text = " ".join([*meta_descriptions, *titles, *paragraphs])
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return " ".join(text.split()).strip()


def _extract_obfuscated_visible_emails(text: str) -> list[str]:
    results: list[str] = []
    for match in OBFUSCATED_EMAIL_PATTERN.finditer(text or ""):
        email = normalize_email(f"{match.group(1)}@{match.group(2)}.{match.group(3)}")
        if email:
            results.append(email)
    return results


def _is_whatsapp_url(value: str) -> bool:
    lower = str(value or "").lower()
    return "wa.me/" in lower or "api.whatsapp.com/" in lower or lower.startswith("whatsapp://")


def _extract_whatsapp_number_from_url(value: str) -> str:
    parsed = urlparse(str(value or ""))
    raw = ""
    if "wa.me" in parsed.netloc:
        raw = parsed.path.strip("/")
    elif "api.whatsapp.com" in parsed.netloc or parsed.scheme == "whatsapp":
        raw = parse_qs(parsed.query).get("phone", [""])[0]
    return _normalize_phone_like_value(raw)


def _extract_visible_whatsapp_numbers(text: str) -> list[str]:
    results: list[str] = []
    for match in VISIBLE_WHATSAPP_PATTERN.finditer(text or ""):
        phone = _normalize_phone_like_value(match.group(1))
        if phone:
            results.append(phone)
    return results


def _normalize_phone_like_value(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    keep_plus = raw.startswith("+")
    digits = "".join(char for char in raw if char.isdigit())
    if len(digits) < 8:
        return ""
    return f"+{digits}" if keep_plus else digits


def _pick_best_whatsapp_number(candidates: list[str], fallback_phone: str) -> str:
    for number in candidates:
        if number:
            return number
    return _normalize_phone_like_value(fallback_phone) if "whatsapp" in str(fallback_phone or "").lower() else ""


def _pick_best_whatsapp_url(candidates: list[str], number: str) -> str:
    for value in candidates:
        if value:
            return value
    if number:
        return f"https://wa.me/{number.lstrip('+')}"
    return ""


def _build_discovery_log_detail(result: DiscoveryResult) -> str:
    channels = []
    if result.email:
        channels.append(f"email={result.email}")
    if result.whatsapp_number:
        channels.append(f"whatsapp={result.whatsapp_number}")
    if result.instagram_url:
        channels.append("instagram")
    if result.linkedin_url:
        channels.append("linkedin")
    return ", ".join(channels) if channels else "No public contact channels found."


def ensure_http_url(value: str) -> str:
    return value if value.startswith(("http://", "https://")) else f"https://{value}"


def clean_snippet(value: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if len(text) > 180:
        text = text[:177].rsplit(" ", 1)[0] + "..."
    return text
