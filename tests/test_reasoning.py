"""Unit tests for aap_hermes.reasoning.ReasoningScheduler."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, List

import pytest

from aap_hermes.reasoning import ReasoningScheduler


@dataclass
class _Tick:
    session_key: str


def _tick_factory(rsess) -> _Tick:
    return _Tick(session_key=rsess.session_key)


@pytest.mark.asyncio
async def test_signal_triggers_one_turn():
    fired: List[_Tick] = []

    async def handler(event):
        fired.append(event)
        return None

    sched = ReasoningScheduler(handler, _tick_factory)
    await sched.signal("sess-A", source="src")
    await asyncio.sleep(0.05)
    assert len(fired) == 1
    assert fired[0].session_key == "sess-A"
    await sched.close()


@pytest.mark.asyncio
async def test_clustered_signals_coalesce():
    """Multiple signals during a slow turn collapse to one extra turn."""
    fired: List[_Tick] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_handler(event):
        fired.append(event)
        started.set()
        await release.wait()
        return None

    sched = ReasoningScheduler(slow_handler, _tick_factory)
    await sched.signal("sess-A", source="src")
    await started.wait()

    # While turn 1 is in flight, fire several more signals.
    for _ in range(5):
        await sched.signal("sess-A", source="src")

    release.set()
    await asyncio.sleep(0.05)
    await sched.close()
    # Exactly two turns: the original + one coalesced replay.
    assert len(fired) == 2


@pytest.mark.asyncio
async def test_separate_sessions_run_independently():
    fired: List[str] = []
    barrier = asyncio.Event()

    async def handler(event):
        fired.append(event.session_key)
        await barrier.wait()
        return None

    sched = ReasoningScheduler(handler, _tick_factory)
    await sched.signal("sess-A", source="src")
    await sched.signal("sess-B", source="src")
    await asyncio.sleep(0.02)
    # Both sessions should have started their handler concurrently.
    assert sorted(fired) == ["sess-A", "sess-B"]
    barrier.set()
    await asyncio.sleep(0.02)
    await sched.close()


@pytest.mark.asyncio
async def test_handler_exception_does_not_kill_loop():
    fired: List[int] = []

    async def flaky_handler(event):
        fired.append(len(fired))
        if len(fired) == 1:
            raise RuntimeError("boom")
        return None

    sched = ReasoningScheduler(flaky_handler, _tick_factory)
    await sched.signal("sess-A", source="src")
    await asyncio.sleep(0.02)
    await sched.signal("sess-A", source="src")
    await asyncio.sleep(0.05)
    await sched.close()
    assert len(fired) >= 2


@pytest.mark.asyncio
async def test_close_cancels_running_tasks():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(event):
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    sched = ReasoningScheduler(handler, _tick_factory)
    await sched.signal("sess-A", source="src")
    await started.wait()
    await sched.close()
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_signal_after_close_is_noop():
    fired: List[Any] = []

    async def handler(event):
        fired.append(event)
        return None

    sched = ReasoningScheduler(handler, _tick_factory)
    await sched.close()
    await sched.signal("sess-A", source="src")
    await asyncio.sleep(0.02)
    assert fired == []


@pytest.mark.asyncio
async def test_origin_passed_through_to_tick_factory():
    captured = {}

    def tick_with_origin(rsess):
        captured["origin"] = dict(rsess.last_origin)
        return _Tick(session_key=rsess.session_key)

    async def handler(event):
        return None

    sched = ReasoningScheduler(handler, tick_with_origin)
    await sched.signal(
        "sess-A", source="src",
        origin={"sender": "peer^x", "conv_id": "c-1"},
    )
    await asyncio.sleep(0.02)
    await sched.close()
    assert captured["origin"] == {"sender": "peer^x", "conv_id": "c-1"}
