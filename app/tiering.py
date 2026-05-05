from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.models import Prospect

if TYPE_CHECKING:
    from app.models import KvkCompany


@dataclass
class TierDecision:
    bike_shop_tier: str
    bike_shop_segment: str
    outreach_priority: str
    headquarters_required: bool
    franchise_or_buying_group: str
    tier_reason: str
    recommended_sales_angle: str
    recommended_contact_type: str
    inferred_use_case: str


def apply_bike_tier(prospect: Prospect) -> TierDecision:
    if prospect.manual_tier_override:
        return TierDecision(
            bike_shop_tier=prospect.bike_shop_tier,
            bike_shop_segment=prospect.bike_shop_segment,
            outreach_priority=prospect.outreach_priority,
            headquarters_required=prospect.headquarters_required,
            franchise_or_buying_group=prospect.franchise_or_buying_group,
            tier_reason=prospect.tier_reason,
            recommended_sales_angle=prospect.recommended_sales_angle,
            recommended_contact_type=prospect.recommended_contact_type,
            inferred_use_case=prospect.custom_use_case or prospect.recommended_sales_angle,
        )

    text = " ".join(
        [
            prospect.company_name or "",
            prospect.company_type or "",
            prospect.website_summary or "",
            prospect.discovery_highlights or "",
            prospect.notes or "",
            prospect.website or "",
        ]
    ).lower()

    def has(*keywords: str) -> bool:
        return any(keyword in text for keyword in keywords)

    buying_group = ""
    if has("bike totaal", "biketotaal"):
        buying_group = "Bike Totaal / buying-group context"
    elif has("dynamo retail", "buying group", "inkoopgroep", "franchise", "dealer network"):
        buying_group = "Franchise / buying-group context"

    if has("mantel", "store locator", "many stores", "filialen", "branches", "head office", "hoofdkantoor", "group", "retail group"):
        decision = TierDecision(
            bike_shop_tier="Hard to Reach",
            bike_shop_segment="Chain / Buying Group",
            outreach_priority="Medium",
            headquarters_required=True,
            franchise_or_buying_group=buying_group or "Chain / HQ-led structure",
            tier_reason="Signals suggest a multi-location or centrally controlled retail structure.",
            recommended_sales_angle="Central purchasing, brand consistency, scalable rollout",
            recommended_contact_type="Head Office",
            inferred_use_case="Consistente branding over meerdere winkels of vestigingen.",
        )
    elif has("giant store", "trek store", "cube store", "brand store", "official concept store", "specialized store"):
        decision = TierDecision(
            bike_shop_tier="Brand Store",
            bike_shop_segment="Single Brand Store",
            outreach_priority="Low",
            headquarters_required=True,
            franchise_or_buying_group=buying_group,
            tier_reason="The store looks like a manufacturer-led or single-brand format.",
            recommended_sales_angle="Central branding partnership only",
            recommended_contact_type="Brand HQ",
            inferred_use_case="Alleen relevant als er een centraal merk- of hoofdkantoorbesluit nodig is.",
        )
    elif has("custom motorcycle", "motorcycle", "motoren", "sport bike only", "racefiets specialist", "triathlon only"):
        decision = TierDecision(
            bike_shop_tier="Low Fit",
            bike_shop_segment="Niche / Non-core",
            outreach_priority="Very Low",
            headquarters_required=False,
            franchise_or_buying_group=buying_group,
            tier_reason="The segment looks outside Schild's strongest mudguard/label opportunity.",
            recommended_sales_angle="Usually not a target",
            recommended_contact_type="Manual Review",
            inferred_use_case="Geen standaard eerste outreach.",
        )
    elif has("second hand", "used bikes", "occasions", "tweedehands", "budget", "goedkope fietsen", "bankruptcy stock", "outlet"):
        decision = TierDecision(
            bike_shop_tier="Mid Tier",
            bike_shop_segment="Used / Volume Driven",
            outreach_priority="Low",
            headquarters_required=False,
            franchise_or_buying_group=buying_group,
            tier_reason="The store appears price-driven or second-hand heavy, so branding urgency is lower.",
            recommended_sales_angle="Simple affordable branding, practical logo visibility, basic upsell only",
            recommended_contact_type="Owner",
            inferred_use_case="Eenvoudige branding en zichtbaarheid op fietsen of accessoires.",
        )
    elif has("repair", "reparatie", "workshop", "fietsenmaker", "service only", "maintenance") and not has("premium", "showroom", "e-bike", "accessoires", "helmets", "bags"):
        decision = TierDecision(
            bike_shop_tier="Low Tier",
            bike_shop_segment="Repair First",
            outreach_priority="Very Low",
            headquarters_required=False,
            franchise_or_buying_group=buying_group,
            tier_reason="The store looks mainly repair-focused with lower branding urgency.",
            recommended_sales_angle="Usually not a priority lead",
            recommended_contact_type="Owner",
            inferred_use_case="Geen standaard outreach, alleen handmatige beoordeling.",
        )
    else:
        decision = TierDecision(
            bike_shop_tier="Good Tier",
            bike_shop_segment="Premium / Professional Bike Store",
            outreach_priority="High",
            headquarters_required=False,
            franchise_or_buying_group=buying_group,
            tier_reason="The store shows strong retail/service signals and looks suitable for premium branding or branded accessories.",
            recommended_sales_angle="Professional branding, premium look, add-on sales, stronger in-store presentation",
            recommended_contact_type="Owner/Manager",
            inferred_use_case="Professionelere uitstraling en extra add-on verkoop met eigen branding.",
        )

    prospect.bike_shop_tier = decision.bike_shop_tier
    prospect.bike_shop_segment = decision.bike_shop_segment
    prospect.outreach_priority = decision.outreach_priority
    prospect.headquarters_required = decision.headquarters_required
    prospect.franchise_or_buying_group = decision.franchise_or_buying_group
    prospect.tier_reason = decision.tier_reason
    prospect.recommended_sales_angle = decision.recommended_sales_angle
    prospect.recommended_contact_type = decision.recommended_contact_type
    if not prospect.custom_use_case:
        prospect.custom_use_case = decision.inferred_use_case
    return decision


