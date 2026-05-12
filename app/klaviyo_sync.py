"""
Klaviyo Sync
============
Push enriched KVK companies to a Klaviyo list as profiles.
Uses Klaviyo API v3 (revision 2024-02-15).
"""
from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.config import settings

KLAVIYO_API_BASE = "https://a.klaviyo.com/api"
KLAVIYO_REVISION = "2024-02-15"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Klaviyo-API-Key {settings.klaviyo_private_api_key}",
        "Content-Type": "application/json",
        "revision": KLAVIYO_REVISION,
        "Accept": "application/json",
    }


def _post(path: str, payload: dict) -> dict:
    req = Request(
        f"{KLAVIYO_API_BASE}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=_headers(),
    )
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Klaviyo {exc.code}: {body}") from exc


def _get(path: str) -> dict:
    req = Request(f"{KLAVIYO_API_BASE}{path}", headers=_headers())
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def upsert_profile(email: str, properties: dict[str, Any]) -> str:
    """
    Create or update a Klaviyo profile.
    Returns the profile ID.
    """
    payload = {
        "data": {
            "type": "profile",
            "attributes": {
                "email": email,
                "organization": properties.get("company_name", ""),
                "location": {
                    "city": properties.get("city", ""),
                    "country": "Netherlands",
                },
                "properties": {
                    "kvk_number": properties.get("kvk_number", ""),
                    "website": properties.get("website", ""),
                    "phone": properties.get("phone", ""),
                    "bike_shop_tier": properties.get("bike_shop_tier", ""),
                    "outreach_priority": properties.get("outreach_priority", ""),
                    "recommended_sales_angle": properties.get("recommended_sales_angle", ""),
                    "primary_address": properties.get("primary_address", ""),
                    "source": "kvk_import",
                },
            },
        }
    }
    result = _post("/profiles/", payload)
    return result.get("data", {}).get("id", "")


def add_profiles_to_list(profile_ids: list[str], list_id: str | None = None) -> None:
    """Add profiles to the configured Klaviyo list."""
    lid = list_id or settings.klaviyo_list_id
    if not profile_ids:
        return
    # Klaviyo accepts up to 1000 per call
    for i in range(0, len(profile_ids), 1000):
        batch = profile_ids[i : i + 1000]
        payload = {
            "data": [{"type": "profile", "id": pid} for pid in batch]
        }
        _post(f"/lists/{lid}/relationships/profiles/", payload)


def push_companies_to_klaviyo(companies: list[Any]) -> tuple[int, int, list[str]]:
    """
    Upsert each company as a Klaviyo profile and add to the configured list.
    Returns (success_count, fail_count, error_messages).
    """
    if not settings.klaviyo_private_api_key:
        raise RuntimeError("KLAVIYO_PRIVATE_API_KEY is not set")

    profile_ids: list[str] = []
    errors: list[str] = []

    for company in companies:
        try:
            pid = upsert_profile(
                email=company.email_public,
                properties={
                    "company_name": company.company_name,
                    "city": company.primary_city,
                    "kvk_number": company.kvk_number,
                    "website": company.website,
                    "phone": company.phone_public,
                    "bike_shop_tier": company.bike_shop_tier,
                    "outreach_priority": company.outreach_priority,
                    "recommended_sales_angle": company.recommended_sales_angle,
                    "primary_address": company.primary_address,
                },
            )
            if pid:
                profile_ids.append(pid)
        except Exception as exc:
            errors.append(f"{company.company_name}: {exc}")

    if profile_ids:
        try:
            add_profiles_to_list(profile_ids)
        except Exception as exc:
            errors.append(f"List add failed: {exc}")

    return len(profile_ids), len(errors), errors


def test_klaviyo_connection() -> dict[str, Any]:
    """Verify API key works by fetching the configured list."""
    if not settings.klaviyo_private_api_key:
        return {"ok": False, "error": "No API key configured"}
    try:
        data = _get(f"/lists/{settings.klaviyo_list_id}/")
        name = data.get("data", {}).get("attributes", {}).get("name", "")
        return {"ok": True, "list_name": name, "list_id": settings.klaviyo_list_id}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
