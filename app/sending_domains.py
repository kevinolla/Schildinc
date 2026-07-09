"""Sending-domain registry — the sender identities a campaign may send from.

Schild sends cold outreach from more than one brand domain:
    - schildinc.com   (primary — Google Workspace, DKIM already verified)
    - schildlabels.com
    - schildinc.nl    (Dutch market)

Each identity is a (From address, display name, Reply-To) triple. The operator
picks one per campaign; the picked address becomes the Resend From header and
the Reply-To. Everything is overridable via env so addresses can change without
a code deploy, but the three brand domains ship as sensible defaults.

Safety: ``is_allowed_reply_to()`` gates which Reply-To values the send layer
will honor. Only addresses on a Schild-owned domain pass — so a mis-built
campaign can never leak replies to an outside address, while still allowing any
of the three brand domains (the old code hard-forced a single address).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


# Domains verified in Resend (DKIM/SPF added + green in the dashboard). Only
# these can actually send; the UI flags the others as "needs setup". Update
# SEND_VERIFIED_DOMAINS as you verify each brand domain in Resend.
_VERIFIED = {
    d.strip().lower()
    for d in os.getenv("SEND_VERIFIED_DOMAINS", "schildinc.com").split(",")
    if d.strip()
}


@dataclass(frozen=True)
class SendingIdentity:
    """One selectable 'send from' identity."""
    key: str          # stable slug used in forms / campaign.sender_alias routing
    label: str        # human label for the dropdown
    from_email: str   # From: address (must be a verified Resend domain)
    from_name: str    # From: display name
    reply_to: str     # Reply-To: address (replies land here)
    domain: str       # registrable domain, for the allowlist

    @property
    def verified(self) -> bool:
        """True when the domain is verified in Resend (safe to send from)."""
        return self.domain.lower() in _VERIFIED


def _identity(key: str, default_domain: str, default_from: str,
              default_name: str, default_reply: str) -> SendingIdentity:
    up = key.upper()
    domain = os.getenv(f"SEND_{up}_DOMAIN", default_domain)
    return SendingIdentity(
        key=key,
        label=os.getenv(f"SEND_{up}_LABEL", f"{default_name} <{default_from}>"),
        from_email=os.getenv(f"SEND_{up}_FROM", default_from),
        from_name=os.getenv(f"SEND_{up}_NAME", default_name),
        reply_to=os.getenv(f"SEND_{up}_REPLY_TO", default_reply),
        domain=domain,
    )


# Ordered — the first entry is the default selection in the UI.
IDENTITIES: list[SendingIdentity] = [
    _identity("schildinc_com", "schildinc.com",
              "sales@schildinc.com", "Schild Inc", "sales@schildinc.com"),
    _identity("schildlabels_com", "schildlabels.com",
              "sales@schildlabels.com", "Schild Labels", "sales@schildlabels.com"),
    _identity("schildinc_nl", "schildinc.nl",
              "verkoop@schildinc.nl", "Schild Inc", "verkoop@schildinc.nl"),
]

_BY_KEY = {i.key: i for i in IDENTITIES}
# Every Schild-owned domain that a Reply-To may legitimately use.
ALLOWED_DOMAINS = {i.domain.lower() for i in IDENTITIES}


def all_identities() -> list[SendingIdentity]:
    return list(IDENTITIES)


def default_identity() -> SendingIdentity:
    return IDENTITIES[0]


def get(key: str) -> SendingIdentity | None:
    return _BY_KEY.get((key or "").strip())


def identity_for_alias(from_email: str) -> SendingIdentity | None:
    """Reverse lookup by From address (used to label an existing campaign)."""
    addr = (from_email or "").strip().lower()
    for ident in IDENTITIES:
        if ident.from_email.lower() == addr:
            return ident
    return None


def domain_of(email: str) -> str:
    return (email or "").rsplit("@", 1)[-1].strip().lower() if "@" in (email or "") else ""


def is_allowed_reply_to(email: str) -> bool:
    """True iff ``email`` is on a Schild-owned sending domain."""
    return domain_of(email) in ALLOWED_DOMAINS
