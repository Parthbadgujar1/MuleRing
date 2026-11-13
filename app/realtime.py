"""Server-pushed events: an in-process bus and the live stream that feeds it.

Every event is rendered once per role before it is published, so a subscriber
only ever receives what that role is allowed to see.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import threading
from collections import deque
from datetime import datetime, timezone

log = logging.getLogger("mulering.realtime")


class BusFull(Exception):
    pass


class Bus:
    def __init__(self, roles: list[str], max_clients: int = 60, history: int = 60):
        self.roles = roles
        self.max_clients = max_clients
        self.clients: dict[int, tuple[str, asyncio.Queue]] = {}
        self.recent = {r: deque(maxlen=history) for r in roles}
        self.loop: asyncio.AbstractEventLoop | None = None
        self._ids = itertools.count(1)
        self._event_ids = itertools.count(1)

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    @property
    def count(self) -> int:
        return len(self.clients)

    def subscribe(self, role: str) -> tuple[int, asyncio.Queue]:
        if len(self.clients) >= self.max_clients:
            raise BusFull()
        cid = next(self._ids)
        self.clients[cid] = (role, asyncio.Queue(maxsize=200))
        return cid, self.clients[cid][1]

    def unsubscribe(self, cid: int) -> None:
        self.clients.pop(cid, None)

    def publish(self, by_role: dict[str, dict]) -> None:
        """Thread-safe. `by_role` maps a role to the payload that role may see."""
        stamp = {"id": next(self._event_ids),
                 "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        out = {}
        for role, payload in by_role.items():
            if payload is None:
                continue
            out[role] = {**stamp, **payload}
            if payload.get("type") not in ("tick", "attack"):
                self.recent[role].appendleft(out[role])
        if out and self.loop is not None and self.clients:
            self.loop.call_soon_threadsafe(self._fanout, out)

    def _fanout(self, by_role: dict[str, dict]) -> None:
        for role, queue in list(self.clients.values()):
            payload = by_role.get(role)
            if payload is None:
                continue
            if queue.full():  # a stalled client loses its oldest event, never blocks the others
                queue.get_nowait()
            queue.put_nowait(payload)

    def clear(self) -> None:
        for d in self.recent.values():
            d.clear()


class LiveStream:
    """Advances the simulation clock on a timer while someone is watching."""

    def __init__(self, step, should_run, tick_seconds: float = 2.0):
        self.step, self.should_run, self.tick_seconds = step, should_run, tick_seconds
        self.running = True
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="live-stream", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.wait(self.tick_seconds):
            if not (self.running and self.should_run()):
                continue
            try:
                self.step()
            except Exception:  # one bad tick must not kill the stream
                log.exception("live tick failed")
