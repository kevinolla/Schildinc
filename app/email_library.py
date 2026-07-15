"""Starter cold-email templates for Schild Inc.

Three clean formats, each localized (EN / NL / DE), written to read like a real
person wrote them — plain language, short, no marketing buzzwords, one clear ask.

The three formats (a simple, effective cold sequence):
  1. Cold intro       — who we are + one concrete offer.
  2. Follow-up        — a short, polite bump with an easy way out.
  3. Free sample      — offer a no-cost sample/mockup with their own logo.

Sector-adaptive: {{product_line}} and {{craft_word}} are filled per recipient
(bike / woodwork / furniture / steel) by app/email_engine.py, so ONE template
reads naturally for a bike shop, a woodworker, a furniture maker or a steel
workshop. {{opener}} adds a city line. All are empty-safe.

Merge fields:
    {{greeting_name}} {{opener}} {{product_line}} {{craft_word}} {{company_name}}
    {{city}} {{country}} {{sender_name}} {{sender_title}} {{company_website}}
    {{reply_to}} {{company_legal_name}} {{company_address}} {{unsubscribe_url}}

Bump STARTER_SEED_VERSION when editing; the seeder re-writes starter rows whose
version is lower and hides any starter no longer in this set.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EmailTemplate

STARTER_SEED_VERSION = 4

COMMON_FIELDS = (
    "greeting_name, opener, product_line, craft_word, company_name, city, "
    "country, sender_name, sender_title, reply_to, company_address, company_website"
)


def _cta(label: str) -> str:
    return f"""\
<table role="presentation" cellspacing="0" cellpadding="0" style="margin:6px 0 4px 0;">
  <tr><td style="border-radius:8px;background:#101010;">
    <a href="{{{{company_website}}}}" style="display:inline-block;padding:12px 24px;font-size:15px;font-weight:600;color:#e4c977;text-decoration:none;border-radius:8px;">{label}</a>
  </td></tr></table>"""


def _wrap(preheader: str, inner_html: str) -> str:
    """Clean, lightweight branded shell — readable type, honest signature+footer."""
    return f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f5f4ef;font-family:'Montserrat','Helvetica Neue',Arial,sans-serif;color:#20272e;">
    <span style="display:none!important;opacity:0;color:#f5f4ef;height:0;overflow:hidden;">{preheader}</span>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f5f4ef;padding:24px 0;">
      <tr><td align="center">
        <table role="presentation" width="580" cellspacing="0" cellpadding="0" style="width:580px;max-width:94%;background:#ffffff;border-radius:14px;border:1px solid #e7e1d6;">
          <tr><td style="background:#101010;padding:18px 32px;">
            <span style="color:#e4c977;font-size:18px;font-weight:bold;letter-spacing:0.5px;">SCHILD INC</span>
            <span style="color:#9b9483;font-size:12px;float:right;padding-top:5px;">Metal labels &amp; branded accessories</span>
          </td></tr>
          <tr><td style="padding:28px 32px 6px 32px;font-size:16px;line-height:1.7;color:#20272e;">
            {inner_html}
          </td></tr>
          <tr><td style="padding:6px 32px 22px 32px;font-size:16px;line-height:1.6;color:#20272e;">
            {{{{sender_name}}}}<br>
            <span style="color:#5b6470;font-size:14px;">{{{{sender_title}}}} · Schild Inc</span><br>
            <a href="{{{{company_website}}}}" style="color:#946f16;text-decoration:none;">{{{{company_website}}}}</a>
          </td></tr>
          <tr><td style="padding:0 32px 22px 32px;">
            <div style="border-top:1px solid #e7dfd0;padding-top:12px;color:#9aa0a6;font-size:12px;line-height:1.6;">
              {{{{company_legal_name}}}} · {{{{company_address}}}}<br>
              You're receiving this because we thought Schild Inc might be useful to {{{{company_name}}}}.
              Not relevant? <a href="{{{{unsubscribe_url}}}}" style="color:#9aa0a6;">Unsubscribe here</a> and we won't email again.
            </div>
          </td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""


def _text(body: str) -> str:
    return (body.rstrip()
            + "\n\n{{sender_name}}\n{{sender_title}}, Schild Inc\n{{company_website}}\n\n"
            + "--\n{{company_legal_name}} · {{company_address}}\n"
            + "Not relevant? Unsubscribe: {{unsubscribe_url}}")


def _op():  # optional city opener line (empty-safe)
    return '<p style="margin:0 0 16px 0;">{{opener}}</p>\n'


# ══ ENGLISH ══════════════════════════════════════════════════════════════════
INTRO_EN = _wrap("A small detail that makes your work look finished",
    _op() + """<p style="margin:0 0 16px 0;">Hi {{greeting_name}},</p>
