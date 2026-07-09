"""Starter email templates for Schild Inc outreach.

Professional, brand-safe, GDPR/CAN-SPAM-conscious cold + follow-up templates,
written to be read comfortably by a busy, non-technical business owner:

  • Large, high-contrast type (16px base, 1.7 line-height) and short paragraphs.
  • Plain, human language — no marketing jargon, one clear ask per email.
  • One prominent, finger-friendly call-to-action button (tracked link).
  • Personalized greeting via {{greeting_name}} and an optional per-recipient
    first line via {{opener}} (city-aware; empty-safe — renders nothing when we
    have no city, so the email still reads perfectly).
  • Localized bodies for the Dutch and German markets (NL / DE / EN), matching
    where the crawler harvest actually lives.
  • Real signature and a compliant footer with physical address, reply-to, and
    one-click unsubscribe.

Merge fields (resolved per-recipient by app/email_engine.py):
    {{greeting_name}} {{opener}} {{first_name}} {{company_name}} {{contact_name}}
    {{city}} {{country}} {{website}} {{sender_name}} {{sender_title}}
    {{reply_to}} {{company_legal_name}} {{company_address}} {{company_phone}}
    {{company_website}} {{unsubscribe_url}}

`{{unsubscribe_url}}` is filled per recipient; the open pixel + click tracking
are injected automatically at send time — do NOT add them manually.

Bump STARTER_SEED_VERSION when editing any template; the seeder re-writes
starter rows whose seed_version is lower (user-created templates untouched).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EmailTemplate

STARTER_SEED_VERSION = 3

COMMON_FIELDS = (
    "greeting_name, opener, first_name, company_name, contact_name, city, "
    "country, website, sender_name, sender_title, reply_to, company_address, "
    "company_phone, company_website"
)

# Localized CTA button label per template language. The button links to the
# Schild site (tracked via click-rewrite at send time).
def _cta(label: str) -> str:
    return f"""\
<table role="presentation" cellspacing="0" cellpadding="0" style="margin:8px 0 6px 0;">
  <tr><td style="border-radius:8px;background:#101010;">
    <a href="{{{{company_website}}}}" style="display:inline-block;padding:13px 26px;font-size:16px;font-weight:600;color:#e4c977;text-decoration:none;border-radius:8px;">{label}</a>
  </td></tr>
