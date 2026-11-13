"""SQLite persistence for the audit trail and case decisions.

The audit trail is tamper-evident: every entry stores a SHA-256 hash of its
own content plus the previous entry's hash. Editing or deleting any past entry
breaks the chain from that point on, and `verify()` reports where.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from datetime import datetime, timezone

GENESIS = "0" * 64
FIELDS = ("id", "ts", "actor", "bank", "action", "token", "detail", "note", "prev_hash", "hash")
CASE_FIELDS = ("id", "token", "bank", "title", "priority", "assignee", "state", "created_by", "created_at",
               "updated_at", "version")
NOTE_FIELDS = ("id", "case_id", "author", "body", "created_at", "updated_at")


def _digest(prev: str, ts: str, actor: str, bank: str, action: str, token: str, detail: str, note: str) -> str:
    material = "\x1f".join((prev, ts, actor, bank, action, token, detail, note))
    return hashlib.sha256(material.encode()).hexdigest()


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS audit_log(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL,
                    bank TEXT NOT NULL, action TEXT NOT NULL, token TEXT NOT NULL, detail TEXT NOT NULL,
                    note TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cases(
                    token TEXT PRIMARY KEY, status TEXT, actor TEXT, updated TEXT);
                CREATE TABLE IF NOT EXISTS case_files(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT NOT NULL UNIQUE, bank TEXT NOT NULL,
                    title TEXT NOT NULL, priority TEXT NOT NULL, assignee TEXT NOT NULL, state TEXT NOT NULL,
                    created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS case_notes(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL, author TEXT NOT NULL,
                    body TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES case_files(id) ON DELETE CASCADE);
                CREATE INDEX IF NOT EXISTS case_notes_case ON case_notes(case_id);
            """)
            self.db.execute("PRAGMA foreign_keys = ON")
            self.db.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def ping(self) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1").fetchone() == (1,)

    def backup(self, dest: str) -> int:
        """Write a consistent copy of the database to `dest` while it stays in use. Returns its size."""
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        target = sqlite3.connect(dest)
        try:
            with self.lock:
                self.db.backup(target)
        finally:
            target.close()
        return os.path.getsize(dest)

    def close(self) -> None:
        with self.lock:
            self.db.commit()
            self.db.close()

    def log(self, actor: str, action: str, token: str = "", detail: str = "", bank: str = "", note: str = "") -> None:
        """Append an entry. `bank` is the bank the entry belongs to, empty for system events."""
        with self.lock:
            row = self.db.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
            prev = row[0] if row else GENESIS
            ts = self._now()
            h = _digest(prev, ts, actor, bank, action, token, detail, note)
            self.db.execute(
                "INSERT INTO audit_log(ts, actor, bank, action, token, detail, note, prev_hash, hash) "
                "VALUES (?,?,?,?,?,?,?,?,?)", (ts, actor, bank, action, token, detail, note, prev, h))
            self.db.commit()

    def audit(self, limit: int = 200, token: str | None = None) -> list[dict]:
        q, args = f"SELECT {', '.join(FIELDS)} FROM audit_log", []
        if token:
            q, args = q + " WHERE token = ?", [token]
        with self.lock:
            rows = self.db.execute(q + " ORDER BY id DESC LIMIT ?", args + [limit]).fetchall()
        return [dict(zip(FIELDS, r)) for r in rows]

    def verify(self) -> dict:
        """Recompute the whole chain. Returns whether it is intact and where it first breaks."""
        with self.lock:
            rows = self.db.execute(f"SELECT {', '.join(FIELDS)} FROM audit_log ORDER BY id").fetchall()
        prev = GENESIS
        for r in rows:
            e = dict(zip(FIELDS, r))
            expect = _digest(prev, e["ts"], e["actor"], e["bank"], e["action"], e["token"], e["detail"], e["note"])
            if e["prev_hash"] != prev or e["hash"] != expect:
                return {"ok": False, "entries": len(rows), "broken_at": e["id"]}
            prev = e["hash"]
        return {"ok": True, "entries": len(rows), "broken_at": None, "head": prev[:16]}

    def set_status(self, token: str, status: str | None, actor: str) -> None:
        with self.lock:
            if status is None:
                self.db.execute("DELETE FROM cases WHERE token = ?", (token,))
            else:
                self.db.execute("INSERT OR REPLACE INTO cases(token, status, actor, updated) VALUES (?,?,?,?)",
                                (token, status, actor, self._now()))
            self.db.commit()

    def statuses(self) -> dict[str, str]:
        with self.lock:
            return dict(self.db.execute("SELECT token, status FROM cases").fetchall())

    def clear_cases(self) -> None:
        with self.lock:
            self.db.execute("DELETE FROM cases")
            self.db.execute("DELETE FROM case_notes")
            self.db.execute("DELETE FROM case_files")
            self.db.commit()

    # ------------------------------------------------------------------ case files
    def _case(self, where: str, args: tuple) -> dict | None:
        row = self.db.execute(
            f"SELECT {', '.join(CASE_FIELDS)}, (SELECT COUNT(*) FROM case_notes n WHERE n.case_id = c.id) "
            f"FROM case_files c WHERE {where}", args).fetchone()
        return dict(zip(CASE_FIELDS + ("note_count",), row)) if row else None

    def case_create(self, token: str, bank: str, title: str, priority: str, assignee: str, actor: str) -> dict | None:
        """Create a case. Returns None if the account already has one."""
        now = self._now()
        with self.lock:
            try:
                cur = self.db.execute(
                    "INSERT INTO case_files(token, bank, title, priority, assignee, state, created_by, created_at, "
                    "updated_at) VALUES (?,?,?,?,?,'open',?,?,?)", (token, bank, title, priority, assignee, actor, now, now))
            except sqlite3.IntegrityError:
                return None
            self.db.commit()
            return self._case("c.id = ?", (cur.lastrowid,))

    def case_get(self, case_id: int) -> dict | None:
        with self.lock:
            return self._case("c.id = ?", (case_id,))

    def case_for(self, token: str) -> dict | None:
        with self.lock:
            return self._case("c.token = ?", (token,))

    def case_list(self, bank: str | None = None) -> list[dict]:
        q = (f"SELECT {', '.join(CASE_FIELDS)}, (SELECT COUNT(*) FROM case_notes n WHERE n.case_id = c.id) "
             f"FROM case_files c")
        args: tuple = ()
        if bank is not None:
            q, args = q + " WHERE c.bank = ?", (bank,)
        with self.lock:
            rows = self.db.execute(q + " ORDER BY c.state DESC, c.updated_at DESC, c.id DESC", args).fetchall()
        return [dict(zip(CASE_FIELDS + ("note_count",), r)) for r in rows]

    def case_update(self, case_id: int, fields: dict, version: int) -> dict | None:
        """Apply changes if `version` is still current. Returns None on a version conflict."""
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.lock:
            cur = self.db.execute(
                f"UPDATE case_files SET {sets}, updated_at = ?, version = version + 1 WHERE id = ? AND version = ?",
                (*fields.values(), self._now(), case_id, version))
            self.db.commit()
            return self._case("c.id = ?", (case_id,)) if cur.rowcount else None

    def case_touch(self, case_id: int) -> None:
        with self.lock:
            self.db.execute("UPDATE case_files SET updated_at = ? WHERE id = ?", (self._now(), case_id))
            self.db.commit()

    def case_delete(self, case_id: int) -> bool:
        with self.lock:
            self.db.execute("DELETE FROM case_notes WHERE case_id = ?", (case_id,))
            cur = self.db.execute("DELETE FROM case_files WHERE id = ?", (case_id,))
            self.db.commit()
            return bool(cur.rowcount)

    # ------------------------------------------------------------------ notes
    def note_list(self, case_id: int) -> list[dict]:
        with self.lock:
            rows = self.db.execute(f"SELECT {', '.join(NOTE_FIELDS)} FROM case_notes WHERE case_id = ? ORDER BY id",
                                   (case_id,)).fetchall()
        return [dict(zip(NOTE_FIELDS, r)) for r in rows]

    def note_add(self, case_id: int, author: str, body: str) -> dict:
        now = self._now()
        with self.lock:
            cur = self.db.execute("INSERT INTO case_notes(case_id, author, body, created_at, updated_at) "
                                  "VALUES (?,?,?,?,?)", (case_id, author, body, now, now))
            self.db.commit()
            return dict(zip(NOTE_FIELDS, (cur.lastrowid, case_id, author, body, now, now)))

    def note_update(self, case_id: int, note_id: int, body: str) -> dict | None:
        with self.lock:
            cur = self.db.execute("UPDATE case_notes SET body = ?, updated_at = ? WHERE id = ? AND case_id = ?",
                                  (body, self._now(), note_id, case_id))
            self.db.commit()
            if not cur.rowcount:
                return None
            row = self.db.execute(f"SELECT {', '.join(NOTE_FIELDS)} FROM case_notes WHERE id = ?", (note_id,)).fetchone()
        return dict(zip(NOTE_FIELDS, row))

    def note_delete(self, case_id: int, note_id: int) -> bool:
        with self.lock:
            cur = self.db.execute("DELETE FROM case_notes WHERE id = ? AND case_id = ?", (note_id, case_id))
            self.db.commit()
            return bool(cur.rowcount)