<p style="margin:0 0 16px 0;">I'll keep this short. We make metal labels and small branded parts at Schild Inc, and we work with a lot of {{craft_word}}. The idea is simple: your own name and logo on {{product_line}}, not just the maker's.</p>
<p style="margin:0 0 18px 0;">It's a small touch, but it makes the finished work look a step more professional — and customers notice it.</p>
""" + _cta("See a few examples") +
    """<p style="margin:14px 0 4px 0;">Happy to send you a free sample with your logo so you can see it in person. Worth a look?</p>""")
INTRO_EN_T = _text("{{opener}}\n\nHi {{greeting_name}},\n\n"
    "I'll keep this short. We make metal labels and small branded parts at Schild Inc, and we work "
    "with a lot of {{craft_word}}. The idea is simple: your own name and logo on {{product_line}}, "
    "not just the maker's.\n\nIt's a small touch, but it makes the finished work look a step more "
    "professional. Happy to send a free sample with your logo. Worth a look? Just reply to this email.")

FOLLOW_EN = _wrap("Quick follow-up",
    """<p style="margin:0 0 16px 0;">Hi {{greeting_name}},</p>
<p style="margin:0 0 16px 0;">Just bringing my note back to the top in case it got buried — it happens to all of us.</p>
<p style="margin:0 0 4px 0;">The offer still stands: a free sample label with your own logo, no cost and no obligation. If it's not for you, a quick "no thanks" is completely fine and I'll leave it there.</p>""")
FOLLOW_EN_T = _text("Hi {{greeting_name}},\n\nJust bringing my note back to the top in case it got "
    "buried. The offer still stands: a free sample label with your own logo, no cost and no obligation. "
    "If it's not for you, a quick \"no thanks\" is completely fine.")

SAMPLE_EN = _wrap("A free sample with your logo",
    _op() + """<p style="margin:0 0 16px 0;">Hi {{greeting_name}},</p>
<p style="margin:0 0 16px 0;">Rather than explain it, I'd rather just show you. We'll make a <strong>free sample label with your own logo</strong> and send it to you — no cost, nothing to sign.</p>
<p style="margin:0 0 18px 0;">You hold it, see the quality, and decide if it fits {{product_line}}. Most {{craft_word}} we work with keep them on hand once they've felt one.</p>
""" + _cta("Request my free sample") +
    """<p style="margin:14px 0 4px 0;">Just reply with your logo and address, and I'll take care of the rest.</p>""")
SAMPLE_EN_T = _text("{{opener}}\n\nHi {{greeting_name}},\n\nRather than explain it, I'd rather show you. "
    "We'll make a free sample label with your own logo and send it to you — no cost, nothing to sign. "
    "You see the quality and decide if it fits {{product_line}}. Just reply with your logo and address.")


# ══ DUTCH (NL) ═══════════════════════════════════════════════════════════════
INTRO_NL = _wrap("Een klein detail dat uw werk af maakt",
    _op() + """<p style="margin:0 0 16px 0;">Hallo {{greeting_name}},</p>
<p style="margin:0 0 16px 0;">Ik houd het kort. Bij Schild Inc maken we metalen labels en kleine merkonderdelen, en we werken met veel {{craft_word}}. Het idee is simpel: uw eigen naam en logo op {{product_line}}, niet alleen dat van de fabrikant.</p>
<p style="margin:0 0 18px 0;">Het is een klein detail, maar het maakt het eindresultaat net wat professioneler — en klanten zien dat.</p>
""" + _cta("Bekijk een paar voorbeelden") +
    """<p style="margin:14px 0 4px 0;">Ik stuur u graag een gratis voorbeeld met uw logo, zodat u het in het echt ziet. Iets om naar te kijken?</p>""")
