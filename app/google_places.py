from __future__ import annotations

import json
from typing import Any
from urllib.request import Request, urlopen

from app.config import settings
from app.utils import normalize_domain, normalize_text


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


def find_best_place_match(
    *,
    company_name: str,
    city: str = "",
    country_code: str = "",
    query: str = "",
    page_size: int = 5,
) -> dict[str, Any] | None:
    search_query = (query or " ".join(part for part in [company_name, city, country_code, "fietswinkel"] if part)).strip()
    if not search_query:
        return None

    places = search_google_places(query=search_query, location=country_code or city, page_size=page_size)
    if not places:
        return None

    clean_name = normalize_text(company_name)
    clean_city = normalize_text(city)
    best_place: dict[str, Any] | None = None
    best_score = -1

    for place in places:
        display_name = place.get("displayName", {}).get("text", "")
        score = 0
        if normalize_text(display_name) == clean_name:
            score += 80
        elif clean_name and clean_name in normalize_text(display_name):
            score += 60

        address_parts = {item.get("types", [""])[0]: item.get("shortText", "") for item in place.get("addressComponents", [])}
        place_city = normalize_text(address_parts.get("locality", ""))
        if clean_city and place_city == clean_city:
            score += 15
        if country_code and normalize_text(address_parts.get("country", "")) == normalize_text(country_code):
            score += 5
        if normalize_domain(place.get("websiteUri", "")):
            score += 8

        if score > best_score:
            best_place = place
            best_score = score

    return best_place