</table>"""


def _wrap(preheader: str, inner_html: str) -> str:
    """Wrap body content in the Schild Inc branded shell — readable type, a
    clear signature, and a compliant footer. Lean HTML for deliverability."""
    return f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f5f4ef;font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#20272e;">
    <span style="display:none!important;opacity:0;color:#f5f4ef;height:0;overflow:hidden;">{preheader}</span>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f5f4ef;padding:24px 0;">
      <tr><td align="center">
        <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="width:600px;max-width:94%;background:#ffffff;border-radius:14px;overflow:hidden;border:1px solid #e7e1d6;">
          <tr>
            <td style="background:#101010;padding:20px 34px;">
              <span style="color:#e4c977;font-size:19px;font-weight:bold;letter-spacing:0.5px;">SCHILD INC</span>
              <span style="color:#9b9483;font-size:12px;float:right;padding-top:6px;">Premium metal labels &amp; branded accessories</span>
            </td>
          </tr>
          <tr>
            <td style="padding:30px 34px 8px 34px;font-size:16px;line-height:1.7;color:#20272e;">
              {inner_html}
            </td>
          </tr>
          <tr>
            <td style="padding:8px 34px 24px 34px;font-size:16px;line-height:1.6;color:#20272e;">
              Kind regards,<br>
              <strong>{{{{sender_name}}}}</strong><br>
              <span style="color:#5b6470;">{{{{sender_title}}}} · Schild Inc</span><br>
              <a href="{{{{company_website}}}}" style="color:#946f16;text-decoration:none;">{{{{company_website}}}}</a>
            </td>
          </tr>
          <tr>
            <td style="padding:0 34px 24px 34px;">
              <div style="border-top:1px solid #e7dfd0;padding-top:14px;color:#9aa0a6;font-size:12.5px;line-height:1.6;">
                {{{{company_legal_name}}}} · {{{{company_address}}}}<br>
                You received this email because we believe Schild Inc may be relevant to {{{{company_name}}}}.
                If it isn't, we're sorry for the interruption — you can
                <a href="{{{{unsubscribe_url}}}}" style="color:#9aa0a6;">unsubscribe in one click</a>
                and we won't contact you again. Replies reach a real person at
                <a href="mailto:{{{{reply_to}}}}" style="color:#9aa0a6;">{{{{reply_to}}}}</a>.
              </div>
            </td>
          </tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""


def _text(body: str) -> str:
    """Plain-text twin with the same compliant signature/footer."""
    return (
        body.rstrip()
        + "\n\nKind regards,\n{{sender_name}}\n{{sender_title}}, Schild Inc\n{{company_website}}\n\n"
        + "--\n{{company_legal_name}} · {{company_address}}\n"
        + "You received this because we believe Schild Inc may be relevant to {{company_name}}. "
        + "Not relevant? Unsubscribe: {{unsubscribe_url}} — replies go to {{reply_to}}."
    )


def _opener_p() -> str:
    """Optional personalized first line. {{opener}} is empty-safe: when we have
    no city the merge renders nothing and this paragraph collapses cleanly."""
    return '<p style="margin:0 0 16px 0;">{{opener}}</p>\n'


# ── ENGLISH ─────────────────────────────────────────────────────────────────

COLD_INTRO_EN_HTML = _wrap(
    "A premium finishing touch for the products leaving your workshop",
    _opener_p() +
    """<p style="margin:0 0 16px 0;">Hi {{greeting_name}},</p>
<p style="margin:0 0 16px 0;">I'll keep this short. I'm with Schild Inc — we make <strong>premium metal labels and branded accessories</strong>, so the products leaving {{company_name}} carry <em>your</em> name, not just the manufacturer's.</p>
<p style="margin:0 0 18px 0;">Many businesses use them as a quiet quality signal. I'd be glad to send you a <strong>free first design with your own logo</strong> — no cost, no obligation, just so you can see how it looks.</p>
""" + _cta("See a free sample design →") +
    """<p style="margin:14px 0 4px 0;">Would that be worth a look?</p>""",
)
COLD_INTRO_EN_TEXT = _text(
    "{{opener}}\n\nHi {{greeting_name}},\n\n"
    "I'll keep this short. I'm with Schild Inc — we make premium metal labels and branded "
    "accessories, so the products leaving {{company_name}} carry your name, not just the "
    "manufacturer's.\n\n"
    "Many businesses use them as a quiet quality signal. I'd be glad to send you a free first "
    "design with your own logo — no cost, no obligation, just so you can see how it looks.\n\n"
    "Would that be worth a look? You can reply straight to this email."
)

COLD_FOLLOWUP_EN_HTML = _wrap(
    "A quick follow-up — and an easy way to say no",
    """<p style="margin:0 0 16px 0;">Hi {{greeting_name}},</p>
<p style="margin:0 0 16px 0;">Just bringing my earlier note back to the top — inboxes get busy, and I don't want to be a nuisance.</p>
<p style="margin:0 0 4px 0;">The offer still stands: a <strong>free first design with your logo</strong>, no strings attached. If the timing isn't right, a one-line "not now" is completely fine and I'll leave it there.</p>""",
)
COLD_FOLLOWUP_EN_TEXT = _text(
    "Hi {{greeting_name}},\n\n"
    "Just bringing my earlier note back to the top — inboxes get busy, and I don't want to be "
    "a nuisance.\n\n"
    "The offer still stands: a free first design with your logo, no strings attached. If the "
    "timing isn't right, a one-line \"not now\" is completely fine and I'll leave it there."
)


# ── DUTCH (NL) ──────────────────────────────────────────────────────────────

COLD_INTRO_NL_HTML = _wrap(
    "Een premium afwerking voor de producten uit uw werkplaats",
    _opener_p() +
    """<p style="margin:0 0 16px 0;">Hallo {{greeting_name}},</p>
