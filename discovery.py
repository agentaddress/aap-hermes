"""Discovery: inbound consent-prompt rendering (hermes-local UI text).

Only ``render_introduction_prompt`` stays here — it produces the
home-channel consent card for an inbound discovery-introduction-request.

Everything else (``query_discovery``, ``extract_searcher_identities``,
``build_introduction_response_envelope``, ``PendingIntroductions``, etc.)
has moved to the ``aap`` SDK:

  from aap.discovery import query_discovery, extract_searcher_identities, build_introduction_response_envelope
  from aap.stores.pending_introductions import PendingIntroductions
"""

from __future__ import annotations

from typing import Optional

from .contacts import ContactSource


def render_introduction_prompt(
    *,
    searcher_address: str,
    searcher_label_for_recipient: Optional[str],
    searcher_identities: list[dict[str, str]],
    verifier_domain: str,
    nonce: str,
    contact_source: Optional[ContactSource] = None,
) -> str:
    """Build the home-channel consent card for an inbound introduction.

    Three flavors driven by ``searcher_identities`` + a local contact match:

    1. **Mutual contact** — any verified identity matches a local contact.
       Surfaces the contact's display name and labels it ``(mutual contact)``.
    2. **Attested but unrecognized** — searcher has verified identities,
       none match local contacts. Identifier values shown so the user
       can recognize manually.
    3. **Unverified** — no verified identities attached. High-friction;
       includes a block option in the suggested actions.
    """
    source = contact_source or ContactSource.load()

    mutual_contact_name: Optional[str] = None
    matched_identifier: Optional[dict[str, str]] = None
    for ident in searcher_identities:
        match = None
        if ident["type"] == "phone":
            match = source.find_by_phone(ident["value"])
        elif ident["type"] == "email":
            match = source.find_by_email(ident["value"])
        if match is not None:
            mutual_contact_name = match.display_name
            matched_identifier = ident
            break

    header_lines: list[str]
    if mutual_contact_name and matched_identifier:
        header_lines = [
            f"🔍 {mutual_contact_name} (mutual contact) is trying to find your agent.",
            f"   peer address: {searcher_address}",
            f"   matched identity: {matched_identifier['type']}="
            f"{matched_identifier['value']} (you have them in your contacts)",
        ]
        if searcher_label_for_recipient:
            header_lines.append(
                f"   they have you saved as: {searcher_label_for_recipient!r}"
            )
        header_lines.append(f"   verified by: {verifier_domain}")
    elif searcher_identities:
        ident_summary = ", ".join(
            f"{i['type']}={i['value']}" for i in searcher_identities
        )
        header_lines = [
            "🔍 An unrecognized agent is trying to find your agent.",
            "   no contact match for their verified identifiers.",
            f"   peer address: {searcher_address}",
            f"   verified identifiers: {ident_summary}",
            f"   verified by: {verifier_domain}",
        ]
        if searcher_label_for_recipient:
            header_lines.append(
                f"   they have you saved as: {searcher_label_for_recipient!r}"
            )
    else:
        # Unverified searcher — high friction.
        header_lines = [
            "⚠️ An unverified agent is trying to find your agent.",
            "   they attached NO verification attestations.",
            f"   peer address: {searcher_address}",
            f"   relayed via: {verifier_domain}",
        ]
        if searcher_label_for_recipient:
            header_lines.append(
                f"   they have you saved as: {searcher_label_for_recipient!r}"
            )
        header_lines.append(
            "   recommendation: decline by default; consider blocking."
        )

    # Bare-word shortcuts (handled by the predispatch hook in _runtime.py)
    # are the primary UX — just type 'approve' / 'deny' / 'block'. The full
    # slash-command form is shown as a fallback when there are multiple
    # pending intros and the user wants to target a specific nonce instead
    # of the most-recent default.
    action_lines = [
        "",
        "Reply `approve` or `deny`.",
    ]
    if not searcher_identities or not mutual_contact_name:
        action_lines.append("Reply `block` to silence this searcher forever.")
    action_lines.append(
        f"(explicit: /aap discover approve {nonce}  /  "
        f"/aap discover deny {nonce}"
        + (
            f"  /  /aap discover block {searcher_address})"
            if not searcher_identities or not mutual_contact_name
            else ")"
        )
    )
    return "\n".join(header_lines + action_lines)
