from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
from urllib.parse import urlparse

from app.config import settings


def normalize_text(value: str | None) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9\s]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize_domain(value: str | None) -> str:
    value = str(value or "").strip().lower()
    if not value:
        return ""
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    domain = parsed.netloc or parsed.path
    domain = domain.lower().strip().strip("/")
    domain = re.sub(r"^www\.", "", domain)
    return domain


def normalize_email(value: str | None) -> str:
    return str(value or "").strip().lower()


def email_domain(email: str | None) -> str:
    value = normalize_email(email)
    if "@" not in value:
        return ""
    return value.split("@", 1)[1]


def build_name_geo_key(name: str | None, city: str | None, state: str | None, country: str | None) -> str:
    return f"namegeo:{normalize_text(name)}|{normalize_text(city)}|{normalize_text(state)}|{normalize_text(country)}"


def build_unsubscribe_token(email: str) -> str:
    digest = hmac.new(
        settings.unsubscribe_secret.encode("utf-8"),
        normalize_email(email).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest


def split_pipe_values(value: str | None) -> list[str]:
    items = [item.strip() for item in str(value or "").split("|")]
    return [item for item in items if item]


def parse_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}
