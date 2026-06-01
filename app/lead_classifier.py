"""
Lead Sector Classifier
======================
Fast, deterministic keyword classifier that buckets every lead /
customer / KVK row into one of the 11 Schild Inc sectors. No API,
no LLM, no Playwright — pure regex + scoring so 10k rows can be
classified in seconds.

Used by:
  - Background daemon in app/facebook_leads.py — runs every 60s,
    classifies any facebook_leads row whose main_sector is empty
  - POST /api/leads/webform — classifies inline before saving
  - Importer — classifies on first insert

Sector vocabulary intentionally mixes Dutch / English / German /
French keywords since the lead pool spans NL/DE/FR/BE markets.
"""
from __future__ import annotations

import re
from typing import Iterable

# ── Canonical sectors (match Customer.main_sector values exactly) ──────────
SECTORS = [
    "Bike",
    "Candles",
    "Woodwork",
    "Furniture",
    "SteelWork",
    "Music",
    "Fashion",
    "Liquor & Bottles",
    "Service",
    "Art",
    "Uncategorized",
]

# Keyword vocab — weight-1 per match. Multi-language so leads from
# NL/DE/FR/UK markets all bucket correctly. Keep ALL lowercase.
SECTOR_KEYWORDS: dict[str, list[str]] = {
    "Bike": [
        "bike", "bikes", "bicycle", "bicycles", "cycling", "cyclist", "cycle",
        "fiets", "fietsen", "rijwiel", "rijwielen", "ebike", "e-bike",
        "fahrrad", "rad", "radsport",
        "vélo", "velo", "cycles",
        "mountainbike", "mtb", "racefiets", "bakfiets",
        "tweewieler", "tweewielers",
        "wielersport", "wieler", "wielers",
        "swapfiets", "trek", "giant", "batavus", "gazelle",
    ],
    "Candles": [
        "candle", "candles", "kaars", "kaarsen", "kerze", "kerzen",
        "bougie", "bougies", "wax", "wick", "waxmelt", "waxmelts",
        "soywax", "soy wax",
    ],
    "Woodwork": [
        "wood", "wooden", "woodwork", "woodworking",
        "hout", "houten", "houtwerk", "houtbewerking",
        "carpentry", "carpenter", "joinery", "joiner",
        "timmerwerk", "timmerman", "timmer",
        "schreiner", "schreinerei", "holz", "tischler",
        "menuisier", "menuiserie", "ebéniste", "ebeniste",
        "timber", "lumber", "plank", "planks", "sawmill",
    ],
    "Furniture": [
        "furniture", "meubel", "meubels", "meubelmaker", "meubelmakerij",
        "möbel", "moebel", "schreinerei",
        "meubles", "mobilier",
        "interior", "interieur", "interiors",
        "chair", "stoel", "stoelen",
        "table", "tafel", "tafels",
        "sofa", "couch", "bank",
        "kast", "kasten", "cabinet", "cabinets",
        "atelier mobilier",
    ],
    "SteelWork": [
        "steel", "staal", "stahl", "acier",
        "metal", "metals", "metaal", "metalen", "métal", "metall",
        "weld", "welding", "lassen", "lasser", "lasbedrijf",
        "smid", "smederij", "smith", "blacksmith", "schmied", "schmiede",
        "iron", "ijzer", "ijzerwerk",
        "metaalbewerking", "metal fabrication", "metalwork", "metalworking",
        "fabrication", "fabricator",
        "construction métallique", "construction metallique",
        "rvs", "stainless",
        "door", "doors", "deur", "deuren", "porte", "portes",
        "gate", "poort", "hek", "hekken", "hekwerk",
    ],
    "Music": [
        "music", "muziek", "musik", "musique",
        "guitar", "guitare", "gitaar", "gitarre",
        "drum", "drums", "drummer",
        "piano", "keyboard",
        "instrument", "instruments",
        "studio", "recording", "muziekstudio",
        "band", "bass", "violin", "viool", "violine",
        "amplifier", "amp",
        "concert", "festival",
    ],
    "Fashion": [
        "fashion", "mode", "moda",
        "kleding", "kleidung", "vetement", "vêtement", "vêtements", "vetements",
        "shirt", "shirts", "tshirt", "t-shirt",
        "clothing", "clothes", "apparel", "wear", "wears",
        "boutique", "designer",
        "atelier couture", "couture", "tailor", "snijder",
        "schoen", "schoenen", "shoes", "schuhe", "chaussures",
        "tas", "tassen", "bag", "bags", "tasche", "taschen", "sac", "sacs",
        "leder", "leather", "leer",
        "jeans", "denim", "knit", "wool",
        "streetwear", "sportswear",
    ],
    "Liquor & Bottles": [
        "wine", "wijn", "wein", "vin", "vins",
        "winery", "wijnhuis", "wijngaard", "weingut", "vignoble",
        "liquor", "spirits",
        "brouwerij", "brewery", "brauerei", "brasserie", "brewing",
        "distillery", "distilleerderij", "distillerie", "destillerie",
        "bottle", "bottles", "fles", "flessen", "flasche", "bouteille",
        "bier", "beer", "bière", "biere",
        "whisky", "whiskey", "bourbon", "scotch",
        "rum", "gin", "vodka", "tequila", "cognac",
        "champagne", "cava", "prosecco", "cider",
        "kombucha",
    ],
    "Service": [
        "service", "services", "consult", "consulting", "consultancy",
        "agency", "agence", "agentur", "bureau",
        "marketing", "advertising",
        "salon", "barber", "kapper", "kapsalon", "coiffeur",
        "spa", "wellness",
        "restaurant", "café", "cafe", "koffie",
        "hotel", "b&b", "bnb",
        "garage", "repair", "reparatie", "service center",
        "shop", "winkel", "store",
        "rental", "verhuur", "rent a",
        "tour", "tours", "touring",
        "training", "academy", "school", "instituut",
        "logistics", "transport",
        "cleaning", "schoonmaak",
        "real estate", "makelaar", "immobilien",
    ],
    "Art": [
        "art", "arts", "kunst", "künstler", "kunstenaar",
        "studio", "atelier",
        "gallery", "galerie", "galleri",
        "design", "designer", "designs",
        "creative", "creatief",
        "paint", "painter", "schilder", "schilderij",
        "sculpt", "sculpture", "sculpteur",
        "ceramic", "ceramics", "keramik", "keramiek", "pottery",
        "print", "prints", "printing",
        "photography", "photographer", "fotograaf", "fotografie",
        "illustration", "illustrator",
        "tattoo", "tatouage",
        "craft", "crafts", "ambacht",
    ],
}

