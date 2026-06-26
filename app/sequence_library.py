"""Seed the 3 baseline cold-email templates + the default weekly sequence.

DESIGN_V2 Phase 3B. The copy is adapted from Schild's existing Klaviyo cold
emails (intro / proof follow-up / close), converted to the app's merge syntax
and tracking/unsubscribe injection. Each template stands on its own with ZERO
personalization; the sequence render layers approved personalization blocks on
top via the merge slots {{first_line}}, {{angle_block}}, {{cta_block}} (these
render empty when no personalization is available — render_merge drops unknown
/ empty fields). Idempotent: seeds once per settings.sequence_seed_version.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import EmailSequence, EmailTemplate, SequenceStep

_LOGO = "https://d3k81ch9hvuctc.cloudfront.net/company/SghQZd/images/62411def-2024-4d3c-9717-7a8ba89956b9.png"
_IMG_LABELS = "https://d3k81ch9hvuctc.cloudfront.net/company/SghQZd/images/31f93faa-5ad8-4f54-a866-c0f05403f61c.png"
_EXAMPLES_URL = "https://schildinc.com/nl/pages/fiets-spatbord-metalen-labels"

SEQUENCE_NAME = "Bike Cold — 3-step weekly (NL)"

_FOOTER = (
    '<hr style="border:none;border-top:1px solid #eee;margin:24px 0;">'
    '<p style="font-size:12px;color:#727272;text-align:center;line-height:1.5;">'
    '{{company_legal_name}} · {{company_address}}<br>'
    'Liever geen e-mails meer? <a href="{{unsubscribe_url}}" style="color:#727272;">Uitschrijven</a>.'
    '</p>'
)


def _wrap(inner: str) -> str:
    return (
        '<div style="font-family:\'Helvetica Neue\',Arial,sans-serif;font-size:15px;'
        'color:#0A0A0A;line-height:1.6;max-width:600px;margin:0 auto;">'
        f'<p style="text-align:center;"><img src="{_LOGO}" width="128" alt="Schild Inc Bike" '
        'style="max-width:128px;height:auto;"></p>'
        f'{inner}{_FOOTER}</div>'
    )


# ── Step 1 — Intro / awareness ───────────────────────────────────────────────
_STEP1_SUBJECT = "Metalen spatbord labels met uw logo — gratis ontwerp"
_STEP1_HTML = _wrap(
    "<p>Beste {{company_name}},</p>"
    "<p>{{first_line}}</p>"
    "<p>Ik ben Ruben, eigenaar van Schild Inc Bike. Misschien kent u ons al: inmiddels werken meer dan "
    "600 fietsenwinkels met onze labels en custom accessoires.</p>"
    "<p>Wij staan bekend om onze ‘‘Schildjes’’: metalen spatbord labels met het logo van de "
    "fietsenwinkel. Geen gewone stickers, maar professionele labels die de fiets een luxere uitstraling geven "
    "én uw winkelnaam zichtbaar houden op straat.</p>"
    f'<p style="text-align:center;"><img src="{_IMG_LABELS}" width="560" alt="Spatbord label voorbeelden" '
    'style="max-width:100%;height:auto;border-radius:8px;"></p>'
    "<p>Als kennismaking ontvangt u <strong>50 gratis labels</strong> bij een eerste bestelling vanaf 250 stuks. "
    "Uiteraard maken we het ontwerp met uw logo volledig gratis.</p>"
    "<p>Zal ik vrijblijvend een gratis ontwerp en prijsvoorbeeld voor uw winkel maken?</p>"
    "<p>{{cta_block}}</p>"
    f'<p>PS. Bekijk <a href="{_EXAMPLES_URL}">hier</a> een aantal spatbord label voorbeelden.</p>'
    "<p>Groet,<br>Ruben<br>Schild Inc Bike</p>"
)
_STEP1_TEXT = (
    "Beste {{company_name}},\n\n{{first_line}}\n\n"
    "Ik ben Ruben, eigenaar van Schild Inc Bike. Inmiddels werken meer dan 600 fietsenwinkels met onze "
    "metalen spatbord labels (‘Schildjes’) en custom accessoires.\n\n"
    "Als kennismaking: 50 gratis labels bij een eerste bestelling vanaf 250 stuks, en het ontwerp met uw "
    "logo maken we gratis. Zal ik vrijblijvend een gratis ontwerp en prijsvoorbeeld maken?\n\n"
    f"Voorbeelden: {_EXAMPLES_URL}\n\nGroet,\nRuben\nSchild Inc Bike\n\n"
    "{{company_legal_name}} · {{company_address}}\nUitschrijven: {{unsubscribe_url}}"
)

# ── Step 2 — Proof / relevance follow-up ─────────────────────────────────────
_STEP2_SUBJECT = "Even een prijsvoorbeeld voor uw winkel?"
_STEP2_HTML = _wrap(
    "<p>Beste {{company_name}},</p>"
    "<p>{{first_line}}</p>"
    "<p>Stuurde je vorige week een berichtje over metalen spatbord labels voor jouw winkel.</p>"
    "<p>Nog niet het juiste moment, of wil je even een prijsvoorbeeld zien?</p>"
    "<p>{{angle_block}}</p>"
    f'<p>PS. Bekijk <a href="{_EXAMPLES_URL}">hier</a> een aantal spatbord label voorbeelden.</p>'
    "<p>Met vriendelijke groet,<br>Ruben Jansen<br>Schild Inc Bike</p>"
)
_STEP2_TEXT = (
    "Beste {{company_name}},\n\n{{first_line}}\n\n"
    "Stuurde je vorige week een berichtje over metalen spatbord labels voor jouw winkel. "
    "Nog niet het juiste moment, of wil je even een prijsvoorbeeld zien?\n\n{{angle_block}}\n\n"
    f"Voorbeelden: {_EXAMPLES_URL}\n\nMet vriendelijke groet,\nRuben Jansen\nSchild Inc Bike\n\n"
    "{{company_legal_name}} · {{company_address}}\nUitschrijven: {{unsubscribe_url}}"
)

# ── Step 3 — Action / close loop ─────────────────────────────────────────────
_STEP3_SUBJECT = "Laatste berichtje over de spatbord labels"
_STEP3_HTML = _wrap(
    "<p>Beste {{company_name}},</p>"
    "<p>{{first_line}}</p>"
    "<p>Laat je nog even iets weten? Als ik niks van je hoor, val ik je niet meer lastig.</p>"
    "<p>Eerste bestelling vanaf 250 stuks, inclusief gratis digitaal ontwerp. Als het ooit relevant wordt: "
    '<a href="https://schildinc.com">schildinc.com</a> of gewoon reply.</p>'
    "<p>{{cta_block}}</p>"
    "<p>Succes met het seizoen,<br>Ruben Jansen<br>Schild Inc Bike</p>"
)
_STEP3_TEXT = (
    "Beste {{company_name}},\n\n{{first_line}}\n\n"
    "Laat je nog even iets weten? Als ik niks van je hoor, val ik je niet meer lastig. "
    "Eerste bestelling vanaf 250 stuks, inclusief gratis digitaal ontwerp. Als het ooit relevant wordt: "
    "schildinc.com of gewoon reply.\n\n{{cta_block}}\n\nSucces met het seizoen,\nRuben Jansen\nSchild Inc Bike\n\n"
    "{{company_legal_name}} · {{company_address}}\nUitschrijven: {{unsubscribe_url}}"
)

_BASELINES = [
    {"name": "Sequence Step 1 — Intro (NL bike)", "category": "cold",
     "subject": _STEP1_SUBJECT, "html": _STEP1_HTML, "text": _STEP1_TEXT, "level": "light"},
    {"name": "Sequence Step 2 — Proof follow-up (NL bike)", "category": "followup",
     "subject": _STEP2_SUBJECT, "html": _STEP2_HTML, "text": _STEP2_TEXT, "level": "medium"},
    {"name": "Sequence Step 3 — Close (NL bike)", "category": "followup",
     "subject": _STEP3_SUBJECT, "html": _STEP3_HTML, "text": _STEP3_TEXT, "level": "strong"},
]


def _upsert_template(session: Session, spec: dict) -> EmailTemplate:
    tpl = session.scalar(select(EmailTemplate).where(EmailTemplate.name == spec["name"]))
    if tpl is None:
        tpl = EmailTemplate(name=spec["name"])
        session.add(tpl)
    tpl.subject = spec["subject"]
    tpl.body_html = spec["html"]
    tpl.body_text = spec["text"]
    # category column exists on EmailTemplate (cold|warm|followup|vip)
    if hasattr(tpl, "category"):
        tpl.category = spec["category"]
    session.flush()
    return tpl


def seed_sequence_templates(session: Session) -> EmailSequence | None:
    """Idempotently seed the 3 baseline templates + the default sequence/steps.

    Re-seeds only when the existing default sequence's seed_version is behind
    settings.sequence_seed_version. Returns the sequence (or None on no-op skip
    when already current). Safe to call on every startup.
    """
    target_version = int(getattr(settings, "sequence_seed_version", 1))
    seq = session.scalar(select(EmailSequence).where(EmailSequence.name == SEQUENCE_NAME))
    if seq is not None and seq.seed_version >= target_version:
        return seq

    templates = [_upsert_template(session, spec) for spec in _BASELINES]

    if seq is None:
        seq = EmailSequence(name=SEQUENCE_NAME, sector="bike", is_default=True)
        session.add(seq)
        session.flush()

    seq.timezone_strategy = "lead_local"
    seq.cadence_rule = "weekly_wed_0700"
    seq.step_count = 3
    seq.is_active = True
    seq.is_default = True
    seq.seed_version = target_version

    # (Re)build the 3 steps to point at the freshly-seeded templates.
    existing = {s.step_number: s for s in seq.steps}
    weekday = int(getattr(settings, "sequence_send_weekday", 2))
    hour = int(getattr(settings, "sequence_send_hour_local", 7))
    gap = int(getattr(settings, "sequence_step_gap_days", 7))
    for i, (tpl, spec) in enumerate(zip(templates, _BASELINES), start=1):
        step = existing.get(i)
        if step is None:
            step = SequenceStep(sequence_id=seq.id, step_number=i)
            session.add(step)
        step.template_id = tpl.id
        step.subject_override = spec["subject"]
        step.send_weekday = weekday
        step.send_hour_local = hour
        step.gap_days = (0 if i == 1 else gap)
        step.personalization_level = spec["level"]
    session.flush()
    return seq