INTRO_NL_T = _text("{{opener}}\n\nHallo {{greeting_name}},\n\nIk houd het kort. Bij Schild Inc maken we "
    "metalen labels en kleine merkonderdelen, en we werken met veel {{craft_word}}. Het idee is simpel: "
    "uw eigen naam en logo op {{product_line}}, niet alleen dat van de fabrikant.\n\nHet is een klein "
    "detail, maar het maakt het eindresultaat net wat professioneler. Ik stuur u graag een gratis "
    "voorbeeld met uw logo. Iets om naar te kijken? U kunt gewoon op deze e-mail antwoorden.")

FOLLOW_NL = _wrap("Korte herinnering",
    """<p style="margin:0 0 16px 0;">Hallo {{greeting_name}},</p>
<p style="margin:0 0 16px 0;">Ik breng mijn bericht even opnieuw onder de aandacht, voor het geval het is ondergesneeuwd — dat gebeurt iedereen.</p>
<p style="margin:0 0 4px 0;">Het aanbod staat nog: een gratis voorbeeldlabel met uw eigen logo, zonder kosten en zonder verplichting. Past het niet, dan is een kort "nee, bedankt" helemaal prima en laat ik het daarbij.</p>""")
FOLLOW_NL_T = _text("Hallo {{greeting_name}},\n\nIk breng mijn bericht even opnieuw onder de aandacht. "
    "Het aanbod staat nog: een gratis voorbeeldlabel met uw eigen logo, zonder kosten en zonder "
    "verplichting. Past het niet, dan is een kort \"nee, bedankt\" helemaal prima.")

SAMPLE_NL = _wrap("Een gratis voorbeeld met uw logo",
    _op() + """<p style="margin:0 0 16px 0;">Hallo {{greeting_name}},</p>
<p style="margin:0 0 16px 0;">In plaats van het uit te leggen, laat ik het u liever gewoon zien. We maken een <strong>gratis voorbeeldlabel met uw eigen logo</strong> en sturen het naar u op — zonder kosten, niets te tekenen.</p>
<p style="margin:0 0 18px 0;">U houdt het vast, ziet de kwaliteit, en bepaalt of het bij {{product_line}} past. De meeste {{craft_word}} met wie we werken houden ze daarna standaard op voorraad.</p>
""" + _cta("Vraag mijn gratis voorbeeld aan") +
    """<p style="margin:14px 0 4px 0;">Stuur gewoon uw logo en adres terug, dan regel ik de rest.</p>""")
SAMPLE_NL_T = _text("{{opener}}\n\nHallo {{greeting_name}},\n\nIn plaats van het uit te leggen, laat ik het "
    "u liever zien. We maken een gratis voorbeeldlabel met uw eigen logo en sturen het op — zonder kosten, "
    "niets te tekenen. U ziet de kwaliteit en bepaalt of het bij {{product_line}} past. Stuur uw logo en adres terug.")


# ══ GERMAN (DE) ══════════════════════════════════════════════════════════════
INTRO_DE = _wrap("Ein kleines Detail, das Ihre Arbeit fertig wirken lässt",
    _op() + """<p style="margin:0 0 16px 0;">Hallo {{greeting_name}},</p>
<p style="margin:0 0 16px 0;">Ich fasse mich kurz. Bei Schild Inc fertigen wir Metallschilder und kleine Markenteile, und wir arbeiten mit vielen {{craft_word}}. Die Idee ist einfach: Ihr eigener Name und Ihr Logo auf {{product_line}}, nicht nur das des Herstellers.</p>
<p style="margin:0 0 18px 0;">Ein kleines Detail — aber es lässt das fertige Werk gleich eine Spur professioneller wirken, und Kunden bemerken das.</p>
""" + _cta("Ein paar Beispiele ansehen") +
    """<p style="margin:14px 0 4px 0;">Gerne schicke ich Ihnen ein kostenloses Muster mit Ihrem Logo, damit Sie es in echt sehen. Einen Blick wert?</p>""")