<p style="margin:0 0 16px 0;">Ik houd het kort. Ik ben van Schild Inc — wij maken <strong>premium metalen labels en merkaccessoires</strong>, zodat de producten die {{company_name}} verlaten <em>uw</em> naam dragen, en niet alleen die van de fabrikant.</p>
<p style="margin:0 0 18px 0;">Veel bedrijven gebruiken ze als een subtiel teken van kwaliteit. Ik stuur u graag een <strong>gratis eerste ontwerp met uw eigen logo</strong> — zonder kosten en zonder verplichting, gewoon zodat u kunt zien hoe het eruitziet.</p>
""" + _cta("Bekijk een gratis voorbeeld →") +
    """<p style="margin:14px 0 4px 0;">Is dat iets om naar te kijken?</p>""",
)
COLD_INTRO_NL_TEXT = _text(
    "{{opener}}\n\nHallo {{greeting_name}},\n\n"
    "Ik houd het kort. Ik ben van Schild Inc — wij maken premium metalen labels en "
    "merkaccessoires, zodat de producten die {{company_name}} verlaten uw naam dragen, en "
    "niet alleen die van de fabrikant.\n\n"
    "Veel bedrijven gebruiken ze als een subtiel teken van kwaliteit. Ik stuur u graag een "
    "gratis eerste ontwerp met uw eigen logo — zonder kosten en zonder verplichting.\n\n"
    "Is dat iets om naar te kijken? U kunt gewoon op deze e-mail antwoorden."
)

COLD_FOLLOWUP_NL_HTML = _wrap(
    "Een korte herinnering — en een makkelijke manier om nee te zeggen",
    """<p style="margin:0 0 16px 0;">Hallo {{greeting_name}},</p>
<p style="margin:0 0 16px 0;">Ik breng mijn eerdere bericht even opnieuw onder de aandacht — het is druk in de inbox en ik wil zeker niet tot last zijn.</p>
<p style="margin:0 0 4px 0;">Het aanbod staat nog: een <strong>gratis eerste ontwerp met uw logo</strong>, geheel vrijblijvend. Als het nu niet uitkomt, is een kort "nu even niet" helemaal prima en laat ik het daarbij.</p>""",
)
COLD_FOLLOWUP_NL_TEXT = _text(
    "Hallo {{greeting_name}},\n\n"
    "Ik breng mijn eerdere bericht even opnieuw onder de aandacht — het is druk in de inbox "
    "en ik wil zeker niet tot last zijn.\n\n"
    "Het aanbod staat nog: een gratis eerste ontwerp met uw logo, geheel vrijblijvend. Als "
    "het nu niet uitkomt, is een kort \"nu even niet\" helemaal prima."
)


# ── GERMAN (DE) ─────────────────────────────────────────────────────────────

COLD_INTRO_DE_HTML = _wrap(
    "Ein hochwertiger letzter Schliff für die Produkte aus Ihrer Werkstatt",
    _opener_p() +
    """<p style="margin:0 0 16px 0;">Hallo {{greeting_name}},</p>