# Compile per-sector regex once at import time — case-insensitive, word-boundary.
# Some keywords contain spaces / hyphens, so escape carefully.
def _compile_pattern(keywords: list[str]) -> re.Pattern:
    parts = []
    for kw in keywords:
        kw = kw.strip().lower()
        if not kw:
            continue
        escaped = re.escape(kw)
        # Word boundary around single words, looser around multi-word phrases
        if " " in kw or "-" in kw or "&" in kw:
            parts.append(escaped)
        else:
            parts.append(rf"\b{escaped}\b")
    return re.compile("|".join(parts), re.IGNORECASE)


_PATTERNS: dict[str, re.Pattern] = {
    sector: _compile_pattern(keywords)
    for sector, keywords in SECTOR_KEYWORDS.items()
}

# Looser pattern (no word boundary) for email-domain SLD text, where
# 'bike' inside 'bikecity' should still match. We only apply this
# to the domain-derived blob — the company-name text still uses
# strict word boundaries so we don't get false positives.
def _compile_loose(keywords: list[str]) -> re.Pattern:
    parts = []
    for kw in keywords:
        kw = kw.strip().lower()
        # Skip single/two-char keywords in loose mode (too noisy)
        if len(kw) < 4 or not kw.isalpha():
            continue
        parts.append(re.escape(kw))
    return re.compile("|".join(parts), re.IGNORECASE) if parts else re.compile("$^")


_LOOSE_PATTERNS: dict[str, re.Pattern] = {
    sector: _compile_loose(keywords)
    for sector, keywords in SECTOR_KEYWORDS.items()
}

# Domain TLDs we strip when scoring email domain — they're never a signal
_TLD_NOISE = {".com", ".nl", ".de", ".fr", ".be", ".eu", ".org", ".net", ".info", ".co"}


def _clean_domain_for_text(email: str) -> str:
    """Pull the domain root out of an email so the classifier sees the
    SLD (e.g. 'bike-shop.nl' → 'bike shop') as keyword fodder."""
    if not email or "@" not in email:
        return ""
    domain = email.split("@", 1)[1].lower()
    # Drop common TLD suffixes
    for tld in _TLD_NOISE:
        if domain.endswith(tld):
            domain = domain[: -len(tld)]
            break
    # Replace dots / hyphens with spaces so 'bike-shop' → 'bike shop'
    return domain.replace(".", " ").replace("-", " ").replace("_", " ")


def classify_sector(
    *texts: str | None,
    email: str | None = None,
    min_score: int = 1,
) -> tuple[str, int]:
    """
    Score every sector against the concatenated text + email-domain
    fodder. Return (best_sector, score). Falls back to ('Uncategorized', 0)
    if no sector hits the minimum threshold.

    `min_score` defaults to 1 = at least one keyword match wins.
    """
    # Strict-match text (company name + other free-text fields)
    strict_blob = " ".join([t for t in texts if t]).lower()
    # Loose-match text — just the email domain SLD, treated as a single
    # token where 'bike' inside 'bikecity' should still hit
    loose_blob = _clean_domain_for_text(email or "")
    if not strict_blob.strip() and not loose_blob.strip():
        return "Uncategorized", 0

    best_sector = "Uncategorized"
    best_score = 0
    for sector in SECTOR_KEYWORDS:
        score = 0
        if strict_blob:
            score += len(_PATTERNS[sector].findall(strict_blob))
        if loose_blob:
            score += len(_LOOSE_PATTERNS[sector].findall(loose_blob))
        if score > best_score:
            best_score = score
            best_sector = sector

    if best_score < min_score:
        return "Uncategorized", 0
    return best_sector, best_score


def classify_lead(lead) -> str:
    """
    Helper for a FacebookLead-like object: combines all the free-text
    fields the lead carries (name, company, industry, detailed info,
    estimated order size, form/ad/campaign names) plus the email domain.
    """
    sector, _ = classify_sector(
        getattr(lead, "company_name", None),
        getattr(lead, "full_name", None),
        getattr(lead, "industry", None),
        getattr(lead, "detailed_information", None),
        getattr(lead, "estimated_order_size", None),
        getattr(lead, "form_name", None),
        getattr(lead, "ad_name", None),
        getattr(lead, "campaign_name", None),
        email=getattr(lead, "email", None),
    )
    return sector


def classify_iter(rows: Iterable) -> int:
    """
    Mutate every row in-place: set `main_sector` from classify_lead().
    Returns the number that got a non-Uncategorized result.
    """
    n_classified = 0
    for row in rows:
        sector = classify_lead(row)
        row.main_sector = sector
        if sector != "Uncategorized":
            n_classified += 1
    return n_classified