def score_kvk_company_tier(company: "KvkCompany") -> TierDecision:
    """Same heuristics as apply_bike_tier but for KvkCompany (no website_summary available at import time)."""
    text = " ".join([
        company.company_name or "",
        company.website or "",
        company.notes or "",
        str(company.main_activity_description or ""),
    ]).lower()

    def has(*keywords: str) -> bool:
        return any(k in text for k in keywords)

    buying_group = ""
    if has("bike totaal", "biketotaal"):
        buying_group = "Bike Totaal / buying-group context"
    elif has("franchise", "dealer network", "inkoopgroep", "buying group"):
        buying_group = "Franchise / buying-group context"

    establishments = getattr(company, "establishments_count", 1) or 1

    if establishments >= 5 or has("mantel", "store locator", "filialen", "head office", "hoofdkantoor", "retail group"):
        return TierDecision(
            bike_shop_tier="Hard to Reach",
            bike_shop_segment="Chain / Buying Group",
            outreach_priority="Medium",
            headquarters_required=True,
            franchise_or_buying_group=buying_group or "Chain / HQ-led structure",
            tier_reason=f"Multi-location or centrally controlled structure ({establishments} establishments).",
            recommended_sales_angle="Central purchasing, brand consistency, scalable rollout",
            recommended_contact_type="Head Office",
            inferred_use_case="Consistente branding over meerdere winkels of vestigingen.",
        )
    if has("giant store", "trek store", "cube store", "brand store", "specialized store"):
        return TierDecision(
            bike_shop_tier="Brand Store",
            bike_shop_segment="Single Brand Store",
            outreach_priority="Low",
            headquarters_required=True,
            franchise_or_buying_group=buying_group,
            tier_reason="Manufacturer-led or single-brand format.",
            recommended_sales_angle="Central branding partnership only",
            recommended_contact_type="Brand HQ",
            inferred_use_case="Alleen relevant als er een centraal merk- of hoofdkantoorbesluit nodig is.",
        )
    if has("custom motorcycle", "motorcycle", "motoren", "sport bike only", "triathlon only"):
        return TierDecision(
            bike_shop_tier="Low Fit",
            bike_shop_segment="Niche / Non-core",
            outreach_priority="Very Low",
            headquarters_required=False,
            franchise_or_buying_group=buying_group,
            tier_reason="Segment outside Schild's core mudguard/label opportunity.",
            recommended_sales_angle="Usually not a target",
            recommended_contact_type="Manual Review",
            inferred_use_case="Geen standaard eerste outreach.",
        )
    if has("second hand", "tweedehands", "occasions", "budget", "goedkope fietsen", "outlet"):
        return TierDecision(
            bike_shop_tier="Mid Tier",
            bike_shop_segment="Used / Volume Driven",
            outreach_priority="Low",
            headquarters_required=False,
            franchise_or_buying_group=buying_group,
            tier_reason="Price-driven or second-hand focus.",
            recommended_sales_angle="Simple affordable branding, practical logo visibility",
            recommended_contact_type="Owner",
            inferred_use_case="Eenvoudige branding en zichtbaarheid op fietsen of accessoires.",
        )
    if has("reparatie", "fietsenmaker", "repair", "workshop") and not has("premium", "showroom", "e-bike"):
        return TierDecision(
            bike_shop_tier="Low Tier",
            bike_shop_segment="Repair First",
            outreach_priority="Very Low",
            headquarters_required=False,
            franchise_or_buying_group=buying_group,
            tier_reason="Mainly repair-focused, lower branding urgency.",
            recommended_sales_angle="Usually not a priority lead",
            recommended_contact_type="Owner",
            inferred_use_case="Geen standaard outreach, alleen handmatige beoordeling.",
        )
    return TierDecision(
        bike_shop_tier="Good Tier",
        bike_shop_segment="Premium / Professional Bike Store",
        outreach_priority="High",
        headquarters_required=False,
        franchise_or_buying_group=buying_group,
        tier_reason="Retail bike store with no chain/brand-store signals — likely core Schild target.",
        recommended_sales_angle="Professional branding, premium look, add-on sales",
        recommended_contact_type="Owner/Manager",
        inferred_use_case="Professionelere uitstraling en extra add-on verkoop met eigen branding.",
    )
