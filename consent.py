"""Consent helpers: host-UI prompt text for identity-binding consent.

``identity_binding_prompt_text`` — prompt shown when a peer presents
verified identities that match a local contact. Stays in hermes (UI text
out of scope for the aap SDK).

``PendingConsent`` has moved to ``aap.stores.consent``.
"""

from __future__ import annotations


def _domain_of(address: str) -> str:
    if "^" not in address:
        return address
    return address.split("^", 1)[1]


def identity_binding_prompt_text(
    *,
    peer_address: str,
    contact_display_name: str,
    matched_identifier: dict[str, str],
) -> str:
    domain = _domain_of(peer_address)
    return (
        f"🪪 Identity claim from {domain}\n"
        f"   peer: {peer_address}\n"
        f"   verified {matched_identifier['type']}: {matched_identifier['value']}\n\n"
        f"This matches your contact \"{contact_display_name}\".\n"
        f"Recognize this peer as \"{contact_display_name}\"?\n\n"
        f"To confirm:  /aap bind {peer_address}\n"
        f"To reject:   /aap unbind {peer_address}"
    )
