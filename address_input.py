"""Helpers for user-entered AAP addresses."""

from __future__ import annotations

from aap.address import Address


def parse_user_address(value: str) -> str:
    """Parse human/LLM-entered AAP address text to canonical string form."""
    parser = getattr(Address, "parse_user_input", None)
    if parser is not None:
        return str(parser(value))

    # Compatibility with older aap-python wheels during local rollout.
    text = value.strip()
    if text.endswith("^"):
        text = f"{text}agentaddress.org"
    return str(Address.parse(text))
