"""Tests for the register(ctx) entrypoint and tool/command wiring."""

import os
from typing import Any

from aap_hermes import register
from aap_hermes.tools import AAP_SEND_MESSAGE_SCHEMA


_TRUST_ROOT = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


class FakeCtx:
    """Mirrors the real Hermes PluginContext API shape (kwargs-based)."""

    def __init__(self):
        self.platforms: dict[str, dict[str, Any]] = {}
        self.tools: list[dict[str, Any]] = []
        self.commands: dict[str, dict[str, Any]] = {}

    def register_platform(self, name: str, **kwargs: Any) -> None:
        self.platforms[name] = kwargs

    def register_tool(self, name: str, **kwargs: Any) -> None:
        self.tools.append({"name": name, **kwargs})

    def register_command(self, name: str, **kwargs: Any) -> None:
        self.commands[name] = kwargs


def test_register_wires_platform_tool_and_command(monkeypatch, tmp_path):
    monkeypatch.setenv("AAP_LOCALPART", "chris")
    monkeypatch.setenv("AAP_INSTANCE_DOMAIN", "test.example")
    monkeypatch.setenv("AAP_TRUST_LIST_PUBLIC_KEY_B64", _TRUST_ROOT)
    monkeypatch.setenv("HOME", str(tmp_path))

    ctx = FakeCtx()
    register(ctx)

    assert "aap" in ctx.platforms
    entry = ctx.platforms["aap"]
    assert entry["label"] == "AAP"
    assert callable(entry["adapter_factory"])
    assert callable(entry["check_fn"])
    assert callable(entry["env_enablement_fn"])
    assert callable(entry["setup_fn"])
    assert "AAP_LOCALPART" in entry["required_env"]
    assert "AAP_TRUST_LIST_PUBLIC_KEY_B64" not in entry["required_env"]

    # Plugin registers the v0.6 toolset: chat plus services + relationships.
    tools_by_name = {t["name"]: t for t in ctx.tools}
    expected_tools = {
        "aap_send_message",
        "aap_group_start",
        "aap_group_complete",
        "aap_group_list",
        "aap_group_send",
        "aap_list_services",
        "aap_describe_service",
        "aap_send_service_request",
        "aap_propose_friendship",
        "aap_propose_relationship",
        "aap_revoke_relationship",
        "aap_list_relationships",
        "aap_verify_start",
        "aap_verify_confirm",
    }
    assert set(tools_by_name) == expected_tools
    send_tool = tools_by_name["aap_send_message"]
    assert send_tool["toolset"] == "aap"
    assert send_tool["schema"] == AAP_SEND_MESSAGE_SCHEMA
    assert send_tool["is_async"] is True
    for new_name in expected_tools - {"aap_send_message"}:
        assert tools_by_name[new_name]["toolset"] == "aap"
        assert tools_by_name[new_name]["is_async"] is True
    assert "aap" in ctx.commands
    assert callable(ctx.commands["aap"]["handler"])

    # Identity file is NOT created at register time — only when the
    # adapter is actually built (env-read deferred to adapter_factory).
    assert not (tmp_path / ".hermes" / "aap.json").exists()

    # Calling the factory then triggers identity generation.
    factory = entry["adapter_factory"]
    class FakeConfig:
        extra: dict = {}
    factory(FakeConfig())
    assert (tmp_path / ".hermes" / "aap.json").exists()


def test_adapter_factory_honors_hermes_home(monkeypatch, tmp_path):
    """Identity must be written under $HERMES_HOME so each Hermes profile
    gets its own aap.json instead of all profiles sharing ~/.hermes/aap.json."""
    profile_home = tmp_path / "profiles" / "work"
    profile_home.mkdir(parents=True)

    monkeypatch.setenv("AAP_LOCALPART", "chris-work")
    monkeypatch.setenv("AAP_INSTANCE_DOMAIN", "test.example")
    monkeypatch.setenv("AAP_TRUST_LIST_PUBLIC_KEY_B64", _TRUST_ROOT)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(profile_home))

    ctx = FakeCtx()
    register(ctx)
    factory = ctx.platforms["aap"]["adapter_factory"]
    class FakeConfig:
        extra: dict = {}
    factory(FakeConfig())

    assert (profile_home / "aap.json").exists()
    assert not (tmp_path / ".hermes" / "aap.json").exists()


