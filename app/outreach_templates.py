from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.models import Prospect
from app.utils import add_business_days


DEFAULT_SUBJECTS = [
    "Idee voor jullie fietsenwinkel branding",
    "Gratis labelontwerp voor jullie fietsenwinkel",
    "Jullie logo op premium labels en accessoires",
]


@dataclass
class OutreachBundle:
    subject: str
    text_body: str
    html_body: str
    linkedin_text: str
    instagram_text: str
    contact_form_text: str
    follow_up_subject: str
    follow_up_text: str


def build_outreach_bundle(prospect: Prospect, sender_name: str | None = None) -> OutreachBundle:
    sender = sender_name or settings.sender_name
    website = prospect.website or "website"
    custom_use_case = prospect.custom_use_case or _default_use_case(prospect)
    proof_line = prospect.proof_line or "Onze oplossingen worden al gebruikt door meer dan 500 fietsenwinkels, waaronder BikeTotaal, Azor, VMG en Gazelle."
    subject = DEFAULT_SUBJECTS[0]

    text_body = "\n".join(
        [
            f"Beste {prospect.company_name} team,",
            "",
            f"Ik kwam jullie {website} tegen en dacht dat Schild Inc interessant kan zijn voor jullie fietsenwinkel.",
            "",
            "Wij helpen fietsenwinkels met:",
            "- premium metalen labels met eigen logo",
            "- bike accessoires met eigen logo",
            f"- {custom_use_case}",
            "",
            proof_line,
            "",
            "Wat we vrijblijvend kunnen doen:",
            "- een gratis eerste labelontwerp met jullie huidige logo",
            "- voorbeelden van bike accessoires met eigen logo",
            "- een paar relevante projectvoorbeelden",
            "",
            "Heeft jullie huidige logo een wat verouderde uitstraling?",
            "Dan kunnen we ook helpen met een eenvoudige logo redesign vanaf €89,95.",
            "",
            "Sta je open voor een paar voorbeelden of een eerste gratis ontwerpidee?",
            "",
            "Met vriendelijke groet,",
            "",
            sender,
            "Schild Inc",
        ]
    )

    html_body = build_email_html(
        company_name=prospect.company_name,
        website=website,
        sender_name=sender,
        custom_use_case=custom_use_case,
        proof_line=proof_line,
    )

    linkedin = (
        f"Hi {prospect.company_name}, ik kwam jullie winkel tegen. "
        "Schild Inc helpt fietsenwinkels met premium metalen labels en bike accessoires met eigen logo. "
        "Als je wilt, stuur ik graag een paar voorbeelden of een gratis eerste labelidee. "
        f"Reacties mogen altijd naar {settings.reply_to_email} of via onze officiële LinkedIn: {settings.official_linkedin_url}"
    )

    instagram = (
        f"Hi {prospect.company_name}! Wij maken premium metalen labels en bike accessoires met eigen logo voor fietsenwinkels. "
        "Als jullie willen, sturen we graag een paar voorbeelden of een gratis eerste labelidee. "
        f"Reageren kan altijd via {settings.reply_to_email} of via {settings.official_instagram_handle}."
    )

    contact_form = (
        f"Beste {prospect.company_name} team,\n\n"
        "Wij helpen fietsenwinkels met premium labels en bike accessoires met eigen logo. "
        "Als jullie willen, sturen we graag een paar voorbeelden of een eerste gratis labelidee.\n\n"
        f"Groet,\n{sender}\nSchild Inc\n{settings.reply_to_email}"
    )

    follow_up_subject = DEFAULT_SUBJECTS[1]
    follow_up_date = add_business_days(prospect.created_at.date(), 5) if prospect.created_at else None
    follow_up_text = "\n".join(
        [
            f"Beste {prospect.company_name} team,",
            "",
            "Nog even een korte follow-up op mijn vorige bericht.",
            "Als jullie willen, stuur ik graag een paar voorbeelden van premium labels of bike accessoires met eigen logo.",
            "",
            "We kunnen ook vrijblijvend een eerste labelontwerp maken met jullie huidige logo.",
            "",
            "Als dit niet relevant is, laat het gerust weten.",
            "",
            "Met vriendelijke groet,",
            "",
            sender,
            "Schild Inc",
            f"Reply-to: {settings.reply_to_email}",
            f"Voorstel follow-up moment: {follow_up_date.isoformat()}" if follow_up_date else "",
        ]
    ).strip()

    return OutreachBundle(
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        linkedin_text=linkedin,
        instagram_text=instagram,
        contact_form_text=contact_form,
        follow_up_subject=follow_up_subject,
        follow_up_text=follow_up_text,
    )


