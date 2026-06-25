"""aap-hermes — Hermes plugin entrypoint.

All submodule imports are deferred inside ``register()`` and the inner
helpers. That keeps the top-level module body free of relative imports, so
``__init__.py`` is harmlessly importable even outside a package context
(e.g. ad-hoc tooling that scans the repo). Hermes always loads us via
``importlib.spec_from_file_location`` with ``submodule_search_locations``
set, so relative imports inside the functions resolve correctly at call time.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

__version__ = "0.16.2"

logger = logging.getLogger(__name__)

__all__ = ["__version__", "register"]

_HOSTED_TRUST_LIST_PUBLIC_KEY_B64 = "HorTQKACHLqp2kt3jscOmdpDuRBpDd15Bqahw05gWwc"


def check_requirements() -> bool:
    """Return True when the minimal required AAP env is set.

    Used by `hermes plugins requirements` and the gateway's pre-load check.
    Domain, relay URL, verifier URL, trust-list root, and seed have defaults or
    are generated. Self-hosted relays can still override the trust root.
    """
    return bool(os.getenv("AAP_LOCALPART", "").strip())


def validate_config(config: Any) -> bool:
    """Validate that PlatformConfig has enough info to construct the adapter."""
    extra = getattr(config, "extra", {}) or {}
    localpart = os.getenv("AAP_LOCALPART") or extra.get("localpart", "")
    return bool(localpart)


def is_connected(config: Any) -> bool:
    """Mirror of validate_config — aap is 'configured' iff localpart is set."""
    return validate_config(config)


def _env_enablement() -> dict | None:
    """Seed PlatformConfig.extra from AAP_* env vars.

    Returning None means 'not configured — skip auto-enabling'. Returning a
    dict tells the platform registry to auto-enable aap and merge the dict
    into PlatformConfig.extra.
    """
    localpart = os.getenv("AAP_LOCALPART", "").strip()
    if not localpart:
        return None
    seed: dict = {"localpart": localpart}
    domain = os.getenv("AAP_INSTANCE_DOMAIN", "").strip()
    if domain:
        seed["domain"] = domain
    relay_url = os.getenv("AAP_RELAY_URL", "").strip()
    if relay_url:
        seed["relay_url"] = relay_url
    trust_root = (
        os.getenv("AAP_TRUST_LIST_PUBLIC_KEY_B64", "").strip()
        or _HOSTED_TRUST_LIST_PUBLIC_KEY_B64
    )
    if trust_root:
        seed["trust_list_public_key_b64"] = trust_root
    return seed


_HUMAN_GATE_HINT = (
    "You are chatting via AAP, the Agent Address Protocol. Other parties "
    "on this channel are autonomous AI agents identified by addresses "
    "like <localpart>^<domain>. Hosted Agent Address shorthand is valid: "
    "if the user writes <localpart>^, treat it as "
    "<localpart>^agentaddress.org and pass it to AAP tools as given; do "
    "not ask for a domain just because the address ends in ^. Messages "
    "are signed and verified end-to-end. "
    "AAP HAS NO LLM-LEVEL HANDSHAKE. Peer trust is established by "
    "relationship records: your user has either established a friend / "
    "admin / team relationship with this peer, or this peer is a "
    "business agent your user has chosen to interact with. NEVER invent "
    "pairing codes, verification rituals, \"hermes pairing approve\" "
    "commands, or other security flows — they do not exist and will "
    "confuse your counterparty. "
    "CRITICAL: do not reply to AAP messages without explicit user "
    "confirmation. Your owner has been notified about this message on "
    "their primary chat platform (Telegram, Discord, Slack, etc.). Wait "
    "for their guidance before invoking the aap_send_message tool. If "
    "the user responds in another chat surface telling you what to say, "
    "use aap_send_message to forward their reply to the original AAP "
    "sender. If they don't respond, do nothing — the peer agent will "
    "retry or move on. "
    "Be precise and structured when you do reply — your counterparty is "
    "another agent, not a human."
)


_AUTONOMOUS_HINT = (
    "You are chatting via AAP, the Agent Address Protocol. Other parties "
    "on this channel are autonomous AI agents identified by addresses "
    "like <localpart>^<domain>. Hosted Agent Address shorthand is valid: "
    "if the user writes <localpart>^, treat it as "
    "<localpart>^agentaddress.org and pass it to AAP tools as given; do "
    "not ask for a domain just because the address ends in ^. Messages "
    "are signed and verified end-to-end. "
    "AAP HAS NO LLM-LEVEL HANDSHAKE. Peer trust is established by "
    "relationship records: your user has either established a friend / "
    "admin / team relationship with this peer, or this peer is a "
    "business agent your user has chosen to interact with. NEVER invent "
    "pairing codes, verification rituals, \"hermes pairing approve\" "
    "commands, or other security flows — they do not exist and will "
    "confuse your counterparty. "
    "AUTONOMOUS MODE: you may reply directly to AAP messages using the "
    "aap_send_message tool without waiting for user confirmation. The "
    "user observes both inbound messages and your outbound replies via "
    "their home channel for visibility, but no approval is required. "
    "AVOID LOOPS: only reply if your response adds genuine value — new "
    "information, a decision, or a substantive answer. Do not reply with "
    "mere acknowledgments (\"thanks\", \"got it\", \"sounds good\"). If "
    "the inbound message appears to be a closing statement, do not "
    "respond. If you've exchanged more than five messages on the same "
    "topic without resolution, stop and notify your user instead of "
    "continuing. "
    "Be precise and structured — your counterparty is another agent, not "
    "a human."
)


def _build_platform_hint() -> str:
    """Pick the platform_hint based on the AAP_AUTONOMOUS env var.

    Default ("off" or unset) is the human-gate hint. "on" switches to the
    autonomous hint with anti-loop guidance.
    """
    if os.getenv("AAP_AUTONOMOUS", "off").strip().lower() == "on":
        return _AUTONOMOUS_HINT
    return _HUMAN_GATE_HINT


def _check_localpart_availability(relay_url: str, localpart: str) -> dict:
    """Hit GET /aap/addresses/check on the relay.

    Returns a dict with shape:
        {"status": "available" | "taken" | "malformed" | "error",
         "base_localpart": str | None,
         "base_claimed": bool | None}

    Statuses:
      - "available"  — name is free
      - "taken"      — name is taken or reserved (200 with available: false)
      - "malformed"  — relay rejected the shape (400)
      - "error"      — network failure, unexpected status, or rate-limited;
                       caller should proceed without blocking on the check
    """
    import httpx

    try:
        r = httpx.get(
            f"{relay_url.rstrip('/')}/aap/addresses/check",
            params={"localpart": localpart},
            timeout=5.0,
        )
    except httpx.HTTPError:
        return {"status": "error", "base_localpart": None, "base_claimed": None}
    if r.status_code == 400:
        return {"status": "malformed", "base_localpart": None, "base_claimed": None}
    if r.status_code != 200:
        return {"status": "error", "base_localpart": None, "base_claimed": None}
    try:
        body = r.json()
    except ValueError:
        return {"status": "error", "base_localpart": None, "base_claimed": None}
    status = "available" if body.get("available") else "taken"
    return {
        "status": status,
        "base_localpart": body.get("base_localpart"),
        "base_claimed": body.get("base_claimed"),
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _domain_from_url(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return parsed.netloc or parsed.path.split("/", 1)[0]


async def _lookup_verifier_public_key(verifier_domain: str) -> bytes | None:
    import os
    from pathlib import Path

    from aap.verifiers import TrustListCache, VerifierPubkeyCache
    from .config import DEFAULT_TRUST_LIST_PUBLIC_KEY_B64, decode_trust_list_public_key

    trust_root_b64 = (
        os.getenv("AAP_TRUST_LIST_PUBLIC_KEY_B64", "").strip()
        or DEFAULT_TRUST_LIST_PUBLIC_KEY_B64
    )
    home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))
    trust_cache = TrustListCache(
        cache_path=home / "aap-trusted-verifiers.json",
        overrides_path=home / "aap-trusted-verifiers-overrides.json",
        trust_list_public_key=decode_trust_list_public_key(trust_root_b64),
    )
    pubkey_cache = VerifierPubkeyCache(cache_dir=home / "aap-verifier-pubkeys")
    try:
        trust_list = await trust_cache.get()
        return await pubkey_cache.get(verifier_domain, trust_list)
    finally:
        await trust_cache.aclose()
        await pubkey_cache.aclose()


def _drive_email_verification(
    relay_url: str,
    verifier_url: str,
    subject_address: str,
    seed: bytes,
    public_key_b64: str,
    email: str,
    prompt_fn,
    print_info_fn,
    print_warning_fn,
) -> dict | None:
    """Drive the verify-email-start + verify-email-confirm flow.

    Returns the attestation envelope as a dict on success, None on failure.
    Uses the wizard's own prompt/print helpers for UX consistency.
    """
    import asyncio
    import httpx
    import secrets
    from aap.envelope import Envelope
    from aap.envelope_policy import verify_envelope
    from aap.payloads import VerifyStartResponse
    from aap.verifier_client import VerifierClientError, confirm_email_verification
    from aap.verifiers import verifier_relay_address

    verifier_domain = _domain_from_url(verifier_url)
    try:
        verifier_public_key = asyncio.run(_lookup_verifier_public_key(verifier_domain))
    except Exception as e:
        print_warning_fn(f"Could not authenticate verifier {verifier_domain}: {e}")
        return None
    if verifier_public_key is None:
        print_warning_fn(
            f"Verifier {verifier_domain} is not in the signed trust list "
            "or has no valid public key."
        )
        return None

    request_nonce = secrets.token_urlsafe(12)
    start_env = Envelope(
        type="aap.envelope/v1",
        payload_type="aap.verify-email-start/v1",
        payload={
            "email": email,
            "subject_address": subject_address,
            "public_key": public_key_b64,
            "nonce": request_nonce,
        },
        iss=subject_address,
        iat=_now_iso(),
    ).sign(seed)

    try:
        start_resp = httpx.post(
            f"{verifier_url.rstrip('/')}/aap/verify/email/start",
            content=start_env.to_json(),
            headers={"Content-Type": "application/json"},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        print_warning_fn(f"Could not reach verifier at {verifier_url}: {e}")
        return None
    if start_resp.status_code != 200:
        print_warning_fn(
            f"Verifier returned {start_resp.status_code}: {start_resp.text[:200]}"
        )
        return None
    try:
        response_env = Envelope.from_json(start_resp.text)
        if response_env.payload_type != VerifyStartResponse.PAYLOAD_TYPE:
            print_warning_fn("Verifier start response used an unexpected payload type")
            return None
        if response_env.iss != verifier_relay_address(verifier_domain):
            print_warning_fn("Verifier start response came from an unexpected issuer")
            return None
        verify_envelope(response_env, verifier_public_key)
        start_response = VerifyStartResponse.from_dict(response_env.payload)
    except Exception as e:
        print_warning_fn(f"Verifier returned an invalid signed start response: {e}")
        return None
    if start_response.request_nonce != request_nonce:
        print_warning_fn("Verifier start response nonce mismatch")
        return None
    if not start_response.otp_id:
        print_warning_fn("Verifier response missing otp_id")
        return None

    print_info_fn(f"Verification email sent to {email}. Check inbox.")
    token = prompt_fn("Enter the code from the email").strip()
    if not token:
        print_warning_fn("No code entered — aborting verification")
        return None

    try:
        attestation_json = asyncio.run(
            confirm_email_verification(
                seed=seed,
                subject_address=subject_address,
                otp_id=start_response.otp_id,
                token=token,
                verification_endpoint=f"{verifier_url.rstrip('/')}/aap/verify",
                verifier_domain=verifier_domain,
                verifier_public_key=verifier_public_key,
            )
        )
    except VerifierClientError as e:
        print_warning_fn(f"Confirmation failed: {e}")
        return None

    import json as _json
    try:
        return _json.loads(attestation_json)
    except ValueError:
        print_warning_fn("Could not parse attestation envelope")
        return None


def _submit_address_claim(
    relay_url: str,
    seed: bytes,
    public_key_b64: str,
    encryption_public_key_b64: str,
    localpart: str,
    domain: str,
    attestation_envelope_dict: dict,
) -> tuple[bool, str]:
    """POST /aap/addresses/claim and return (success, message)."""
    return _submit_address_op(
        relay_url=relay_url,
        seed=seed,
        public_key_b64=public_key_b64,
        encryption_public_key_b64=encryption_public_key_b64,
        localpart=localpart,
        domain=domain,
        attestation_envelope_dict=attestation_envelope_dict,
        op="claim",
    )


def _submit_address_rotate(
    relay_url: str,
    seed: bytes,
    public_key_b64: str,
    encryption_public_key_b64: str,
    localpart: str,
    domain: str,
    attestation_envelope_dict: dict,
) -> tuple[bool, str]:
    """POST /aap/addresses/rotate-key and return (success, message).

    The rotate-key endpoint binds an already-claimed address to a new
    keypair, gated by re-verifying ownership of the original email.
    Used by the wizard's recovery branch when the user owns a localpart
    that's been claimed (e.g. a prior claim attempt whose response was
    lost to a network timeout — server processed the claim but the
    client never persisted the keypair).
    """
    return _submit_address_op(
        relay_url=relay_url,
        seed=seed,
        public_key_b64=public_key_b64,
        encryption_public_key_b64=encryption_public_key_b64,
        localpart=localpart,
        domain=domain,
        attestation_envelope_dict=attestation_envelope_dict,
        op="rotate-key",
    )


def _submit_address_op(
    *,
    relay_url: str,
    seed: bytes,
    public_key_b64: str,
    encryption_public_key_b64: str,
    localpart: str,
    domain: str,
    attestation_envelope_dict: dict,
    op: str,
) -> tuple[bool, str]:
    """Submit a claim or rotate-key request and return (success, message).

    The two operations share envelope shape, signing, transport, timeout
    handling, and success/failure decoding. They differ only in:
      - URL path (/aap/addresses/claim vs /aap/addresses/rotate-key)
      - payload_type
      - the public-key field name (public_key vs new_public_key)
      - the success status code (201 vs 200)
    """
    import httpx
    from aap.envelope import Envelope
    from aap.payloads import AgentCard

    if op == "claim":
        path = "/aap/addresses/claim"
        payload_type = "aap.address-claim/v1"
        public_key_field = "public_key"
        success_status = 201
    elif op == "rotate-key":
        path = "/aap/addresses/rotate-key"
        payload_type = "aap.address-rotate/v1"
        public_key_field = "new_public_key"
        success_status = 200
    else:
        raise ValueError(f"unknown op: {op!r}")

    subject = f"{localpart}^{domain}"
    agent_card = AgentCard(
        address=subject,
        did=f"did:web:{domain}#agent",
        public_key=public_key_b64,
        encryption_key=encryption_public_key_b64,
        endpoints=[{"type": "didcomm", "uri": relay_url.rstrip("/")}],
        kind="personal",
    )
    agent_card_env = Envelope(
        type="aap.envelope/v1",
        payload_type=AgentCard.PAYLOAD_TYPE,
        payload=agent_card.to_dict(),
        iss=subject,
        iat=_now_iso(),
    ).sign(seed)
    env = Envelope(
        type="aap.envelope/v1",
        payload_type=payload_type,
        payload={
            "localpart": localpart,
            public_key_field: public_key_b64,
            "email_attestation": attestation_envelope_dict,
            "agent_card_envelope": agent_card_env.to_dict(),
        },
        iss=subject,
        iat=_now_iso(),
    ).sign(seed)

    # Separate connect/read budgets: the server-side claim/rotate path
    # does signature verification + a DB write, which under load has
    # been observed to take >15s. ReadTimeout on this call is the worst
    # outcome — the request was sent and may have been processed — so
    # give the response a generous read budget but keep connect tight.
    op_timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)

    try:
        r = httpx.post(
            f"{relay_url.rstrip('/')}{path}",
            content=env.to_json(),
            headers={"Content-Type": "application/json"},
            timeout=op_timeout,
        )
    except httpx.ReadTimeout:
        return False, (
            "no response from relay within 60s. The server may have "
            "processed this — re-run setup and, if the address shows as "
            "taken, choose the recovery (rotate-key) path to bind it to a "
            "fresh keypair."
        )
    except httpx.HTTPError as e:
        return False, f"network error contacting relay: {e}"
    if r.status_code == success_status:
        return True, r.json().get("address", subject)
    return False, f"HTTP {r.status_code}: {r.text[:300]}"


def _persist_identity(
    private_seed: bytes,
    public_key: bytes,
    encryption_private_key: bytes,
    encryption_public_key: bytes,
    address: str,
    home: "Path",
) -> None:
    """Write the keypair to $HERMES_HOME/aap.json in IdentityFile format."""
    import json as _json
    from datetime import datetime, timezone
    from aap.keys import encode_b64url

    identity_path = home / "aap.json"
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = identity_path.with_suffix(identity_path.suffix + ".tmp")
    tmp.write_text(_json.dumps({
        "private_seed_b64": encode_b64url(private_seed),
        "public_key_b64": encode_b64url(public_key),
        "encryption_private_key_b64": encode_b64url(encryption_private_key),
        "encryption_public_key_b64": encode_b64url(encryption_public_key),
        "address": address,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))
    tmp.chmod(0o600)
    tmp.replace(identity_path)


_HOSTED_DOMAIN = "agentaddress.org"
_HOSTED_RELAY_URL = "https://api.agentaddress.org"
_HOSTED_VERIFIER_URL = "https://verify.agentaddress.org"


def interactive_setup() -> None:
    """`hermes gateway setup` flow for aap.

    Two paths:

    - **Hosted** — the relay's own namespace (``agentaddress.org``). The
      relay both issues the address via ``/aap/addresses/claim`` and
      transports messages for it. One-step user flow: pick a localpart,
      verify by email, done.

    - **BYOD** — the operator owns a domain (e.g. ``dinetable.com``) and
      uses the public relay only as transport. The relay's claim endpoint
      does NOT apply (it's namespace-bound to ``agentaddress.org``);
      instead the domain itself is claimed once via
      ``/aap/domains/claim-start`` + a ``.well-known`` proof, and from
      then on any agent under the domain self-registers. The operator is
      also responsible for serving ``/.well-known/aap-resolve`` on their
      domain so peers can resolve the address.

    Lazy-imports hermes_cli.setup helpers so the plugin stays importable
    outside the CLI (gateway runtime, tests).
    """
    from hermes_cli.setup import (
        prompt,
        prompt_choice,
        prompt_yes_no,
        save_env_value,
        get_env_value,
        print_header,
        print_info,
        print_warning,
        print_success,
    )

    print_header("AAP")
    existing_localpart = (get_env_value("AAP_LOCALPART") or "").strip()
    existing_domain = (get_env_value("AAP_INSTANCE_DOMAIN") or "").strip()
    if existing_localpart:
        existing_addr = (
            f"{existing_localpart}^{existing_domain}"
            if existing_domain
            else existing_localpart
        )
        print_info(f"AAP: already configured ({existing_addr})")
        if not prompt_yes_no("Reconfigure AAP?", False):
            return

    print_info(
        "Your agent gets an AAP address like <localpart>^<domain>. Other "
        "agents reach yours at that address."
    )
    # Always default to the free path — it's the common case for new
    # users and a safe choice for re-runs. Operators on a custom domain
    # arrow down once; the existing domain is still pre-filled inside
    # the BYOD prompt.
    idx = prompt_choice(
        "Address type",
        [
            f"Free address at {_HOSTED_DOMAIN}",
            "Your own domain (e.g. bookings^example.com)",
        ],
        default=0,
    )

    if idx == 0:
        completed = _setup_hosted_path(
            existing_localpart=existing_localpart,
            prompt=prompt,
            prompt_yes_no=prompt_yes_no,
            save_env_value=save_env_value,
            print_info=print_info,
            print_warning=print_warning,
            print_success=print_success,
        )
    else:
        completed = _setup_byod_path(
            existing_domain=(
                existing_domain
                if existing_domain and existing_domain != _HOSTED_DOMAIN
                else ""
            ),
            existing_localpart=existing_localpart,
            existing_relay_url=(get_env_value("AAP_RELAY_URL") or "").strip(),
            existing_verifier_url=(get_env_value("AAP_VERIFIER_URL") or "").strip(),
            existing_trust_root=(get_env_value("AAP_TRUST_LIST_PUBLIC_KEY_B64") or "").strip(),
            prompt=prompt,
            prompt_choice=prompt_choice,
            prompt_yes_no=prompt_yes_no,
            save_env_value=save_env_value,
            print_info=print_info,
            print_warning=print_warning,
            print_success=print_success,
        )
    if not completed:
        return

    print_info(
        "Autonomous mode lets this agent reply to AAP messages directly "
        "without waiting for your confirmation. You'll still see all "
        "messages on your home channel — you just won't have to approve "
        "each reply. Recommended off until you trust the agent."
    )
    autonomous = prompt_yes_no(
        "Enable autonomous AAP replies (no human approval required)?",
        False,
    )
    save_env_value("AAP_AUTONOMOUS", "on" if autonomous else "off")

    print_success("AAP configuration saved to ~/.hermes/.env")
    print_info("Restart the gateway for changes to take effect: hermes gateway restart")


def _setup_hosted_path(
    *,
    existing_localpart: str,
    prompt,
    prompt_yes_no,
    save_env_value,
    print_info,
    print_warning,
    print_success,
) -> bool:
    """Path 1 — claim a localpart in the relay's namespace.

    Returns True on success, False on abort. The wizard's caller handles
    the common tail (autonomous-mode prompt, success message).
    """
    domain = _HOSTED_DOMAIN
    relay_url = _HOSTED_RELAY_URL
    verifier_url = _HOSTED_VERIFIER_URL
    save_env_value("AAP_INSTANCE_DOMAIN", domain)
    save_env_value("AAP_RELAY_URL", relay_url)
    save_env_value("AAP_VERIFIER_URL", verifier_url)
    save_env_value("AAP_TRUST_LIST_PUBLIC_KEY_B64", _HOSTED_TRUST_LIST_PUBLIC_KEY_B64)
    os.environ["AAP_TRUST_LIST_PUBLIC_KEY_B64"] = _HOSTED_TRUST_LIST_PUBLIC_KEY_B64

    # Loop until the user picks an available localpart AND (when the relay
    # supports it) successfully claims it. A rejected claim loops back to the
    # localpart prompt with the rejected name pre-filled — the common case is
    # the right localpart with the wrong email, so the user can hit Enter to
    # keep the name and only re-type the email.
    #
    # The relay's GET /aap/addresses/check endpoint tells us whether a name is
    # free AND whether its base is claimed (needed for the derivative claim
    # flow).
    while True:
        localpart = prompt(
            "AAP localpart (e.g. yourname-bot) — the part before ^ in your AAP address",
            default=existing_localpart or "",
        )
        if not localpart:
            print_warning("Localpart is required — skipping AAP setup")
            return False
        localpart = localpart.strip()

        last_check = _check_localpart_availability(relay_url, localpart)
        status = last_check["status"]
        recovery_mode = False
        if status == "available":
            # Derivative localparts also need the base to be claimed by the
            # same email — surface that requirement now so the user can claim
            # the base first if missing.
            if "+" in localpart and last_check["base_claimed"] is False:
                print_warning(
                    f"'{last_check['base_localpart']}' is unclaimed; claim it "
                    "first (without the +suffix), then re-run setup."
                )
                existing_localpart = ""
                continue
        elif status == "taken":
            # Two reasons a localpart is taken: (1) someone else owns it,
            # or (2) the user owns it — common after a claim whose response
            # was lost to a network timeout (the relay recorded the claim
            # but the client never persisted the keypair). Offer the
            # rotate-key recovery path: re-verify the original email, bind
            # the address to a fresh keypair. The relay rejects with 403 if
            # the email doesn't match the recorded owner, so guessing
            # ownership is safe.
            print_warning(f"{localpart}^{domain} is already claimed.")
            print_info(
                "If you own this address (you claimed it earlier and lost "
                "the key, or a previous setup timed out before saving the "
                "key locally), you can recover it now by re-verifying the "
                "email that originally claimed it."
            )
            if not prompt_yes_no(
                "Recover ownership of this address by re-verifying email?",
                False,
            ):
                existing_localpart = ""
                continue
            recovery_mode = True
        elif status == "malformed":
            print_warning(
                f"'{localpart}' is not a valid localpart shape. "
                "Use lowercase a-z, 0-9, hyphens or underscores, optionally "
                "followed by + and a suffix."
            )
            existing_localpart = ""
            continue
        else:
            # status == "error": relay unreachable or unexpected response.
            # Don't block setup on connectivity — registration at gateway
            # start will catch collisions via TOFU.
            print_info(
                f"(could not reach {relay_url} to check availability — "
                "proceeding anyway; gateway start will catch collisions)"
            )
            save_env_value("AAP_LOCALPART", localpart)
            return True

        claim_result = _run_claim_flow(
            relay_url=relay_url,
            verifier_url=verifier_url,
            domain=domain,
            localpart=localpart,
            base_localpart=(last_check["base_localpart"] if last_check else localpart),
            recovery_mode=recovery_mode,
            prompt_fn=prompt,
            prompt_yes_no_fn=prompt_yes_no,
            print_info_fn=print_info,
            print_warning_fn=print_warning,
            print_success_fn=print_success,
        )
        if claim_result == "claimed":
            save_env_value("AAP_LOCALPART", localpart)
            return True
        if claim_result == "retry":
            existing_localpart = localpart
            continue
        # "aborted": user declined to retry. Don't persist a half-configured
        # AAP setup; bail out of the wizard.
        return False


def _setup_byod_path(
    *,
    existing_domain: str,
    existing_localpart: str,
    existing_relay_url: str,
    existing_verifier_url: str,
    existing_trust_root: str,
    prompt,
    prompt_choice,
    prompt_yes_no,
    save_env_value,
    print_info,
    print_warning,
    print_success,
) -> bool:
    """Path 2 — bring-your-own-domain.

    Steps:
      1. Ask for the domain + localpart.
      2. Ask which relay to point at: the public agentaddress.org relay,
         or a self-hosted one (prompt for URL).
      3. Optionally drive ``/aap/domains/claim-start`` + ``claim-confirm``
         against the chosen relay (one-time per domain).
      4. Generate a keypair and persist ``aap.json``. The address is
         actually bound to that key when the gateway starts and calls
         ``/aap/agents/register`` — first-write wins via TOFU.
      5. Print resolver guidance: the operator still has to serve
         ``/.well-known/aap-resolve`` on their domain so peers can resolve
         the address.
    """
    from pathlib import Path
    from aap.encryption import generate_encryption_keypair
    from aap.keys import encode_b64url, generate_keypair

    domain = prompt(
        "Your domain (e.g. example.com)",
        default=existing_domain or "",
    ).strip().lower()
    if not domain:
        print_warning("Domain is required — skipping AAP setup")
        return False
    if domain == _HOSTED_DOMAIN:
        print_warning(
            f"{_HOSTED_DOMAIN} is the relay's own namespace — use the "
            "'free address' path instead."
        )
        return False

    localpart = prompt(
        "AAP localpart (e.g. yourname-bot) — the part before ^ in your AAP address",
        default=existing_localpart or "",
    ).strip()
    if not localpart:
        print_warning("Localpart is required — skipping AAP setup")
        return False

    # Default the relay sub-choice to "own relay" iff the existing config
    # points at a non-agentaddress.org URL — otherwise default to the
    # public relay (the common case).
    relay_default_idx = (
        1
        if existing_relay_url and existing_relay_url != _HOSTED_RELAY_URL
        else 0
    )
    relay_idx = prompt_choice(
        "Relay",
        [
            f"Public agentaddress.org relay ({_HOSTED_RELAY_URL})",
            "Your own relay (advanced)",
        ],
        default=relay_default_idx,
    )
    if relay_idx == 1:
        relay_url = prompt(
            "Relay URL",
            default=existing_relay_url or f"https://api.{domain}",
        ).strip().rstrip("/")
        if not relay_url:
            print_warning("Relay URL is required — skipping AAP setup")
            return False
        verifier_url = prompt(
            "Verifier URL (used by /aap verify commands)",
            default=existing_verifier_url or _HOSTED_VERIFIER_URL,
        ).strip().rstrip("/")
        if not verifier_url:
            verifier_url = _HOSTED_VERIFIER_URL
        trust_root = prompt(
            "Trust-list public key for your relay (advanced)",
            default=existing_trust_root or "",
        ).strip()
        if not trust_root:
            print_warning(
                "A custom relay needs a pinned trust-list public key so "
                "Hermes can authenticate verifier discovery."
            )
            return False
    else:
        relay_url = _HOSTED_RELAY_URL
        verifier_url = _HOSTED_VERIFIER_URL
        trust_root = _HOSTED_TRUST_LIST_PUBLIC_KEY_B64

    address = f"{localpart}^{domain}"
    print_info("")
    print_info(f"Address: {address}")
    print_info(f"Relay:   {relay_url}")
    print_info("")
    print_info(f"Two one-time setup steps for {domain}:")
    print_info(
        f"  1. Claim {domain} at the relay — proves you control the "
        "domain and opens a billing account."
    )
    print_info(
        f"  2. Serve a signed AgentCard at "
        f"https://{domain}/.well-known/aap-resolve so peers can look up "
        "your address's public key."
    )
    print_info("")

    if prompt_yes_no(f"Run domain claim against {relay_url} now?", True):
        claim_ok = _run_domain_claim_flow(
            relay_url=relay_url,
            domain=domain,
            prompt_fn=prompt,
            print_info_fn=print_info,
            print_warning_fn=print_warning,
            print_success_fn=print_success,
        )
        if not claim_ok and not prompt_yes_no(
            "Save partial AAP config anyway?", False
        ):
            return False
    else:
        print_info(
            f"Skipping domain claim. Until {domain} is claimed at the "
            f"relay, the gateway will fail to register {address}."
        )

    # Save env BEFORE writing identity so a failure between the two
    # leaves env in a state matching the new identity (or vice-versa —
    # gateway start can't start either way until both land).
    save_env_value("AAP_INSTANCE_DOMAIN", domain)
    save_env_value("AAP_RELAY_URL", relay_url)
    save_env_value("AAP_VERIFIER_URL", verifier_url)
    save_env_value("AAP_TRUST_LIST_PUBLIC_KEY_B64", trust_root)
    os.environ["AAP_TRUST_LIST_PUBLIC_KEY_B64"] = trust_root
    save_env_value("AAP_LOCALPART", localpart)

    seed, public = generate_keypair()
    encryption_private, encryption_public = generate_encryption_keypair()
    public_key_b64 = encode_b64url(public)
    home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))

    # Re-running BYOD setup with the same address would generate a fresh
    # keypair and stomp the existing aap.json. The relay's TOFU then
    # rejects the next register with 409 (KeyChangeRejected). Warn the
    # operator so they can keep the existing key if that's what they want.
    identity_path = home / "aap.json"
    if identity_path.exists():
        try:
            import json as _json
            existing_identity = _json.loads(identity_path.read_text())
            existing_addr = existing_identity.get("address", "")
        except (OSError, ValueError):
            existing_addr = ""
        if existing_addr == address:
            print_warning(
                f"An identity for {address} already exists at "
                f"{identity_path}. Generating a fresh keypair will lock "
                "you out at the relay (TOFU rejects key changes)."
            )
            if not prompt_yes_no("Overwrite with new keypair?", False):
                print_info("Keeping existing identity.")
                _print_resolver_guidance(
                    domain=domain,
                    address=address,
                    public_key_b64=existing_identity.get("public_key_b64", ""),
                    relay_url=relay_url,
                    print_info_fn=print_info,
                )
                return True

    _persist_identity(seed, public, encryption_private, encryption_public, address, home)
    print_success(f"Identity saved to {identity_path}")
    _print_resolver_guidance(
        domain=domain,
        address=address,
        public_key_b64=public_key_b64,
        relay_url=relay_url,
        print_info_fn=print_info,
    )
    return True


def _run_domain_claim_flow(
    *,
    relay_url: str,
    domain: str,
    prompt_fn,
    print_info_fn,
    print_warning_fn,
    print_success_fn,
) -> bool:
    """Drive ``POST /aap/domains/claim-start`` + ``claim-confirm``.

    Returns True on success (or 409 ``domain_already_claimed``), False on
    transport/validation failure or operator abort.
    """
    import httpx

    contact = prompt_fn(f"Contact email for the {domain} account").strip()
    if not contact:
        print_warning_fn("Contact email is required — aborting claim")
        return False

    try:
        start = httpx.post(
            f"{relay_url.rstrip('/')}/aap/domains/claim-start",
            json={"domain": domain, "contact_email": contact},
            timeout=15.0,
        )
    except httpx.HTTPError as e:
        print_warning_fn(f"Could not reach relay: {e}")
        return False

    if start.status_code == 409:
        # Already claimed — fine for our purposes (a new agent registers
        # under the existing domain account).
        print_info_fn(f"{domain} is already claimed at the relay — proceeding.")
        return True
    if start.status_code != 200:
        print_warning_fn(
            f"claim-start failed: HTTP {start.status_code}: {start.text[:200]}"
        )
        return False

    body = start.json()
    token = body.get("claim_token")
    if not token:
        print_warning_fn("claim-start response missing claim_token — aborting")
        return False

    print_info_fn("")
    print_info_fn(f"Verification email sent to {contact}.")
    print_info_fn("")
    print_info_fn(
        f"Place this token at https://{domain}/.well-known/aap-domain-claim "
        "(raw response body — any content-type):"
    )
    print_info_fn(f"  {token}")
    print_info_fn("")
    # We don't actually need the input value — pressing Enter is the signal.
    prompt_fn("Press Enter when the token is in place")

    otp = prompt_fn("Enter the OTP code from the email").strip()
    if not otp:
        print_warning_fn("OTP is required — aborting claim")
        return False

    try:
        confirm = httpx.post(
            f"{relay_url.rstrip('/')}/aap/domains/claim-confirm",
            json={"domain": domain, "otp_token": otp},
            timeout=15.0,
        )
    except httpx.HTTPError as e:
        print_warning_fn(f"claim-confirm request failed: {e}")
        return False

    if confirm.status_code == 201:
        print_success_fn(f"{domain} claimed at the relay.")
        return True
    if confirm.status_code == 409:
        # Concurrent claim won — same effect as 409 from claim-start.
        print_info_fn(
            f"{domain} was claimed by a concurrent request — proceeding."
        )
        return True
    print_warning_fn(
        f"claim-confirm failed: HTTP {confirm.status_code}: {confirm.text[:200]}"
    )
    return False


def _print_resolver_guidance(
    *,
    domain: str,
    address: str,
    public_key_b64: str,
    relay_url: str,
    print_info_fn,
) -> None:
    """Tell the operator what they need to serve on their domain so peers
    can resolve ``<localpart>^<domain>``.

    The wizard can't host the resolver — it's domain-side infrastructure
    only the operator can deploy. We surface the exact shape of the
    AgentCard so a junior backend dev can wire it up in a few lines.
    """
    print_info_fn("")
    print_info_fn(f"Peers resolve {address} by POSTing to:")
    print_info_fn(f"  https://{domain}/.well-known/aap-resolve")
    print_info_fn(
        '  body: {"localpart": "<localpart>"}'
    )
    print_info_fn("")
    print_info_fn(
        "The response must be a signed Envelope whose payload is an "
        "AgentCard (aap.agent-card/v1) with:"
    )
    print_info_fn(f'  address:    "{address}"')
    print_info_fn(f'  did:        "did:web:{domain}#agent"')
    if public_key_b64:
        print_info_fn(f'  public_key: "{public_key_b64}"')
    else:
        print_info_fn('  public_key: "<your b64url public key>"')
    print_info_fn(
        f'  endpoints:  [{{"type": "didcomm", "uri": "{relay_url}"}}]'
    )
    print_info_fn("")
    print_info_fn(
        "The envelope's signing key must match the AgentCard's public_key "
        "and the envelope issuer must equal the AAP address. See "
        "aap.payloads.AgentCard for the full schema."
    )


def _run_claim_flow(
    *,
    relay_url: str,
    verifier_url: str,
    domain: str,
    localpart: str,
    base_localpart: str,
    recovery_mode: bool = False,
    prompt_fn,
    prompt_yes_no_fn,
    print_info_fn,
    print_warning_fn,
    print_success_fn,
) -> str:
    """Run the email-verify + claim-or-rotate handshake against the relay.

    When ``recovery_mode`` is False (default), submits a fresh ``claim``
    against an available localpart. When True, the localpart is already
    claimed and the user is recovering ownership: same email-verify
    handshake, but the final POST is to ``rotate-key`` which updates the
    agent row's pubkey to the new keypair rather than inserting a new
    claim row.

    Returns one of:
      ``"claimed"`` — claim or rotation succeeded, identity persisted.
      ``"retry"``   — failed but the user wants to try again (caller should
                       loop back to the localpart prompt; the rejected
                       localpart is a sensible default for the next attempt).
      ``"aborted"`` — failed and the user does not want to retry (caller
                       should bail out without persisting AAP config).
    """
    import os
    from pathlib import Path
    from aap.encryption import generate_encryption_keypair
    from aap.keys import encode_b64url, generate_keypair

    is_derivative = "+" in localpart
    subject = f"{localpart}^{domain}"

    print_info_fn("")
    if recovery_mode:
        print_info_fn(f"Recovering ownership of {subject} — re-verified by email.")
        print_info_fn(
            "Use the email that originally claimed this address. The relay "
            "binds the address to a fresh keypair only if the email matches "
            "the recorded owner."
        )
    else:
        print_info_fn(f"Claiming {subject} — verified by email.")
        if is_derivative:
            print_info_fn(
                f"This is a derivative under base '{base_localpart}'. Use the email "
                f"that originally claimed '{base_localpart}'."
            )

    email = prompt_fn("Email for verification").strip()
    if not email:
        op_label = "recovery" if recovery_mode else "claim"
        print_warning_fn(f"Email is required — aborting {op_label}")
        return "aborted"

    seed, public = generate_keypair()
    public_key_b64 = encode_b64url(public)
    encryption_private, encryption_public = generate_encryption_keypair()
    encryption_public_key_b64 = encode_b64url(encryption_public)

    attestation = _drive_email_verification(
        relay_url=relay_url,
        verifier_url=verifier_url,
        subject_address=subject,
        seed=seed,
        public_key_b64=public_key_b64,
        email=email,
        prompt_fn=prompt_fn,
        print_info_fn=print_info_fn,
        print_warning_fn=print_warning_fn,
    )
    if attestation is None:
        op_label = "recovery" if recovery_mode else "claim"
        print_warning_fn(f"Email verification did not complete — {op_label} aborted")
        if prompt_yes_no_fn("Try again with a different localpart or email?", True):
            return "retry"
        return "aborted"

    submit_fn = _submit_address_rotate if recovery_mode else _submit_address_claim
    ok, message = submit_fn(
        relay_url=relay_url,
        seed=seed,
        public_key_b64=public_key_b64,
        encryption_public_key_b64=encryption_public_key_b64,
        localpart=localpart,
        domain=domain,
        attestation_envelope_dict=attestation,
    )
    if not ok:
        op_label = "Recovery" if recovery_mode else "Claim"
        print_warning_fn(f"{op_label} rejected: {message}")
        if prompt_yes_no_fn("Try again with a different localpart or email?", True):
            return "retry"
        return "aborted"

    home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))
    _persist_identity(seed, public, encryption_private, encryption_public, subject, home)
    verb = "Rebound" if recovery_mode else "Claimed"
    print_success_fn(f"{verb} {subject}; identity saved to {home / 'aap.json'}")
    return "claimed"


def register(ctx: Any) -> None:
    """Hermes plugin entrypoint.

    Wires aap into Hermes's platform registry with the full kwargs contract,
    plus optional tool and slash-command hooks if the ctx supports them.

    Crucially, env reads (Settings, identity load) happen **inside**
    ``adapter_factory`` — not at register time. ``register()`` is called
    during plugin discovery, long before any platform is enabled or the
    user has had a chance to configure AAP_LOCALPART. Eager env reads
    here would make `hermes plugins list` and `hermes gateway setup`
    crash for unconfigured users.
    """
    # TODO(remove-when-upstream-flag-lands): Hermes fires a "no home
    # channel is set for <Platform>" prompt to the user on first message
    # from any platform whose <PLATFORM>_HOME_CHANNEL env var is empty.
    # The prompt is delivered via ``source.platform`` — which for AAP
    # means it gets shipped OUT as an AAP envelope to the peer agent,
    # confusing them. AAP isn't a user-chat surface, so the prompt
    # shouldn't fire at all. Planting AAP_HOME_CHANNEL=auto satisfies
    # Hermes's existence check. Remove this when upstream Hermes adds
    # a ``skip_home_channel_prompt`` flag to PlatformEntry.
    os.environ.setdefault("AAP_HOME_CHANNEL", "auto")
    # TODO(remove-when-upstream-flag-lands): Hermes's ``_is_user_authorized``
    # treats unknown senders as unauthorized and (for DMs) ships a real
    # pairing code back via the adapter — for AAP that goes OUT to the
    # peer agent. AAP's authoritative trust gate is capability tokens
    # (enforced inside adapter._dispatch), so default every AAP peer to
    # Hermes-authorized. Operators can flip this to "false" to opt into
    # Hermes-layer gating.
    os.environ.setdefault("AAP_ALLOW_ALL_USERS", "true")

    # TODO(remove-when-upstream-flag-lands): Hermes's tool-progress
    # broadcaster (gateway/run.py:15763) calls adapter.send() with
    # "🔍 <tool_name>: <args>" text whenever the LLM invokes a tool, so
    # peers see chatter like "🔍 session_search: 'recall: ...'" as AAP
    # envelopes. v0.5.4 tried mutating _PLATFORM_DEFAULTS["aap"] but the
    # user's display.tool_progress=all global beats per-platform defaults
    # in the resolution order. v0.5.5 patches resolve_display_setting
    # itself so AAP forces "off" unless the user has explicitly set
    # display.platforms.aap.tool_progress (which still wins).
    try:
        from gateway import display_config as _hermes_display_config
        if not getattr(_hermes_display_config.resolve_display_setting, "_aap_hermes_patched", False):
            _original_resolve = _hermes_display_config.resolve_display_setting

            def _aap_aware_resolve(user_config, platform_key, setting, fallback=None):
                if platform_key == "aap" and setting == "tool_progress":
                    display_cfg = (
                        user_config.get("display") if isinstance(user_config, dict) else None
                    ) or {}
                    platforms = display_cfg.get("platforms") or {}
                    aap_overrides = platforms.get("aap")
                    if isinstance(aap_overrides, dict) and "tool_progress" in aap_overrides:
                        return _original_resolve(user_config, platform_key, setting, fallback)
                    return "off"
                return _original_resolve(user_config, platform_key, setting, fallback)

            _aap_aware_resolve._aap_hermes_patched = True
            _hermes_display_config.resolve_display_setting = _aap_aware_resolve
    except ImportError:
        pass  # older Hermes without display_config — no-op

    from ._hermes_base import Platform
    from .adapter import AAPPlatformAdapter
    from .config import Settings, build_address, decode_trust_list_public_key
    from aap.identity import load_or_generate
    from .tools import (
        AAP_DESCRIBE_SERVICE_SCHEMA,
        AAP_LIST_RELATIONSHIPS_SCHEMA,
        AAP_LIST_SERVICES_SCHEMA,
        AAP_PROPOSE_FRIENDSHIP_SCHEMA,
        AAP_PROPOSE_RELATIONSHIP_SCHEMA,
        AAP_REVOKE_RELATIONSHIP_SCHEMA,
        AAP_GROUP_COMPLETE_SCHEMA,
        AAP_GROUP_LIST_SCHEMA,
        AAP_GROUP_SEND_SCHEMA,
        AAP_GROUP_START_SCHEMA,
        AAP_SEND_MESSAGE_SCHEMA,
        AAP_SEND_SERVICE_REQUEST_SCHEMA,
        AAP_VERIFY_CONFIRM_SCHEMA,
        AAP_VERIFY_START_SCHEMA,
    )

    def adapter_factory(cfg: Any) -> AAPPlatformAdapter:
        from aap.verifiers import TrustListCache, VerifierPubkeyCache
        from aap.services import ServiceCatalogCache
        from aap.relationships import RelationshipStore
        from aap.service_followups import FollowupGrantStore
        from aap.conversations import ConversationStore
        from aap.stores.attestations import AttestationStore
        from aap.stores.pending_proposals import PendingProposalStore
        from aap.stores.identity_bindings import IdentityBindingStore
        from aap.stores.consent import PendingConsent
        from aap.stores.outbound_contacts import OutboundContactStore
        from aap.stores.verification_flow import PendingVerifications
        from aap.stores.pending_introductions import PendingIntroductions
        from aap.pending_responses import PendingResponses
        from .adapter import AAPAdapterStores, _resolve_agent_public_key
        from .service_request_origins import ServiceRequestOriginIndex

        settings = Settings()
        address = build_address(settings)
        trust_list_public_key = decode_trust_list_public_key(
            settings.AAP_TRUST_LIST_PUBLIC_KEY_B64
        )
        # HERMES_HOME-aware so each profile gets its own identity file
        # (matches the pattern used by every other aap-* state file).
        home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))
        identity_path = home / "aap.json"
        identity = load_or_generate(
            identity_path=identity_path,
            env_seed_b64=settings.AAP_PRIVATE_SEED_B64,
            address=address,
        )
        logger.info("aap-hermes %s starting for %s", __version__, address)

        stores = AAPAdapterStores(
            trust_list_cache=TrustListCache(
                cache_path=home / "aap-trusted-verifiers.json",
                overrides_path=home / "aap-trusted-verifiers-overrides.json",
                trust_list_public_key=trust_list_public_key,
            ),
            verifier_pubkey_cache=VerifierPubkeyCache(cache_dir=home / "aap-verifier-pubkeys"),
            service_catalog_cache=ServiceCatalogCache(
                cache_dir=home / "aap-service-catalog-cache",
                agent_public_key_resolver=_resolve_agent_public_key,
            ),
            relationships=RelationshipStore.load(home),
            followup_grants=FollowupGrantStore.load(home),
            conversations=ConversationStore.load(home),
            attestations=AttestationStore.load(home),
            pending_proposals=PendingProposalStore.load(home),
            identity_bindings=IdentityBindingStore.load(home),
            pending_consents=PendingConsent.load(home),
            outbound_contacts=OutboundContactStore.load(home),
            pending_verifications=PendingVerifications.load(home),
            pending_introductions=PendingIntroductions.load(home),
            service_request_origins=ServiceRequestOriginIndex(base_dir=home),
            pending_responses=PendingResponses(),
        )

        return AAPPlatformAdapter(
            config=cfg,
            platform=Platform("aap"),
            relay_url=settings.AAP_RELAY_URL,
            identity=identity,
            stores=stores,
        )

    if not hasattr(ctx, "register_platform"):
        logger.error("Hermes ctx missing register_platform — plugin can't load")
        return

    ctx.register_platform(
        name="aap",
        label="AAP",
        adapter_factory=adapter_factory,
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["AAP_LOCALPART"],
        allowed_users_env="AAP_ALLOWED_USERS",
        allow_all_env="AAP_ALLOW_ALL_USERS",
        install_hint="pip install -r requirements.txt (deps: httpx, pydantic-settings, aap, rfc8785, cryptography)",
        setup_fn=interactive_setup,
        env_enablement_fn=_env_enablement,
        emoji="🛰",
        pii_safe=False,
        allow_update_command=True,
        platform_hint=_build_platform_hint(),
    )

    if hasattr(ctx, "register_tool"):
        from . import _runtime
        ctx.register_tool(
            name=AAP_SEND_MESSAGE_SCHEMA["name"],
            toolset="aap",
            schema=AAP_SEND_MESSAGE_SCHEMA,
            handler=_runtime.tool_handler_wrapper,
            is_async=True,
            description=AAP_SEND_MESSAGE_SCHEMA["description"],
            emoji="🛰",
        )
        ctx.register_tool(
            name=AAP_GROUP_START_SCHEMA["name"],
            toolset="aap",
            schema=AAP_GROUP_START_SCHEMA,
            handler=_runtime.group_start_tool_wrapper,
            is_async=True,
            description=AAP_GROUP_START_SCHEMA["description"],
            emoji="🛰",
        )
        ctx.register_tool(
            name=AAP_GROUP_LIST_SCHEMA["name"],
            toolset="aap",
            schema=AAP_GROUP_LIST_SCHEMA,
            handler=_runtime.group_list_tool_wrapper,
            is_async=True,
            description=AAP_GROUP_LIST_SCHEMA["description"],
            emoji="🛰",
        )
        ctx.register_tool(
            name=AAP_GROUP_SEND_SCHEMA["name"],
            toolset="aap",
            schema=AAP_GROUP_SEND_SCHEMA,
            handler=_runtime.group_send_tool_wrapper,
            is_async=True,
            description=AAP_GROUP_SEND_SCHEMA["description"],
            emoji="🛰",
        )
        ctx.register_tool(
            name=AAP_GROUP_COMPLETE_SCHEMA["name"],
            toolset="aap",
            schema=AAP_GROUP_COMPLETE_SCHEMA,
            handler=_runtime.group_complete_tool_wrapper,
            is_async=True,
            description=AAP_GROUP_COMPLETE_SCHEMA["description"],
            emoji="🛰",
        )
        # v0.6 services + relationships
        ctx.register_tool(
            name=AAP_LIST_SERVICES_SCHEMA["name"],
            toolset="aap",
            schema=AAP_LIST_SERVICES_SCHEMA,
            handler=_runtime.list_services_tool_wrapper,
            is_async=True,
            description=AAP_LIST_SERVICES_SCHEMA["description"],
            emoji="🛰",
        )
        ctx.register_tool(
            name=AAP_DESCRIBE_SERVICE_SCHEMA["name"],
            toolset="aap",
            schema=AAP_DESCRIBE_SERVICE_SCHEMA,
            handler=_runtime.describe_service_tool_wrapper,
            is_async=True,
            description=AAP_DESCRIBE_SERVICE_SCHEMA["description"],
            emoji="🛰",
        )
        ctx.register_tool(
            name=AAP_SEND_SERVICE_REQUEST_SCHEMA["name"],
            toolset="aap",
            schema=AAP_SEND_SERVICE_REQUEST_SCHEMA,
            handler=_runtime.send_service_request_tool_wrapper,
            is_async=True,
            description=AAP_SEND_SERVICE_REQUEST_SCHEMA["description"],
            emoji="🛰",
        )
        ctx.register_tool(
            name=AAP_PROPOSE_RELATIONSHIP_SCHEMA["name"],
            toolset="aap",
            schema=AAP_PROPOSE_RELATIONSHIP_SCHEMA,
            handler=_runtime.propose_relationship_tool_wrapper,
            is_async=True,
            description=AAP_PROPOSE_RELATIONSHIP_SCHEMA["description"],
            emoji="🛰",
        )
        ctx.register_tool(
            name=AAP_PROPOSE_FRIENDSHIP_SCHEMA["name"],
            toolset="aap",
            schema=AAP_PROPOSE_FRIENDSHIP_SCHEMA,
            handler=_runtime.propose_friendship_tool_wrapper,
            is_async=True,
            description=AAP_PROPOSE_FRIENDSHIP_SCHEMA["description"],
            emoji="🛰",
        )
        ctx.register_tool(
            name=AAP_LIST_RELATIONSHIPS_SCHEMA["name"],
            toolset="aap",
            schema=AAP_LIST_RELATIONSHIPS_SCHEMA,
            handler=_runtime.list_relationships_tool_wrapper,
            is_async=True,
            description=AAP_LIST_RELATIONSHIPS_SCHEMA["description"],
            emoji="🛰",
        )
        ctx.register_tool(
            name=AAP_REVOKE_RELATIONSHIP_SCHEMA["name"],
            toolset="aap",
            schema=AAP_REVOKE_RELATIONSHIP_SCHEMA,
            handler=_runtime.revoke_relationship_tool_wrapper,
            is_async=True,
            description=AAP_REVOKE_RELATIONSHIP_SCHEMA["description"],
            emoji="🛰",
        )
        ctx.register_tool(
            name=AAP_VERIFY_START_SCHEMA["name"],
            toolset="aap",
            schema=AAP_VERIFY_START_SCHEMA,
            handler=_runtime.verify_start_tool_wrapper,
            is_async=True,
            description=AAP_VERIFY_START_SCHEMA["description"],
            emoji="🛰",
        )
        ctx.register_tool(
            name=AAP_VERIFY_CONFIRM_SCHEMA["name"],
            toolset="aap",
            schema=AAP_VERIFY_CONFIRM_SCHEMA,
            handler=_runtime.verify_confirm_tool_wrapper,
            is_async=True,
            description=AAP_VERIFY_CONFIRM_SCHEMA["description"],
            emoji="🛰",
        )
    else:
        logger.warning("Hermes ctx missing register_tool — aap_send_message unavailable")

    if hasattr(ctx, "register_command"):
        from . import _runtime
        ctx.register_command(
            name="aap",
            handler=_runtime.command_handler_wrapper,
            description="AAP commands: send, whoami, status",
            args_hint="<send|whoami|status>",
        )
    else:
        logger.warning(
            "Hermes ctx missing register_command — /aap slash command unavailable. "
            "Use the aap_send_message LLM tool instead."
        )

    # Pre-dispatch hook: a bare "approve"/"deny" reply on the home channel
    # resolves the most-recent pending capability_request without the user
    # having to type the explicit /aap approve <nonce> slash command.
    if hasattr(ctx, "register_hook"):
        from . import _runtime
        ctx.register_hook("pre_gateway_dispatch", _runtime.predispatch_group_reply_bridge)
        ctx.register_hook("pre_gateway_dispatch", _runtime.predispatch_consent_check)
    else:
        logger.debug(
            "Hermes ctx missing register_hook — bare approve/deny unavailable; "
            "users must type /aap approve <nonce> explicitly."
        )

    logger.info("aap-hermes %s registered with Hermes plugin context", __version__)

    # Safety net: Hermes core's slash-command dispatch swallows handler
    # exceptions at DEBUG level (gateway/run.py: "Plugin command dispatch
    # failed (non-fatal)") and falls through to a generic "Unrecognized
    # slash command" reply. That hid an actual ModuleNotFoundError from a
    # bad import for hours during the v0.6 work. Promote our handler's
    # exceptions to WARNING with a traceback so future regressions are
    # immediately visible.
    try:
        import asyncio as _asyncio
        import hermes_cli.plugins as _hp  # type: ignore
        if not getattr(_hp, "_aap_handler_error_wrap_installed", False):
            _orig_lookup = _hp.get_plugin_command_handler

            def _aap_logging_lookup(name):
                handler = _orig_lookup(name)
                if handler is None or name != "aap":
                    return handler

                async def _logged_handler(*args, **kwargs):
                    try:
                        result = handler(*args, **kwargs)
                        if _asyncio.iscoroutine(result):
                            result = await result
                        return result
                    except Exception as _exc:
                        logger.warning(
                            "aap-hermes handler raised %s: %r",
                            type(_exc).__name__, _exc,
                            exc_info=True,
                        )
                        raise

                return _logged_handler

            _hp.get_plugin_command_handler = _aap_logging_lookup
            _hp._aap_handler_error_wrap_installed = True
    except Exception as _wrap_err:
        logger.debug("aap-hermes handler error wrapper install failed: %s", _wrap_err)