def test_register_does_not_read_env_at_discovery(monkeypatch, tmp_path):
    """register() must not crash if AAP_LOCALPART is unset — discovery happens
    long before the user has configured anything."""
    monkeypatch.delenv("AAP_LOCALPART", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    ctx = FakeCtx()
    register(ctx)  # must not raise

    assert "aap" in ctx.platforms


def test_register_handles_missing_register_tool(monkeypatch, tmp_path):
    monkeypatch.setenv("AAP_LOCALPART", "chris")
    monkeypatch.setenv("HOME", str(tmp_path))

    class CtxWithoutTool:
        def __init__(self):
            self.platforms = {}
            self.commands = {}
        def register_platform(self, name: str, **kwargs: Any) -> None:
            self.platforms[name] = kwargs
        def register_command(self, name: str, **kwargs: Any) -> None:
            self.commands[name] = kwargs

    ctx = CtxWithoutTool()
    register(ctx)  # should not raise

    assert "aap" in ctx.platforms


def test_env_enablement_returns_none_without_localpart(monkeypatch):
    from aap_hermes import _env_enablement

    monkeypatch.delenv("AAP_LOCALPART", raising=False)
    assert _env_enablement() is None


def test_env_enablement_returns_dict_with_localpart(monkeypatch):
    from aap_hermes import _env_enablement
    from aap_hermes.config import DEFAULT_TRUST_LIST_PUBLIC_KEY_B64

    monkeypatch.setenv("AAP_LOCALPART", "alice")
    monkeypatch.setenv("AAP_INSTANCE_DOMAIN", "example.com")
    monkeypatch.setenv("AAP_RELAY_URL", "https://relay.example.com")
    monkeypatch.delenv("AAP_TRUST_LIST_PUBLIC_KEY_B64", raising=False)

    seed = _env_enablement()
    assert seed == {
        "localpart": "alice",
        "domain": "example.com",
        "relay_url": "https://relay.example.com",
        "trust_list_public_key_b64": DEFAULT_TRUST_LIST_PUBLIC_KEY_B64,
    }


def test_env_enablement_honors_trust_root_override(monkeypatch):
    from aap_hermes import _env_enablement

    monkeypatch.setenv("AAP_LOCALPART", "alice")
    monkeypatch.setenv("AAP_TRUST_LIST_PUBLIC_KEY_B64", _TRUST_ROOT)

    seed = _env_enablement()
    assert seed is not None
    assert seed["trust_list_public_key_b64"] == _TRUST_ROOT


def test_check_requirements(monkeypatch):
    from aap_hermes import check_requirements

    monkeypatch.delenv("AAP_LOCALPART", raising=False)
    monkeypatch.delenv("AAP_TRUST_LIST_PUBLIC_KEY_B64", raising=False)
    assert check_requirements() is False

    monkeypatch.setenv("AAP_LOCALPART", "bob")
    assert check_requirements() is True


def test_register_plants_aap_home_channel_env_var(monkeypatch, tmp_path):
    """v0.5.1 hack: register() must plant AAP_HOME_CHANNEL=auto if unset
    so Hermes's "no home channel set" prompt doesn't fire on first AAP
    message (the prompt otherwise ships OUT via AAP to the peer agent).
    """
    monkeypatch.setenv("AAP_LOCALPART", "chris")
    monkeypatch.setenv("AAP_TRUST_LIST_PUBLIC_KEY_B64", _TRUST_ROOT)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AAP_HOME_CHANNEL", raising=False)

    ctx = FakeCtx()
    register(ctx)

    assert os.environ.get("AAP_HOME_CHANNEL") == "auto"


def test_register_does_not_overwrite_user_aap_home_channel(monkeypatch, tmp_path):
    """If the user has set AAP_HOME_CHANNEL explicitly, we don't clobber it."""
    monkeypatch.setenv("AAP_LOCALPART", "chris")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AAP_HOME_CHANNEL", "user-explicitly-set")

    ctx = FakeCtx()
    register(ctx)

    assert os.environ.get("AAP_HOME_CHANNEL") == "user-explicitly-set"


def test_platform_hint_includes_human_gate_language(monkeypatch, tmp_path):
    """Default (AAP_AUTONOMOUS unset/off): the platform_hint must instruct
    the LLM not to reply autonomously."""
    monkeypatch.delenv("AAP_AUTONOMOUS", raising=False)
    monkeypatch.setenv("AAP_LOCALPART", "chris")
    monkeypatch.setenv("HOME", str(tmp_path))

    ctx = FakeCtx()
    register(ctx)

    hint = ctx.platforms["aap"]["platform_hint"]
    lower = hint.lower()
    assert "do not reply" in lower or "wait for" in lower
    assert "owner" in lower or "user" in lower
    assert "home channel" in lower or "primary chat" in lower
    assert "aap_send_message" in hint
    # Must NOT contain the autonomous-mode signal
    assert "autonomous mode" not in lower


def test_platform_hint_autonomous_mode_when_env_set(monkeypatch, tmp_path):
    """When AAP_AUTONOMOUS=on, the hint switches to autonomous + anti-loop."""
    monkeypatch.setenv("AAP_AUTONOMOUS", "on")
    monkeypatch.setenv("AAP_LOCALPART", "chris")
    monkeypatch.setenv("HOME", str(tmp_path))

    ctx = FakeCtx()
    register(ctx)

    hint = ctx.platforms["aap"]["platform_hint"]
    lower = hint.lower()
    # Autonomous signal present
    assert "autonomous mode" in lower
    # Permission to reply directly
    assert "reply directly" in lower or "without waiting" in lower
    # Anti-loop guidance present
    assert "loop" in lower or "acknowledgments" in lower
    # Human-gate language absent
    assert "do not reply to aap messages without" not in lower
    assert "wait for their guidance" not in lower


def test_platform_hint_autonomous_off_means_human_gate(monkeypatch, tmp_path):
    """AAP_AUTONOMOUS=off (explicit) behaves the same as unset — human gate."""
    monkeypatch.setenv("AAP_AUTONOMOUS", "off")
    monkeypatch.setenv("AAP_LOCALPART", "chris")
    monkeypatch.setenv("HOME", str(tmp_path))

    ctx = FakeCtx()
    register(ctx)

    hint = ctx.platforms["aap"]["platform_hint"]
    assert "autonomous mode" not in hint.lower()


def test_human_gate_hint_forbids_pairing_flows(monkeypatch, tmp_path):
    """Human-gate hint must explicitly tell the LLM not to invent pairing
    codes, verification flows, or hermes-pairing-approve commands."""
    monkeypatch.delenv("AAP_AUTONOMOUS", raising=False)
    monkeypatch.setenv("AAP_LOCALPART", "chris")
    monkeypatch.setenv("HOME", str(tmp_path))

    ctx = FakeCtx()
    register(ctx)

    hint = ctx.platforms["aap"]["platform_hint"]
    lower = hint.lower()
    assert "pairing" in lower
    assert "no pairing" in lower or "no handshake" in lower or "do not exist" in lower
    # v0.6.0: relationship records replace capability tokens as the auth mechanism.
    assert "relationship" in lower


def test_autonomous_hint_forbids_pairing_flows(monkeypatch, tmp_path):
    """Autonomous hint must also include the anti-pairing prohibition."""
    monkeypatch.setenv("AAP_AUTONOMOUS", "on")
    monkeypatch.setenv("AAP_LOCALPART", "chris")
    monkeypatch.setenv("HOME", str(tmp_path))

    ctx = FakeCtx()
    register(ctx)

    hint = ctx.platforms["aap"]["platform_hint"]
    lower = hint.lower()
    assert "pairing" in lower
    assert "no pairing" in lower or "no handshake" in lower or "do not exist" in lower
    assert "relationship" in lower


def test_register_declares_aap_allow_all_env(monkeypatch, tmp_path):
    """v0.5.3: register_platform must pass allowed_users_env and
    allow_all_env so Hermes's _is_user_authorized recognizes AAP's
    auth env vars (otherwise every AAP peer gets the unauthorized-DM
    pairing-code message)."""
    monkeypatch.setenv("AAP_LOCALPART", "chris")
    monkeypatch.setenv("HOME", str(tmp_path))

    ctx = FakeCtx()
    register(ctx)

    entry = ctx.platforms["aap"]
    assert entry["allowed_users_env"] == "AAP_ALLOWED_USERS"
    assert entry["allow_all_env"] == "AAP_ALLOW_ALL_USERS"


def test_register_plants_aap_allow_all_users_default_true(monkeypatch, tmp_path):
    """v0.5.3: register() must plant AAP_ALLOW_ALL_USERS=true by default
    so Hermes treats every AAP peer as authorized. Our /aap approve peer
    store is the authoritative trust gate for AAP — Hermes's
    human-chat-style allowlist doesn't apply."""
    monkeypatch.setenv("AAP_LOCALPART", "chris")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AAP_ALLOW_ALL_USERS", raising=False)

    ctx = FakeCtx()
    register(ctx)

    import os
    assert os.environ.get("AAP_ALLOW_ALL_USERS") == "true"


def test_register_does_not_overwrite_user_aap_allow_all_users(monkeypatch, tmp_path):
    """If the operator explicitly set AAP_ALLOW_ALL_USERS (e.g. to 'false'
    to opt into Hermes-layer allowlist gating), don't clobber it."""
    monkeypatch.setenv("AAP_LOCALPART", "chris")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AAP_ALLOW_ALL_USERS", "false")

    ctx = FakeCtx()
    register(ctx)

    import os
    assert os.environ.get("AAP_ALLOW_ALL_USERS") == "false"


def test_register_patches_resolve_for_aap_tool_progress(monkeypatch, tmp_path):
    """v0.5.5: register() must monkey-patch
    gateway.display_config.resolve_display_setting so that for AAP
    tool_progress lookups, "off" wins over the user's global
    display.tool_progress (which would otherwise beat v0.5.4's
    _PLATFORM_DEFAULTS approach)."""
    import sys
    import types

    def _real_resolve(user_config, platform_key, setting, fallback=None):
        # Stand-in for the real resolve_display_setting. Mirrors the real
        # resolution order: per-platform → global → built-in default.
        display_cfg = (user_config.get("display") if isinstance(user_config, dict) else None) or {}
        platforms = display_cfg.get("platforms") or {}
        plat_overrides = platforms.get(platform_key)
        if isinstance(plat_overrides, dict) and setting in plat_overrides:
            return plat_overrides[setting]
        if setting in display_cfg:
            return display_cfg[setting]
        return fallback

    fake_gateway = types.ModuleType("gateway")
    fake_display = types.ModuleType("gateway.display_config")
    fake_display.resolve_display_setting = _real_resolve
    sys.modules["gateway"] = fake_gateway
    sys.modules["gateway.display_config"] = fake_display

    monkeypatch.setenv("AAP_LOCALPART", "chris")
    monkeypatch.setenv("HOME", str(tmp_path))

    try:
        ctx = FakeCtx()
        register(ctx)

        patched = fake_display.resolve_display_setting

        # User has display.tool_progress = "all" globally (like the real
        # config that exposed the bug). The patched resolver must force
        # AAP to "off" anyway.
        user_config = {"display": {"tool_progress": "all"}}
        assert patched(user_config, "aap", "tool_progress") == "off"

        # Other platforms continue to inherit the global "all".
        assert patched(user_config, "telegram", "tool_progress") == "all"

        # Other settings on AAP delegate to the original resolver.
        assert patched({"display": {"show_reasoning": True}}, "aap", "show_reasoning") is True
    finally:
        sys.modules.pop("gateway.display_config", None)
        sys.modules.pop("gateway", None)


def test_register_honors_explicit_aap_tool_progress_override(monkeypatch, tmp_path):
    """If the operator has explicitly set
    display.platforms.aap.tool_progress (e.g. to "all" for debugging),
    don't override that — explicit AAP-scoped config beats our default."""
    import sys
    import types

    def _real_resolve(user_config, platform_key, setting, fallback=None):
        display_cfg = (user_config.get("display") if isinstance(user_config, dict) else None) or {}
        platforms = display_cfg.get("platforms") or {}
        plat_overrides = platforms.get(platform_key)
        if isinstance(plat_overrides, dict) and setting in plat_overrides:
            return plat_overrides[setting]
        if setting in display_cfg:
            return display_cfg[setting]
        return fallback

    fake_gateway = types.ModuleType("gateway")
    fake_display = types.ModuleType("gateway.display_config")
    fake_display.resolve_display_setting = _real_resolve
    sys.modules["gateway"] = fake_gateway
    sys.modules["gateway.display_config"] = fake_display

    monkeypatch.setenv("AAP_LOCALPART", "chris")
    monkeypatch.setenv("HOME", str(tmp_path))

    try:
        ctx = FakeCtx()
        register(ctx)

        patched = fake_display.resolve_display_setting
        user_config = {
            "display": {
                "tool_progress": "all",  # global
                "platforms": {"aap": {"tool_progress": "all"}},  # explicit AAP override
            }
        }
        assert patched(user_config, "aap", "tool_progress") == "all"
    finally:
        sys.modules.pop("gateway.display_config", None)
        sys.modules.pop("gateway", None)


def test_register_resolve_patch_idempotent(monkeypatch, tmp_path):
    """If register() runs twice (gateway restart, plugin reload), don't
    pile up nested wrappers — installing the patch once is enough."""
    import sys
    import types

    call_count = [0]
    def _real_resolve(user_config, platform_key, setting, fallback=None):
        call_count[0] += 1
        return fallback

    fake_gateway = types.ModuleType("gateway")
    fake_display = types.ModuleType("gateway.display_config")
    fake_display.resolve_display_setting = _real_resolve
    sys.modules["gateway"] = fake_gateway
    sys.modules["gateway.display_config"] = fake_display

    monkeypatch.setenv("AAP_LOCALPART", "chris")
    monkeypatch.setenv("HOME", str(tmp_path))

    try:
        register(FakeCtx())
        register(FakeCtx())  # second call must not double-wrap

        # Verify the patched function exists (with our marker)
        patched = fake_display.resolve_display_setting
        assert getattr(patched, "_aap_hermes_patched", False) is True

        # A non-AAP query should hit the original resolver exactly once
        # per call, not twice (which would happen if double-wrapped).
        call_count[0] = 0
        patched({"display": {}}, "telegram", "tool_progress")
        assert call_count[0] == 1
    finally:
        sys.modules.pop("gateway.display_config", None)
        sys.modules.pop("gateway", None)


def test_register_resolve_patch_skipped_without_hermes(monkeypatch, tmp_path):
    """register() must not raise if gateway.display_config is missing
    (older Hermes / unit-test environments)."""
    import sys
    sys.modules.pop("gateway.display_config", None)
    sys.modules.pop("gateway", None)

    monkeypatch.setenv("AAP_LOCALPART", "chris")
    monkeypatch.setenv("HOME", str(tmp_path))

    ctx = FakeCtx()
    register(ctx)  # must not raise

    assert "aap" in ctx.platforms


def test_adapter_factory_signature(monkeypatch, tmp_path):
    """Hermes's platform registry invokes adapter_factory(config) — one arg only."""
    monkeypatch.setenv("AAP_LOCALPART", "chris")
    monkeypatch.setenv("AAP_TRUST_LIST_PUBLIC_KEY_B64", _TRUST_ROOT)
    monkeypatch.setenv("HOME", str(tmp_path))

    ctx = FakeCtx()
    register(ctx)

    factory = ctx.platforms["aap"]["adapter_factory"]

    # Build a minimal PlatformConfig stand-in (just needs .extra)
    class FakeConfig:
        extra: dict = {}

    adapter = factory(FakeConfig())
    assert adapter is not None
    assert adapter.relay_url
