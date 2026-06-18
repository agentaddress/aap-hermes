"""Tests for the home-channel mirror helper."""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from aap_hermes.mirror import (
    mirror_group_inbound_to_home_session,
    mirror_home_reply_to_group_session,
    mirror_to_home_channels,
)


def _fake_config(platforms: dict):
    """Build a stand-in gateway config with the given platforms dict."""
    config = MagicMock()
    config.platforms = platforms
    return config


def _fake_platform(*, enabled: bool, home_channel) -> MagicMock:
    p = MagicMock()
    p.enabled = enabled
    p.home_channel = home_channel
    return p


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    monkeypatch.delenv("AAP_MIRROR", raising=False)


def test_inbound_mirror_calls_send_for_each_enabled_home_channel():
    """Inbound notification posts to every platform with home_channel set."""
    telegram = MagicMock(value="telegram")
    discord = MagicMock(value="discord")
    slack = MagicMock(value="slack")
    irc = MagicMock(value="irc")
    fake_platforms = {
        telegram: _fake_platform(enabled=True, home_channel="chat-1"),
        discord: _fake_platform(enabled=True, home_channel="chat-2"),
        slack: _fake_platform(enabled=True, home_channel=None),
        irc: _fake_platform(enabled=False, home_channel="chat-3"),
    }

    handle_send = MagicMock()
    with patch("aap_hermes.mirror.load_gateway_config",
               return_value=_fake_config(fake_platforms)), \
         patch("aap_hermes.mirror._handle_send", handle_send):
        mirror_to_home_channels(
            sender="john^x",
            recipient=None,
            text="hi",
            direction="inbound",
        )

    targets = [call.args[0]["target"] for call in handle_send.call_args_list]
    assert sorted(targets) == ["discord", "telegram"]
    bodies = [call.args[0]["message"] for call in handle_send.call_args_list]
    for body in bodies:
        assert "\U0001f4e8 AAP from john^x" in body
        assert "hi" in body


def test_outbound_mirror_uses_arrow_and_recipient():
    telegram = MagicMock(value="telegram")
    fake_platforms = {
        telegram: _fake_platform(enabled=True, home_channel="chat-1"),
    }
    handle_send = MagicMock()
    with patch("aap_hermes.mirror.load_gateway_config",
               return_value=_fake_config(fake_platforms)), \
         patch("aap_hermes.mirror._handle_send", handle_send):
        mirror_to_home_channels(
            sender=None,
            recipient="james^y",
            text="reply",
            direction="outbound",
        )

    assert handle_send.call_count == 1
    body = handle_send.call_args.args[0]["message"]
    assert "\U0001f4e4 You sent to james^y" in body
    assert "reply" in body


def test_aap_mirror_off_suppresses_calls(monkeypatch):
    monkeypatch.setenv("AAP_MIRROR", "off")
    handle_send = MagicMock()
    with patch("aap_hermes.mirror.load_gateway_config") as load_cfg, \
         patch("aap_hermes.mirror._handle_send", handle_send):
        mirror_to_home_channels(
            sender="x", recipient=None, text="t", direction="inbound",
        )
    handle_send.assert_not_called()
    load_cfg.assert_not_called()  # short-circuit before loading config


def test_thread_id_appears_in_mirror_body():
    telegram = MagicMock(value="telegram")
    fake_platforms = {
        telegram: _fake_platform(enabled=True, home_channel="chat-1"),
    }
    handle_send = MagicMock()
    with patch("aap_hermes.mirror.load_gateway_config",
               return_value=_fake_config(fake_platforms)), \
         patch("aap_hermes.mirror._handle_send", handle_send):
        mirror_to_home_channels(
            sender="john^x",
            recipient=None,
            text="hi",
            direction="inbound",
            thread_id="dinner-plan",
        )

    body = handle_send.call_args.args[0]["message"]
    assert "(thread: dinner-plan)" in body


