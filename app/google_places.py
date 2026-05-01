from __future__ import annotations

import json
from typing import Any
from urllib.request import Request, urlopen

from app.config import settings


def search_google_places(query: str, location: str = "", page_size: int = 10) -> list[dict[str, Any]]:
    if not settings.google_places_api_key:
        return []

    text_query = f"{query} in {location}".strip() if location else query
    payload = json.dumps({"textQuery": text_query, "pageSize": page_size}).encode("utf-8")
    request = Request(
        "https://places.googleapis.com/v1/places:searchText",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": settings.google_places_api_key,
            "X-Goog-FieldMask": ",".join(
                [
                    "places.id",
                    "places.displayName",
                    "places.formattedAddress",
                    "places.websiteUri",
                    "places.nationalPhoneNumber",
                    "places.primaryTypeDisplayName",
                    "places.googleMapsUri",
                    "places.addressComponents",
                ]
            ),
        },
    )
    with urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data.get("places", [])


def place_to_prospect_record(place: dict[str, Any]) -> dict[str, Any]:
    address_parts = {item.get("types", [""])[0]: item.get("shortText", "") for item in place.get("addressComponents", [])}
    return {
        "source_reference": place.get("id", ""),
        "company_name": place.get("displayName", {}).get("text", ""),
        "website": place.get("websiteUri", ""),
        "phone": place.get("nationalPhoneNumber", ""),
        "company_type": place.get("primaryTypeDisplayName", {}).get("text", ""),
        "address": place.get("formattedAddress", ""),
        "city": address_parts.get("locality", ""),
        "state": address_parts.get("administrative_area_level_1", ""),
        "country_code": address_parts.get("country", ""),
        "google_maps_url": place.get("googleMapsUri", ""),
    }
