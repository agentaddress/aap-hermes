"""Tests for the conversations store."""

import pytest

from aap.conversations import Conversation, ConversationStore, broadcast_to_conversation


@pytest.fixture
def hermes_home(tmp_path):
    return tmp_path


def test_empty_store(hermes_home):
    store = ConversationStore.load(hermes_home)
    assert store.list_active() == []


def test_record_conversation(hermes_home):
    store = ConversationStore.load(hermes_home)
    conv = Conversation(
        conversation_id="dinner-abc",
        purpose="Plan dinner",
        members=["chris^x.com", "james^y.com"],
        convener="chris^x.com",
        accepted_at="2026-05-22T12:00:00Z",
        last_message_at=None,
    )
    store.record(conv)
    assert (hermes_home / "aap-conversations.json").exists()

    reloaded = ConversationStore.load(hermes_home)
    assert len(reloaded.list_active()) == 1
    assert reloaded.get("dinner-abc").purpose == "Plan dinner"


def test_update_members(hermes_home):
    store = ConversationStore.load(hermes_home)
    store.record(Conversation(
        conversation_id="x",
        purpose="t",
        members=["chris^x.com", "james^y.com"],
        convener="chris^x.com",
        accepted_at="2026-05-22T12:00:00Z",
        last_message_at=None,
    ))
    store.update_members("x", ["chris^x.com", "james^y.com", "alice^z.com"])
    reloaded = ConversationStore.load(hermes_home)
    assert len(reloaded.get("x").members) == 3


def test_remove_member(hermes_home):
    store = ConversationStore.load(hermes_home)
    store.record(Conversation(
        conversation_id="x",
        purpose="t",
        members=["chris^x.com", "james^y.com", "mike^z.com"],
        convener="chris^x.com",
        accepted_at="2026-05-22T12:00:00Z",
        last_message_at=None,
    ))
    store.remove_member("x", "mike^z.com")
    reloaded = ConversationStore.load(hermes_home)
    assert "mike^z.com" not in reloaded.get("x").members


def test_dissolve_conversation(hermes_home):
    store = ConversationStore.load(hermes_home)
    store.record(Conversation(
        conversation_id="x",
        purpose="t",
        members=["chris^x.com", "james^y.com"],
        convener="chris^x.com",
        accepted_at="2026-05-22T12:00:00Z",
        last_message_at=None,
    ))
    store.dissolve("x")
    assert ConversationStore.load(hermes_home).get("x") is None


def test_other_members_excludes_self(hermes_home):
    store = ConversationStore.load(hermes_home)
    store.record(Conversation(
        conversation_id="x",
        purpose="t",
        members=["chris^x.com", "james^y.com", "sarah^z.com"],
        convener="chris^x.com",
        accepted_at="2026-05-22T12:00:00Z",
        last_message_at=None,
    ))
    others = ConversationStore.load(hermes_home).other_members("x", self_address="chris^x.com")
    assert set(others) == {"james^y.com", "sarah^z.com"}


# ── broadcast_to_conversation (v0.6: no capability tokens) ────────────────


@pytest.mark.asyncio
async def test_broadcast_sends_to_each_other_member(hermes_home):
    """broadcast_to_conversation sends one envelope per other member with
    conversation_id + members. No capability_token is attached — group
    chat is authorized by recipient-side membership check."""
    members = [
        "chris^x.com",
        "james^y.com",
        "sarah^z.com",
    ]
    store = ConversationStore.load(hermes_home)
    store.record(Conversation(
        conversation_id="dinner-abc",
        purpose="Plan dinner",
        members=members,
        convener="chris^x.com",
        accepted_at="2026-05-22T12:00:00Z",
        last_message_at=None,
    ))

    class FakeClient:
        def __init__(self):
            self.calls = []

        async def send_envelope(self, **kwargs):
            self.calls.append(kwargs)
            return 100 + len(self.calls)

    client = FakeClient()
    results = await broadcast_to_conversation(
        client=client,
        store=store,
        self_address="chris^x.com",
        conversation_id="dinner-abc",
        text="dinner at 7?",
    )

    assert len(client.calls) == 2
    recipients = {c["to"] for c in client.calls}
    assert recipients == {"james^y.com", "sarah^z.com"}
    for c in client.calls:
        assert c["text"] == "dinner at 7?"
        assert c["conversation_id"] == "dinner-abc"
        assert c["conversation_members"] == members
        # No capability_token kwarg under v0.6.
        assert "capability_token" not in c

    assert {r[0] for r in results} == {"james^y.com", "sarah^z.com"}
    assert all(isinstance(r[1], int) for r in results)


@pytest.mark.asyncio
async def test_broadcast_continues_after_send_error(hermes_home):
    """A failed send to one recipient must NOT prevent sends to others."""
    store = ConversationStore.load(hermes_home)
    store.record(Conversation(
        conversation_id="conv1",
        purpose="t",
        members=[
            "me^x.com",
            "flaky^y.com",
            "happy^z.com",
        ],
        convener="me^x.com",
        accepted_at="2026-05-22T12:00:00Z",
        last_message_at=None,
    ))

    class FakeClient:
        def __init__(self):
            self.calls = []

        async def send_envelope(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["to"] == "flaky^y.com":
                raise RuntimeError("network error")
            return 42

    client = FakeClient()
    results = await broadcast_to_conversation(
        client=client,
        store=store,
        self_address="me^x.com",
        conversation_id="conv1",
        text="hi",
    )
    assert len(client.calls) == 2
    result_dict = dict(results)
    assert result_dict["happy^z.com"] == 42
    assert "error" in str(result_dict["flaky^y.com"]).lower()


@pytest.mark.asyncio
async def test_broadcast_unknown_conversation_raises(hermes_home):
    store = ConversationStore.load(hermes_home)

    class FakeClient:
        async def send_envelope(self, **kwargs):
            return 1

    with pytest.raises(ValueError, match="unknown conversation"):
        await broadcast_to_conversation(
            client=FakeClient(),
            store=store,
            self_address="me^x.com",
            conversation_id="does-not-exist",
            text="hi",
        )
