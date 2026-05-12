from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.config import settings
from app.models import Prospect


@dataclass
class KlaviyoExportResult:
    list_id: str
    list_name: str
    exported_count: int
    job_id: str


class KlaviyoExportError(RuntimeError):
    pass


PHONE_PATTERN = re.compile(r"^\+\d{7,15}$")


def export_prospects_to_klaviyo(prospects: list[Prospect], *, list_id: str = "", list_name: str = "") -> KlaviyoExportResult:
    if not settings.klaviyo_private_api_key:
        raise KlaviyoExportError("Klaviyo is not configured yet. Set KLAVIYO_PRIVATE_API_KEY first.")
    if not prospects:
        raise KlaviyoExportError("No prospects with public email were available for export.")

    resolved_list_id = (list_id or settings.klaviyo_default_list_id).strip()
    resolved_list_name = (list_name or settings.klaviyo_default_list_name).strip()
    if not resolved_list_id and not resolved_list_name:
        raise KlaviyoExportError("Provide a Klaviyo list ID or a new list name.")

    if not resolved_list_id:
        resolved_list_id = _create_klaviyo_list(resolved_list_name)

    payload = {
        "data": {
            "type": "profile-bulk-import-job",
            "attributes": {
                "profiles": {
                    "data": [_prospect_to_klaviyo_profile(prospect) for prospect in prospects]
                }
            },
            "relationships": {
                "lists": {
                    "data": [
                        {
                            "type": "list",
                            "id": resolved_list_id,
                        }
                    ]
                }
            },
        }
    }
    response = _klaviyo_request(
        method="POST",
        url="https://a.klaviyo.com/api/profile-bulk-import-jobs",
        payload=payload,
    )
    job_id = str(response.get("data", {}).get("id", "")).strip()
    if not job_id:
        raise KlaviyoExportError("Klaviyo did not return a job ID.")
    return KlaviyoExportResult(
        list_id=resolved_list_id,
        list_name=resolved_list_name,
        exported_count=len(prospects),
        job_id=job_id,
    )


def _create_klaviyo_list(name: str) -> str:
    payload = {
        "data": {
            "type": "list",
            "attributes": {
                "name": name,
            },
        }
    }
    response = _klaviyo_request(
        method="POST",
        url="https://a.klaviyo.com/api/lists",
        payload=payload,
    )
    list_id = str(response.get("data", {}).get("id", "")).strip()
    if not list_id:
        raise KlaviyoExportError("Klaviyo did not return a list ID.")
    return list_id


def _prospect_to_klaviyo_profile(prospect: Prospect) -> dict:
    phone_number = _normalize_phone_number(prospect.phone)
    attributes = {
        "email": prospect.email,
        "phone_number": phone_number,
        "organization": prospect.company_name,
        "location": {
            "city": prospect.city or None,
            "country": prospect.country_code or None,
            "address1": prospect.address or None,
        },
        "properties": {
            "source": prospect.source,
            "source_reference": prospect.source_reference,
            "website": prospect.website,
            "website_domain": prospect.website_domain,
            "bike_shop_tier": prospect.bike_shop_tier,
            "outreach_priority": prospect.outreach_priority,
            "match_status": prospect.match_status.value,
            "review_status": prospect.review_status.value,
            "kvk_number": prospect.kvk_number,
            "kvk_establishment_number": prospect.kvk_establishment_number,
            "recommended_contact_type": prospect.recommended_contact_type,
        },
    }
    cleaned_attributes = {key: value for key, value in attributes.items() if value not in ("", None, {}, [])}
    return {
        "type": "profile",
        "attributes": cleaned_attributes,
    }


def _normalize_phone_number(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if normalized.startswith("00"):
        normalized = f"+{normalized[2:]}"
    if PHONE_PATTERN.match(normalized):
        return normalized
    return None


def _klaviyo_request(*, method: str, url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Klaviyo-API-Key {settings.klaviyo_private_api_key}",
            "accept": "application/vnd.api+json",
            "content-type": "application/vnd.api+json",
            "revision": settings.klaviyo_api_revision,
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise KlaviyoExportError(f"Klaviyo API error ({exc.code}): {detail}") from exc