<p style="margin:0 0 16px 0;">Ich fasse mich kurz. Ich bin von Schild Inc — wir fertigen <strong>hochwertige Metallschilder und Markenzubehör</strong>, damit die Produkte, die {{company_name}} verlassen, <em>Ihren</em> Namen tragen, nicht nur den des Herstellers.</p>
<p style="margin:0 0 18px 0;">Viele Betriebe nutzen sie als dezentes Qualitätsmerkmal. Gerne sende ich Ihnen einen <strong>kostenlosen ersten Entwurf mit Ihrem eigenen Logo</strong> — unverbindlich und kostenfrei, damit Sie sehen, wie es aussieht.</p>
""" + _cta("Kostenloses Muster ansehen →") +
    """<p style="margin:14px 0 4px 0;">Wäre das einen Blick wert?</p>""",
)
COLD_INTRO_DE_TEXT = _text(
    "{{opener}}\n\nHallo {{greeting_name}},\n\n"
    "Ich fasse mich kurz. Ich bin von Schild Inc — wir fertigen hochwertige Metallschilder "
    "und Markenzubehör, damit die Produkte, die {{company_name}} verlassen, Ihren Namen "
    "tragen, nicht nur den des Herstellers.\n\n"
    "Viele Betriebe nutzen sie als dezentes Qualitätsmerkmal. Gerne sende ich Ihnen einen "
    "kostenlosen ersten Entwurf mit Ihrem eigenen Logo — unverbindlich und kostenfrei.\n\n"
    "Wäre das einen Blick wert? Sie können einfach auf diese E-Mail antworten."
)

COLD_FOLLOWUP_DE_HTML = _wrap(
    "Eine kurze Erinnerung — und ein einfaches Nein",
    """<p style="margin:0 0 16px 0;">Hallo {{greeting_name}},</p>
<p style="margin:0 0 16px 0;">Ich hole meine frühere Nachricht kurz wieder nach oben — Postfächer sind voll, und ich möchte keinesfalls lästig sein.</p>
<p style="margin:0 0 4px 0;">Das Angebot steht weiterhin: ein <strong>kostenloser erster Entwurf mit Ihrem Logo</strong>, völlig unverbindlich. Wenn es gerade nicht passt, genügt ein kurzes "derzeit nicht" — dann lasse ich es dabei.</p>""",
)
COLD_FOLLOWUP_DE_TEXT = _text(
    "Hallo {{greeting_name}},\n\n"
    "Ich hole meine frühere Nachricht kurz wieder nach oben — Postfächer sind voll, und ich "
    "möchte keinesfalls lästig sein.\n\n"
    "Das Angebot steht weiterhin: ein kostenloser erster Entwurf mit Ihrem Logo, völlig "
    "unverbindlich. Wenn es gerade nicht passt, genügt ein kurzes \"derzeit nicht\"."
)


# ── VIP / multi-location partnership (EN) ───────────────────────────────────

VIP_HTML = _wrap(
    "A partnership idea for multi-location businesses",
    """<p style="margin:0 0 16px 0;">Hi {{greeting_name}},</p>
<p style="margin:0 0 16px 0;">Given {{company_name}}'s presence across multiple locations, I'd love to explore a <strong>partnership</strong> rather than a one-off order.</p>
<p style="margin:0 0 10px 0;">For multi-site businesses and buying groups we offer:</p>
<ul style="margin:0 0 18px 0;padding-left:22px;">
  <li style="margin-bottom:6px;">Volume pricing across all your locations</li>
  <li style="margin-bottom:6px;">A dedicated account contact</li>
  <li>Consistent branding on labels &amp; accessories everywhere</li>
</ul>
<p style="margin:0 0 4px 0;">Would a short 15-minute call be worth your time? I'm happy to work around your schedule.</p>""",
)
VIP_TEXT = _text(
    "Hi {{greeting_name}},\n\n"
    "Given {{company_name}}'s presence across multiple locations, I'd love to explore a "
    "partnership rather than a one-off order.\n\n"
    "For multi-site businesses and buying groups we offer:\n"
    "- Volume pricing across all your locations\n"
    "- A dedicated account contact\n"
    "- Consistent branding on labels & accessories everywhere\n\n"
    "Would a short 15-minute call be worth your time? I'm happy to work around your schedule."
)