def build_email_html(company_name: str, website: str, sender_name: str, custom_use_case: str, proof_line: str) -> str:
    logo_url = f"{settings.app_base_url}/static/email/schild-bike-logo.png"
    labels_url = f"{settings.app_base_url}/static/email/metal-labels.png"
    accessories_url = f"{settings.app_base_url}/static/email/bike-accessories.png"
    return f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f5f4ef;font-family:Arial,sans-serif;color:#1f2933;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f5f4ef;padding:24px 0;">
      <tr>
        <td align="center">
          <table role="presentation" width="680" cellspacing="0" cellpadding="0" style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e7e1d6;">
            <tr>
              <td style="background:#101010;padding:28px 32px;text-align:center;">
                <img src="{logo_url}" alt="Schild Inc Bike" width="120" style="display:block;margin:0 auto 16px auto;width:120px;height:auto;">
                <div style="color:#e4c977;font-size:22px;font-weight:bold;">Premium branding voor fietsenwinkels</div>
              </td>
            </tr>
            <tr>
              <td style="padding:28px 32px 10px 32px;font-size:16px;line-height:1.6;">
                <p style="margin:0 0 14px 0;">Beste {company_name} team,</p>
                <p style="margin:0 0 14px 0;">Ik kwam jullie <a href="{website}" style="color:#946f16;">website</a> tegen en dacht dat Schild Inc interessant kan zijn voor jullie fietsenwinkel.</p>
                <p style="margin:0 0 18px 0;">Wij helpen fietsenwinkels met premium metalen labels, bike accessoires met eigen logo en <strong>{custom_use_case}</strong>.</p>
              </td>
            </tr>
            <tr>
              <td style="padding:0 32px 18px 32px;">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                  <tr>
                    <td width="50%" style="padding-right:8px;vertical-align:top;">
                      <img src="{labels_url}" alt="Schild premium metal labels" width="100%" style="display:block;width:100%;height:auto;border-radius:10px;border:1px solid #e5e5e5;">
                    </td>
                    <td width="50%" style="padding-left:8px;vertical-align:top;">
                      <img src="{accessories_url}" alt="Schild bike accessories with logo" width="100%" style="display:block;width:100%;height:auto;border-radius:10px;border:1px solid #e5e5e5;">
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:0 32px 8px 32px;">
                <div style="height:1px;background:#e7dfd0;"></div>
              </td>
            </tr>
            <tr>
              <td style="padding:18px 32px 8px 32px;font-size:15px;line-height:1.7;">
                <p style="margin:0 0 14px 0;">{proof_line}</p>
                <p style="margin:0 0 12px 0;font-weight:bold;">Wat we vrijblijvend kunnen doen:</p>
                <ul style="padding-left:18px;margin:0 0 18px 0;">
                  <li>een gratis eerste labelontwerp met jullie huidige logo</li>
                  <li>voorbeelden van bike accessoires met eigen logo</li>
                  <li>een paar relevante projectvoorbeelden</li>
                </ul>
                <p style="margin:0 0 14px 0;">Heeft jullie huidige logo een wat verouderde uitstraling? Dan kunnen we ook helpen met een eenvoudige logo redesign vanaf €89,95.</p>
              </td>
            </tr>
            <tr>
              <td style="padding:8px 32px 28px 32px;">
                <div style="background:#f8f4e6;border:1px solid #e6d7a8;border-radius:10px;padding:16px 18px;">
                  <p style="margin:0 0 10px 0;font-size:15px;line-height:1.6;">Sta je open voor een paar voorbeelden of een eerste gratis ontwerpidee?</p>
                  <p style="margin:0;font-size:15px;line-height:1.6;">Met vriendelijke groet,<br><strong>{sender_name}</strong><br>Schild Inc</p>
                </div>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def _default_use_case(prospect: Prospect) -> str:
    tier = (prospect.bike_shop_tier or "").lower()
    segment = (prospect.bike_shop_segment or "").lower()
    if "workshop" in segment or "repair" in segment:
        return "een sterkere branding op fietsen die de werkplaats verlaten"
    if "premium" in segment or "good tier" in tier:
        return "een professionelere uitstraling in de winkel en op de fiets"
    return "een professionelere uitstraling in de winkel en extra add-on verkoop"
