"""AAP_* environment-driven settings."""

from __future__ import annotations

from aap.keys import decode_b64url
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_TRUST_LIST_PUBLIC_KEY_B64 = "HorTQKACHLqp2kt3jscOmdpDuRBpDd15Bqahw05gWwc"


class Settings(BaseSettings):
    """All knobs for the aap-hermes plugin.

    Read from environment variables (no .env file — Hermes manages env via ~/.hermes/.env
    which the host process exports before loading plugins).
    """

    model_config = SettingsConfigDict(extra="ignore", env_file=None)

    AAP_LOCALPART: str
    AAP_INSTANCE_DOMAIN: str = "agentaddress.org"
    AAP_RELAY_URL: str = "https://api.agentaddress.org"
    AAP_VERIFIER_URL: str = "https://verify.agentaddress.org"
    AAP_TRUST_LIST_PUBLIC_KEY_B64: str = DEFAULT_TRUST_LIST_PUBLIC_KEY_B64
    AAP_PRIVATE_SEED_B64: str | None = None
    AAP_HTTP_TIMEOUT_SECONDS: int = 35


def build_address(s: Settings) -> str:
    """Construct the user's full AAP address from settings."""
    return f"{s.AAP_LOCALPART}^{s.AAP_INSTANCE_DOMAIN}"


def decode_trust_list_public_key(value: str) -> bytes:
    """Decode and validate the pinned standards-body trust-list public key."""
    try:
        public_key = decode_b64url(value.strip())
    except Exception as e:
        raise ValueError("AAP_TRUST_LIST_PUBLIC_KEY_B64 is not valid base64url") from e
    if len(public_key) != 32:
        raise ValueError("AAP_TRUST_LIST_PUBLIC_KEY_B64 must decode to 32 bytes")
    return public_key