INTRO_DE_T = _text("{{opener}}\n\nHallo {{greeting_name}},\n\nIch fasse mich kurz. Bei Schild Inc fertigen "
    "wir Metallschilder und kleine Markenteile, und wir arbeiten mit vielen {{craft_word}}. Die Idee ist "
    "einfach: Ihr eigener Name und Ihr Logo auf {{product_line}}, nicht nur das des Herstellers.\n\n"
    "Gerne schicke ich Ihnen ein kostenloses Muster mit Ihrem Logo. Einen Blick wert? Antworten Sie einfach auf diese E-Mail.")

FOLLOW_DE = _wrap("Kurze Erinnerung",
    """<p style="margin:0 0 16px 0;">Hallo {{greeting_name}},</p>
<p style="margin:0 0 16px 0;">Ich hole meine Nachricht kurz wieder nach oben, falls sie untergegangen ist — das passiert uns allen.</p>
<p style="margin:0 0 4px 0;">Das Angebot steht weiterhin: ein kostenloses Musterschild mit Ihrem eigenen Logo, ohne Kosten und ohne Verpflichtung. Passt es nicht, genügt ein kurzes „nein, danke" — dann lasse ich es dabei.</p>""")
FOLLOW_DE_T = _text("Hallo {{greeting_name}},\n\nIch hole meine Nachricht kurz wieder nach oben. Das Angebot "
    "steht weiterhin: ein kostenloses Musterschild mit Ihrem eigenen Logo, ohne Kosten und ohne Verpflichtung. "
    "Passt es nicht, genügt ein kurzes „nein, danke\".")

SAMPLE_DE = _wrap("Ein kostenloses Muster mit Ihrem Logo",
    _op() + """<p style="margin:0 0 16px 0;">Hallo {{greeting_name}},</p>
<p style="margin:0 0 16px 0;">Statt es zu erklären, zeige ich es Ihnen lieber. Wir fertigen ein <strong>kostenloses Musterschild mit Ihrem eigenen Logo</strong> und schicken es Ihnen zu — ohne Kosten, nichts zu unterschreiben.</p>
<p style="margin:0 0 18px 0;">Sie halten es in der Hand, sehen die Qualität und entscheiden, ob es zu {{product_line}} passt. Die meisten {{craft_word}}, mit denen wir arbeiten, haben sie danach immer vorrätig.</p>
""" + _cta("Mein kostenloses Muster anfordern") +
    """<p style="margin:14px 0 4px 0;">Schicken Sie einfach Ihr Logo und Ihre Adresse zurück, um den Rest kümmere ich mich.</p>""")
SAMPLE_DE_T = _text("{{opener}}\n\nHallo {{greeting_name}},\n\nStatt es zu erklären, zeige ich es Ihnen lieber. "
    "Wir fertigen ein kostenloses Musterschild mit Ihrem eigenen Logo und schicken es Ihnen zu — ohne Kosten, "
    "nichts zu unterschreiben. Sie sehen die Qualität und entscheiden, ob es zu {{product_line}} passt. "
    "Schicken Sie Ihr Logo und Ihre Adresse zurück.")