def test_one_platform_failure_does_not_block_others():
    telegram = MagicMock(value="telegram")
    discord = MagicMock(value="discord")
    fake_platforms = {
        telegram: _fake_platform(enabled=True, home_channel="chat-1"),
        discord: _fake_platform(enabled=True, home_channel="chat-2"),
    }
    handle_send = MagicMock(side_effect=[RuntimeError("boom"), "ok"])
    with patch("aap_hermes.mirror.load_gateway_config",
               return_value=_fake_config(fake_platforms)), \
         patch("aap_hermes.mirror._handle_send", handle_send):
        # Should not raise even though telegram raised
        mirror_to_home_channels(
            sender="x", recipient=None, text="t", direction="inbound",
        )
    assert handle_send.call_count == 2


def test_group_inbound_injection_uses_home_channel_chat_id(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    telegram = MagicMock(value="telegram")
    home = SimpleNamespace(chat_id="7969749825")
    fake_platforms = {
        telegram: _fake_platform(enabled=True, home_channel=home),
    }

    class FakeSessionSource:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    fake_store = MagicMock()
    fake_store.get_or_create_session.return_value = SimpleNamespace(session_id="home-session")
    fake_runner = SimpleNamespace(session_store=fake_store)

    run_mod = ModuleType("gateway.run")
    run_mod._gateway_runner_ref = lambda: fake_runner
    base_mod = ModuleType("gateway.platforms.base")
    base_mod.SessionSource = FakeSessionSource
    base_mod.Platform = lambda value: SimpleNamespace(value=value)
    monkeypatch.setitem(sys.modules, "gateway.run", run_mod)
    monkeypatch.setitem(sys.modules, "gateway.platforms.base", base_mod)

    with patch("aap_hermes.mirror.load_gateway_config",
               return_value=_fake_config(fake_platforms)):
        mirror_group_inbound_to_home_session(
            group_label="Dinner Planning",
            sender="hermes3^example.com",
            text="Can you do Wednesday?",
            conversation_id="conv-123",
        )

    source = fake_store.get_or_create_session.call_args.args[0]
    assert source.chat_id == "7969749825"
    assert source.user_id == "7969749825"


def test_home_reply_bridge_queues_user_evidence_to_group_session(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    telegram = MagicMock(value="telegram")
    home = SimpleNamespace(chat_id="7969749825")
    fake_platforms = {
        telegram: _fake_platform(enabled=True, home_channel=home),
    }

    class FakeSessionSource:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    fake_store = MagicMock()
    fake_store.get_or_create_session.return_value = SimpleNamespace(session_id="group-session")
    fake_runner = SimpleNamespace(session_store=fake_store)

    run_mod = ModuleType("gateway.run")
    run_mod._gateway_runner_ref = lambda: fake_runner
    base_mod = ModuleType("gateway.platforms.base")
    base_mod.SessionSource = FakeSessionSource
    base_mod.Platform = lambda value: SimpleNamespace(value=value)
    monkeypatch.setitem(sys.modules, "gateway.run", run_mod)
    monkeypatch.setitem(sys.modules, "gateway.platforms.base", base_mod)

    with patch("aap_hermes.mirror.load_gateway_config",
               return_value=_fake_config(fake_platforms)):
        mirror_group_inbound_to_home_session(
            group_label="Dinner Planning",
            sender="hermes3^example.com",
            text="Can you do Wednesday?",
            conversation_id="conv-123",
        )

    fake_store.reset_mock()

    from aap_hermes import _runtime

    fake_adapter = SimpleNamespace(enqueue_group_home_reply=MagicMock(return_value=True))
    monkeypatch.setattr(_runtime, "_adapter", fake_adapter)

    event = SimpleNamespace(
        text="Wednesday 8pm works for me",
        source=SimpleNamespace(platform=telegram, chat_id="7969749825"),
    )

    assert mirror_home_reply_to_group_session(event) is True

    fake_adapter.enqueue_group_home_reply.assert_called_once_with(
        conversation_id="conv-123",
        group_label="Dinner Planning",
        text="Wednesday 8pm works for me",
    )
    fake_store.get_or_create_session.assert_not_called()
    fake_store.append_to_transcript.assert_not_called()


def test_mirror_to_home_channels_emits_user_view_on_outbound(
    monkeypatch, tmp_path
):
    """Outbound mirror should log a named user_view event with audience=user."""
    import json
    from aap_hermes import scenario_log

    monkeypatch.setenv("HERMES_SCENARIO_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SCENARIO_RUN_ID", "mirror-test")
    monkeypatch.setenv("HERMES_PROFILE", "hermes9")
    monkeypatch.delenv("AAP_MIRROR", raising=False)
    scenario_log._reset_for_tests()

    # Stub the gateway integration so we isolate the log emit. We don't
    # care whether the home-channel notification fires — we only care
    # that the scenario_log event is written.
    with patch("aap_hermes.mirror.load_gateway_config",
               return_value=_fake_config({})):
        mirror_to_home_channels(
            sender=None,
            recipient="b^example",
            text="Hi Ian, when are you free?",
            direction="outbound",
        )

    lines = (tmp_path / "hermes9.jsonl").read_text().strip().splitlines()
    events = [
        json.loads(line) for line in lines
        if json.loads(line)["kind"] == "user_view"
    ]
    assert len(events) == 1, f"expected 1 user_view event, got {len(events)}: {events}"
    e = events[0]
    assert e["audience"] == "user"
    assert e["layer"] == "named"
    assert e["data"]["text"] == "Hi Ian, when are you free?"
    assert e["data"]["direction"] == "outbound"
    assert e["data"]["peer"] == "b^example"


def test_mirror_to_home_channels_emits_user_input_on_inbound(
    monkeypatch, tmp_path
):
    """Inbound mirror should log a named user_input event."""
    import json
    from aap_hermes import scenario_log

    monkeypatch.setenv("HERMES_SCENARIO_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SCENARIO_RUN_ID", "mirror-test")
    monkeypatch.setenv("HERMES_PROFILE", "hermes9")
    monkeypatch.delenv("AAP_MIRROR", raising=False)
    scenario_log._reset_for_tests()

    with patch("aap_hermes.mirror.load_gateway_config",
               return_value=_fake_config({})):
        mirror_to_home_channels(
            sender="b^example",
            recipient=None,
            text="Sounds good",
            direction="inbound",
        )

    lines = (tmp_path / "hermes9.jsonl").read_text().strip().splitlines()
    events = [
        json.loads(line) for line in lines
        if json.loads(line)["kind"] == "user_input"
    ]
    assert len(events) == 1
    e = events[0]
    assert e["audience"] == "user"
    assert e["layer"] == "named"
    assert e["data"]["text"] == "Sounds good"
    assert e["data"]["direction"] == "inbound"
    assert e["data"]["peer"] == "b^example"


def test_mirror_to_home_channels_no_emit_when_address_missing(
    monkeypatch, tmp_path
):
    """When both sender and recipient resolve to None for the direction, no event is emitted."""
    import json
    from aap_hermes import scenario_log

    monkeypatch.setenv("HERMES_SCENARIO_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SCENARIO_RUN_ID", "mirror-test")
    monkeypatch.setenv("HERMES_PROFILE", "hermes9")
    monkeypatch.delenv("AAP_MIRROR", raising=False)
    scenario_log._reset_for_tests()

    with patch("aap_hermes.mirror.load_gateway_config",
               return_value=_fake_config({})):
        # Outbound with no recipient → address is None → early return.
        mirror_to_home_channels(
            sender="a^example",
            recipient=None,
            text="anything",
            direction="outbound",
        )

    log_file = tmp_path / "hermes9.jsonl"
    if log_file.exists():
        events = [
            json.loads(line)
            for line in log_file.read_text().strip().splitlines()
            if json.loads(line)["kind"] in ("user_view", "user_input")
        ]
        assert events == [], f"unexpected events: {events}"
