"""Transaction ingestion: the door through which a bank's payment events enter the engine.

A bank-side adapter posts standardised events. Each one is validated,
de-duplicated and queued; the live engine drains the queue on its next tick.

  idempotency   an event_id is accepted once; sending it again is reported as a duplicate
  replay        events older than a few minutes, or dated in the future, are refused
  ordering      queued events are applied in timestamp order
  statuses      only successful payments are scored; pending and failed ones are acknowledged;
                a reversal removes the payment it refers to
  back-pressure a full queue refuses new events instead of dropping old ones
  dead letters  refused events are kept, with the reason, for the sender to inspect
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone

import numpy as np

from . import config
from .synth import CH_ATM, CH_NEFT, CH_UPI

CHANNEL_CODE = {"UPI": CH_UPI, "IMPS": CH_UPI, "NEFT": CH_NEFT, "ATM": CH_ATM}
MAX_SKEW_SECONDS = 300      # how old or how far ahead an event's timestamp may be
QUEUE_LIMIT = 5000
REMEMBER_IDS = 100_000


class Ingestor:
    def __init__(self):
        self.lock = threading.Lock()
        self.queue: deque = deque()
        self.seen: OrderedDict[str, dict] = OrderedDict()    # event_id -> what became of it
        self.dead: dict[str, deque] = {b: deque(maxlen=50) for b in config.BANK_CODES}
        self.recent: dict[str, deque] = {b: deque(maxlen=30) for b in config.BANK_CODES}
        self.stats = {b: {"accepted": 0, "duplicate": 0, "rejected": 0, "ignored": 0, "reversed": 0}
                      for b in config.BANK_CODES}
        self._accounts: tuple | None = None

    def reset(self) -> None:
        with self.lock:
            self.queue.clear()
            self.seen.clear()
            for b in config.BANK_CODES:
                self.dead[b].clear()
                self.recent[b].clear()
                self.stats[b] = dict.fromkeys(self.stats[b], 0)

    # ------------------------------------------------------------------ lookup
    def _lookup(self, world) -> dict:
        """(bank code, account number) -> account index, rebuilt only when accounts are added."""
        if self._accounts is None or self._accounts[0] is not world.acct_no:
            table = {(config.BANK_CODES[b], a): i for i, (b, a) in enumerate(zip(world.bank.tolist(), world.acct_no.tolist()))}
            self._accounts = (world.acct_no, table)
        return self._accounts[1]

    # ------------------------------------------------------------------ submit
    def submit(self, bank: str, events: list[dict], world) -> list[dict]:
        """Validate and queue a batch from one bank. Returns one result per event, in order."""
        table = self._lookup(world)
        now = time.time()
        results = []
        with self.lock:
            for ev in events:
                results.append(self._one(bank, ev, table, now))
        return results

    def _refuse(self, bank: str, ev: dict, reason: str) -> dict:
        self.stats[bank]["rejected"] += 1
        self.dead[bank].appendleft({"event_id": ev["event_id"], "reason": reason,
                                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        return {"event_id": ev["event_id"], "result": "rejected", "reason": reason}

    def _one(self, bank: str, ev: dict, table: dict, now: float) -> dict:
        eid = ev["event_id"]
        if eid in self.seen:
            self.stats[bank]["duplicate"] += 1
            return {"event_id": eid, "result": "duplicate", "reason": "This event was already received."}
        if ev["sender_bank"] != bank:
            return self._refuse(bank, ev, "A bank may only report payments sent by its own customers.")
        age = now - ev["timestamp"].timestamp()
        if age > MAX_SKEW_SECONDS:
            return self._refuse(bank, ev, "The timestamp is too old. Possible replay.")
        if age < -MAX_SKEW_SECONDS:
            return self._refuse(bank, ev, "The timestamp is in the future.")
        src = table.get((ev["sender_bank"], ev["sender_account"]))
        if src is None:
            return self._refuse(bank, ev, "The sender account is not known.")
        cash = ev["channel"] == "ATM"
        dst = -1 if cash else table.get((ev["receiver_bank"], ev["receiver_account"]))
        if dst is None:
            return self._refuse(bank, ev, "The receiver account is not held at a member bank.")
        if dst == src:
            return self._refuse(bank, ev, "Sender and receiver are the same account.")

        record = {"bank": bank, "src": src, "dst": dst, "amt": float(round(ev["amount"])),
                  "at": ev["timestamp"].timestamp(), "ch": CHANNEL_CODE[ev["channel"]], "state": "queued", "ts": None}
        if ev["status"] in ("pending", "failed"):
            self.stats[bank]["ignored"] += 1
            self._remember(eid, {**record, "state": ev["status"]})
            return {"event_id": eid, "result": "acknowledged", "reason": f"A {ev['status']} payment is not scored."}
        if ev["status"] == "reversed":
            target = self.seen.get(ev.get("reverses") or "")
            if target is None or target["bank"] != bank or target["state"] not in ("queued", "applied"):
                return self._refuse(bank, ev, "The payment to reverse was not found.")
            target["state"] = "reversal-" + target["state"]     # the engine removes it on the next tick
            self.queue.append(("reverse", target))
            self.stats[bank]["reversed"] += 1
            self._remember(eid, {**record, "state": "reversal"})
            return {"event_id": eid, "result": "accepted", "reason": "The original payment will be removed."}
        if len(self.queue) >= QUEUE_LIMIT:
            return self._refuse(bank, ev, "The queue is full. Send this event again shortly.")
        self._remember(eid, record)
        self.queue.append(("add", record))
        self.stats[bank]["accepted"] += 1
        self.recent[bank].appendleft({"event_id": eid, "amount": record["amt"], "channel": ev["channel"],
                                      "at": ev["timestamp"].isoformat(timespec="seconds")})
        return {"event_id": eid, "result": "accepted", "reason": "Queued for scoring."}

    def _remember(self, eid: str, record: dict) -> None:
        self.seen[eid] = record
        if len(self.seen) > REMEMBER_IDS:
            self.seen.popitem(last=False)

    # ------------------------------------------------------------------ drain
    def drain(self, t1: float, t2: float) -> tuple[dict, list[dict]]:
        """Take everything queued. Returns new transactions for (t1, t2] and payments to remove."""
        with self.lock:
            items = list(self.queue)
            self.queue.clear()
        adds = [r for kind, r in items if kind == "add" and r["state"] == "queued"]
        adds.sort(key=lambda r: r["at"])                        # apply in event-time order
        stamps = np.linspace(t1, t2, len(adds) + 2)[1:-1] if adds else np.zeros(0)
        for r, ts in zip(adds, stamps):
            r["state"], r["ts"] = "applied", float(ts)
        new = {"src": np.array([r["src"] for r in adds], dtype=np.int64),
               "dst": np.array([r["dst"] for r in adds], dtype=np.int64),
               "amt": np.array([r["amt"] for r in adds], dtype=float), "ts": stamps.astype(float),
               "ch": np.array([r["ch"] for r in adds], dtype=np.int8)}
        removals = [r for kind, r in items if kind == "reverse" and r["state"] == "reversal-applied" and r["ts"] is not None]
        return new, removals

    def status(self, bank: str | None) -> dict:
        with self.lock:
            banks = config.BANK_CODES if bank is None else [bank]
            totals = {k: sum(self.stats[b][k] for b in banks) for k in self.stats[config.BANK_CODES[0]]}
            out = {"queued": len(self.queue), "totals": totals,
                   "by_bank": {b: dict(self.stats[b]) for b in banks}}
            if bank is not None:   # the detail of a bank's own events stays with that bank
                out["recent"] = list(self.recent[bank])
                out["dead_letters"] = list(self.dead[bank])
            return out