STARTER_TEMPLATES = [
    # Format 1 — Cold intro
    {"name": "1. Cold intro (English)", "category": "cold",
     "subject": "A small detail that finishes {{company_name}}'s work",
     "body_html": INTRO_EN, "body_text": INTRO_EN_T,
     "description": "Format 1 of 3 — first touch. Sector-adaptive (bike/wood/furniture/steel), English."},
    {"name": "1. Cold intro (Nederlands)", "category": "cold",
     "subject": "Een klein detail dat het werk van {{company_name}} af maakt",
     "body_html": INTRO_NL, "body_text": INTRO_NL_T,
     "description": "Format 1 van 3 — eerste contact. Past zich aan per sector, Nederlands."},
    {"name": "1. Cold intro (Deutsch)", "category": "cold",
     "subject": "Ein kleines Detail, das die Arbeit von {{company_name}} fertig wirken lässt",
     "body_html": INTRO_DE, "body_text": INTRO_DE_T,
     "description": "Format 1 von 3 — Erstkontakt. Passt sich je Branche an, Deutsch."},
    # Format 2 — Follow-up
    {"name": "2. Follow-up (English)", "category": "followup",
     "subject": "Quick follow-up, {{greeting_name}}",
     "body_html": FOLLOW_EN, "body_text": FOLLOW_EN_T,
     "description": "Format 2 of 3 — polite bump ~5 days later, English."},
    {"name": "2. Follow-up (Nederlands)", "category": "followup",
     "subject": "Korte herinnering, {{greeting_name}}",
     "body_html": FOLLOW_NL, "body_text": FOLLOW_NL_T,
     "description": "Format 2 van 3 — vriendelijke herinnering, Nederlands."},
    {"name": "2. Follow-up (Deutsch)", "category": "followup",
     "subject": "Kurze Erinnerung, {{greeting_name}}",
     "body_html": FOLLOW_DE, "body_text": FOLLOW_DE_T,
     "description": "Format 2 von 3 — freundliche Erinnerung, Deutsch."},
    # Format 3 — Free sample offer
    {"name": "3. Free sample (English)", "category": "cold",
     "subject": "A free sample with {{company_name}}'s logo",
     "body_html": SAMPLE_EN, "body_text": SAMPLE_EN_T,
     "description": "Format 3 of 3 — free-sample offer. Sector-adaptive, English."},
    {"name": "3. Free sample (Nederlands)", "category": "cold",
     "subject": "Een gratis voorbeeld met het logo van {{company_name}}",
     "body_html": SAMPLE_NL, "body_text": SAMPLE_NL_T,
     "description": "Format 3 van 3 — gratis voorbeeld. Past zich aan per sector, Nederlands."},
    {"name": "3. Free sample (Deutsch)", "category": "cold",
     "subject": "Ein kostenloses Muster mit dem Logo von {{company_name}}",
     "body_html": SAMPLE_DE, "body_text": SAMPLE_DE_T,
     "description": "Format 3 von 3 — kostenloses Muster. Passt sich je Branche an, Deutsch."},
]


def seed_starter_templates(session: Session) -> int:
    """Insert/update the 3 formats x 3 languages; hide any starter not in this set."""
    touched = 0
    for spec in STARTER_TEMPLATES:
        existing = session.scalar(select(EmailTemplate).where(
            EmailTemplate.name == spec["name"], EmailTemplate.is_starter.is_(True)))
        if existing is None:
            session.add(EmailTemplate(
                name=spec["name"], category=spec["category"], description=spec["description"],
                subject=spec["subject"], body_html=spec["body_html"], body_text=spec["body_text"],
                merge_fields=COMMON_FIELDS, is_active=True, is_starter=True,
                seed_version=STARTER_SEED_VERSION))
            touched += 1
        elif existing.seed_version < STARTER_SEED_VERSION:
            existing.category = spec["category"]; existing.description = spec["description"]
            existing.subject = spec["subject"]; existing.body_html = spec["body_html"]
            existing.body_text = spec["body_text"]; existing.merge_fields = COMMON_FIELDS
            existing.seed_version = STARTER_SEED_VERSION; existing.is_active = True
            touched += 1

    # Retire every other starter (old bike-only templates, VIP, warm, etc.).
    current = {s["name"] for s in STARTER_TEMPLATES}
    for row in session.scalars(select(EmailTemplate).where(
            EmailTemplate.is_starter.is_(True), EmailTemplate.is_active.is_(True),
            EmailTemplate.name.notin_(current))).all():
        row.is_active = False
        touched += 1

    if touched:
        session.commit()
    return touched