STARTER_TEMPLATES = [
    {
        "name": "Cold — Initial Outreach (English)",
        "category": "cold",
        "description": "First touch, English. Large readable type, personalized opener + one clear CTA.",
        "subject": "A premium finishing touch for {{company_name}}",
        "body_html": COLD_INTRO_EN_HTML,
        "body_text": COLD_INTRO_EN_TEXT,
    },
    {
        "name": "Cold — Follow-up (English)",
        "category": "followup",
        "description": "Polite nudge ~5 days after the English intro, with an easy opt-out.",
        "subject": "Quick follow-up, {{greeting_name}}",
        "body_html": COLD_FOLLOWUP_EN_HTML,
        "body_text": COLD_FOLLOWUP_EN_TEXT,
    },
    {
        "name": "Cold — Initial Outreach (Nederlands)",
        "category": "cold",
        "description": "Eerste contact, Nederlands. Grote leesbare tekst, persoonlijke opener + duidelijke CTA.",
        "subject": "Een premium afwerking voor {{company_name}}",
        "body_html": COLD_INTRO_NL_HTML,
        "body_text": COLD_INTRO_NL_TEXT,
    },
    {
        "name": "Cold — Follow-up (Nederlands)",
        "category": "followup",
        "description": "Vriendelijke herinnering ~5 dagen na het Nederlandse intro-bericht.",
        "subject": "Korte herinnering, {{greeting_name}}",
        "body_html": COLD_FOLLOWUP_NL_HTML,
        "body_text": COLD_FOLLOWUP_NL_TEXT,
    },
    {
        "name": "Cold — Initial Outreach (Deutsch)",
        "category": "cold",
        "description": "Erstkontakt, Deutsch. Gut lesbare Schrift, persönlicher Einstieg + klarer CTA.",
        "subject": "Ein hochwertiger letzter Schliff für {{company_name}}",
        "body_html": COLD_INTRO_DE_HTML,
        "body_text": COLD_INTRO_DE_TEXT,
    },
    {
        "name": "Cold — Follow-up (Deutsch)",
        "category": "followup",
        "description": "Freundliche Erinnerung ~5 Tage nach dem deutschen Erstkontakt.",
        "subject": "Kurze Erinnerung, {{greeting_name}}",
        "body_html": COLD_FOLLOWUP_DE_HTML,
        "body_text": COLD_FOLLOWUP_DE_TEXT,
    },
    {
        "name": "VIP — Multi-location Partnership",
        "category": "vip",
        "description": "For franchises, buying groups, and multi-location businesses.",
        "subject": "Partnership idea for {{company_name}}",
        "body_html": VIP_HTML,
        "body_text": VIP_TEXT,
    },
]


def seed_starter_templates(session: Session) -> int:
    """Idempotently insert/update the built-in starter templates.

    Matches on (name, is_starter=True). Re-writes content only when the stored
    seed_version is older than STARTER_SEED_VERSION. Returns rows touched.
    """
    touched = 0
    for spec in STARTER_TEMPLATES:
        existing = session.scalar(
            select(EmailTemplate).where(
                EmailTemplate.name == spec["name"],
                EmailTemplate.is_starter.is_(True),
            )
        )
        if existing is None:
            session.add(
                EmailTemplate(
                    name=spec["name"], category=spec["category"], description=spec["description"],
                    subject=spec["subject"], body_html=spec["body_html"], body_text=spec["body_text"],
                    merge_fields=COMMON_FIELDS, is_active=True, is_starter=True,
                    seed_version=STARTER_SEED_VERSION,
                )
            )
            touched += 1
        elif existing.seed_version < STARTER_SEED_VERSION:
            existing.category = spec["category"]
            existing.description = spec["description"]
            existing.subject = spec["subject"]
            existing.body_html = spec["body_html"]
            existing.body_text = spec["body_text"]
            existing.merge_fields = COMMON_FIELDS
            existing.seed_version = STARTER_SEED_VERSION
            touched += 1

    # Retire starter templates that are no longer in the current set (e.g. the
    # pre-localization names). Hide, don't delete — a campaign may reference one.
    current_names = {spec["name"] for spec in STARTER_TEMPLATES}
    stale = session.scalars(
        select(EmailTemplate).where(
            EmailTemplate.is_starter.is_(True),
            EmailTemplate.is_active.is_(True),
            EmailTemplate.name.notin_(current_names),
        )
    ).all()
    for row in stale:
        row.is_active = False
        touched += 1

    if touched:
        session.commit()
    return touched
