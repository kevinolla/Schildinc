"""
Country code/name registry
==========================
Two jobs:
  1. Canonicalize any country string the imports throw at us
     (full name, ISO-2, ISO-3, the 3-letter slug my old normalizer
     produced) to a single ISO 2-letter code.
  2. Display human-friendly names in the UI, mapped from those codes.

Only the countries we actually see in Schild's customer data are
listed; unknown values pass through unchanged so we never lose
information.
"""
from __future__ import annotations

# Canonical: ISO 2-letter → human name
COUNTRIES: dict[str, str] = {
    "NL": "Netherlands",
    "BE": "Belgium",
    "DE": "Germany",
    "FR": "France",
    "GB": "United Kingdom",
    "US": "United States",
    "CH": "Switzerland",
    "AT": "Austria",
    "IT": "Italy",
    "ES": "Spain",
    "PT": "Portugal",
    "DK": "Denmark",
    "NO": "Norway",
    "SE": "Sweden",
    "FI": "Finland",
    "IE": "Ireland",
    "LU": "Luxembourg",
    "PL": "Poland",
    "CZ": "Czechia",
    "GR": "Greece",
    "HU": "Hungary",
    "RO": "Romania",
    "BG": "Bulgaria",
    "HR": "Croatia",
    "SI": "Slovenia",
    "SK": "Slovakia",
    "EE": "Estonia",
    "LV": "Latvia",
    "LT": "Lithuania",
    "MT": "Malta",
    "CY": "Cyprus",
    "TR": "Turkey",
    "XK": "Kosovo",
    "RS": "Serbia",
    "BA": "Bosnia & Herzegovina",
    "MK": "North Macedonia",
    "AL": "Albania",
    "ME": "Montenegro",
    "MD": "Moldova",
    "UA": "Ukraine",
    "RU": "Russia",
    "BY": "Belarus",
    "CA": "Canada",
    "MX": "Mexico",
    "BR": "Brazil",
    "AR": "Argentina",
    "CL": "Chile",
    "CO": "Colombia",
    "AU": "Australia",
    "NZ": "New Zealand",
    "JP": "Japan",
    "CN": "China",
    "HK": "Hong Kong",
    "TW": "Taiwan",
    "KR": "South Korea",
    "SG": "Singapore",
    "MY": "Malaysia",
    "TH": "Thailand",
    "ID": "Indonesia",
    "PH": "Philippines",
    "VN": "Vietnam",
    "IN": "India",
    "AE": "United Arab Emirates",
    "SA": "Saudi Arabia",
    "IL": "Israel",
    "ZA": "South Africa",
}

# Aliases the historical CSVs and my older normalizer produced.
# Map → canonical ISO 2-letter from COUNTRIES.
_ALIAS_TO_ISO2: dict[str, str] = {
    # Full names (lowercased)
    "netherlands": "NL", "the netherlands": "NL", "holland": "NL",
    "belgium": "BE",
    "germany": "DE", "deutschland": "DE",
    "france": "FR",
    "united kingdom": "GB", "uk": "GB", "great britain": "GB", "britain": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    "united states": "US", "usa": "US", "u.s.a.": "US", "u.s.": "US", "america": "US",
    "switzerland": "CH",
    "austria": "AT",
    "italy": "IT", "italia": "IT",
    "spain": "ES", "españa": "ES",
    "portugal": "PT",
    "denmark": "DK",
    "norway": "NO",
    "sweden": "SE",
    "finland": "FI",
    "ireland": "IE",
    "luxembourg": "LU",
    "poland": "PL",
    "czechia": "CZ", "czech republic": "CZ",
    "greece": "GR",
    "hungary": "HU",
    "romania": "RO",
    "bulgaria": "BG",
    "croatia": "HR",
    "slovenia": "SI",
    "slovakia": "SK",
    "estonia": "EE",
    "latvia": "LV",
    "lithuania": "LT",
    "malta": "MT",
    "cyprus": "CY",
    "turkey": "TR", "türkiye": "TR",
    "kosovo": "XK",
    "serbia": "RS",
    "canada": "CA",
    "mexico": "MX",
    "brazil": "BR", "brasil": "BR",
    "argentina": "AR",
    "australia": "AU",
    "new zealand": "NZ",
    "japan": "JP",
    "china": "CN",
    "hong kong": "HK",
    "taiwan": "TW",
    "south korea": "KR", "korea": "KR",
    "singapore": "SG",
    "malaysia": "MY",
    "thailand": "TH",
    "indonesia": "ID",
    "philippines": "PH",
    "vietnam": "VN",
    "india": "IN",
    "united arab emirates": "AE", "uae": "AE",
    "israel": "IL",
    "south africa": "ZA",

    # 3-letter ISO + my old slug truncations
    "nld": "NL", "net": "NL",
    "bel": "BE",
    "deu": "DE", "ger": "DE",
    "fra": "FR",
    "gbr": "GB", "uni": "US",   # UNI: ambiguous (was United Kingdom or United States) — pick US as the majority case in Schild data
    "usa": "US",
    "che": "CH", "swi": "CH",
    "aut": "AT",
    "ita": "IT",
    "esp": "ES",
    "prt": "PT",
    "dnk": "DK",
    "nor": "NO",
    "swe": "SE",
    "fin": "FI",
    "irl": "IE",
    "lux": "LU",
    "pol": "PL",
    "cze": "CZ",
    "grc": "GR",
    "hun": "HU",
    "rou": "RO",
    "bgr": "BG",
    "hrv": "HR",
    "svn": "SI",
    "svk": "SK",
    "est": "EE",
    "lva": "LV",
    "ltu": "LT",
    "mlt": "MT",
    "cyp": "CY",
    "tur": "TR",
    "srb": "RS",
    "can": "CA",
    "mex": "MX",
    "bra": "BR",
    "arg": "AR",
    "aus": "AU",
    "nzl": "NZ",
    "jpn": "JP",
    "chn": "CN",
    "hkg": "HK",
    "twn": "TW",
    "kor": "KR",
    "sgp": "SG",
    "mys": "MY",
    "tha": "TH",
    "idn": "ID",
    "phl": "PH",
    "vnm": "VN",
    "ind": "IN",
    "are": "AE",
    "isr": "IL",
    "zaf": "ZA",
}


def to_iso2(value: str | None) -> str:
    """
    Canonicalize any country string to ISO 2-letter. Returns '' for
    empty / unknown so the caller can decide whether to drop or keep.
    """
    if not value:
        return ""
    v = value.strip()
    if not v:
        return ""
    # Already an ISO-2 we know about
    upper = v.upper()
    if len(upper) == 2 and upper in COUNTRIES:
        return upper
    # Alias lookup (case-insensitive)
    iso = _ALIAS_TO_ISO2.get(v.lower())
    if iso:
        return iso
    # Last shot: maybe it's a 2-letter we don't have in COUNTRIES yet
    if len(upper) == 2 and upper.isalpha():
        return upper
    return ""  # unknown — caller may keep the original


def name_for(code: str | None) -> str:
    """Display name for an ISO-2 code. Falls back to the code itself."""
    if not code:
        return ""
    return COUNTRIES.get(code.upper(), code)
