"""SQLite-backed persistent store for cadence beats."""

from __future__ import annotations

import secrets
import sqlite3
import threading
from pathlib import Path
from typing import cast

from ..protocols import Clock
from .enums import Kind, OccurrenceState, Reason, Scope, Status
from .model import Beat, Session
from .patch import BeatPatch

SCHEMA = """
CREATE TABLE IF NOT EXISTS beats (
    id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    note TEXT NOT NULL,
    kind TEXT NOT NULL,
    next_at REAL,
    interval_s REAL,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_fired_at REAL,
    fires INTEGER NOT NULL DEFAULT 0,
    max_fires INTEGER,
    expires_at REAL,
    last_error TEXT NOT NULL DEFAULT '',
    error_count INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    scope TEXT NOT NULL DEFAULT 'chat',
    anchor_session_id TEXT,
    noop_streak INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS beats_due ON beats(status, next_at);
CREATE INDEX IF NOT EXISTS beats_session ON beats(session_key, created_at);
CREATE TABLE IF NOT EXISTS occurrences (
    beat_id TEXT NOT NULL,
    due_key TEXT NOT NULL,
    state TEXT NOT NULL,
    claimed_by TEXT,
    claimed_at REAL,
    finished_at REAL,
    error TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (beat_id, due_key)
);
CREATE TABLE IF NOT EXISTS sessions (
    session_key TEXT PRIMARY KEY,
    current_session_id TEXT,
    platform TEXT,
    chat_id TEXT,
    last_human_at REAL,
    human_session_id TEXT,
    last_beat_at REAL,
    unanswered_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ledger (
    day TEXT NOT NULL,
    session_key TEXT NOT NULL,
    messages INTEGER NOT NULL DEFAULT 0,
    fires INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, session_key)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _opt_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _float(value: object, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) else default


def _opt_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _int(value: object, default: int = 0) -> int:
    return int(value) if isinstance(value, int) else default


def _opt_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None


def _row_values(row: sqlite3.Row) -> dict[str, object]:
    """``sqlite3.Row`` values are ``Any``; this is the only place that leaks in."""
    return {key: cast(object, row[key]) for key in row.keys()}  # noqa: SIM118 - Row iterates values


class Store:
    def __init__(self, path: str | Path, clock: Clock) -> None:
        self.path = Path(path)
        self.clock = clock
        self._closed = False
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._ensure_column("beats", "scope", "TEXT NOT NULL DEFAULT 'chat'")
        self._ensure_column("beats", "anchor_session_id", "TEXT")
        self._ensure_column("beats", "noop_streak", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("sessions", "human_session_id", "TEXT")
        self._ensure_column("sessions", "unanswered_count", "INTEGER NOT NULL DEFAULT 0")

    def _fetch_one(self, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Row | None:
        return cast("sqlite3.Row | None", self.conn.execute(sql, params).fetchone())

    def _fetch_all(self, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        return cast("list[sqlite3.Row]", self.conn.execute(sql, params).fetchall())

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        rows = self._fetch_all(f"PRAGMA table_info({table})")
        if column not in {_text(_row_values(row).get("name")) for row in rows}:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            self.conn.commit()

    def _beat(self, row: sqlite3.Row | None) -> Beat | None:
        if row is None:
            return None
        data = _row_values(row)
        return Beat(
            id=_text(data["id"]),
            session_key=_text(data["session_key"]),
            note=_text(data["note"]),
            kind=Kind(_text(data["kind"])),
            next_at=_opt_float(data["next_at"]),
            interval_s=_opt_float(data["interval_s"]),
            status=Status(_text(data["status"])),
            created_at=_float(data["created_at"]),
            updated_at=_float(data["updated_at"]),
            last_fired_at=_opt_float(data["last_fired_at"]),
            fires=_int(data["fires"]),
            max_fires=_opt_int(data["max_fires"]),
            expires_at=_opt_float(data["expires_at"]),
            last_error=_text(data["last_error"]),
            error_count=_int(data["error_count"]),
            version=_int(data["version"], 1),
            scope=Scope(_text(data["scope"], Scope.CHAT.value)),
            anchor_session_id=_opt_text(data["anchor_session_id"]),
            noop_streak=_int(data["noop_streak"]),
        )

    def require_beat(self, beat_id: str) -> Beat:
        beat = self.get_beat(beat_id)
        if beat is None:
            raise KeyError(f"unknown beat: {beat_id}")
        return beat

    def create_beat(
        self,
        *,
        session_key: str,
        note: str,
        kind: Kind,
        next_at: float,
        interval_s: float | None,
        max_fires: int | None,
        expires_at: float | None,
        scope: Scope = Scope.CHAT,
        anchor_session_id: str | None = None,
    ) -> Beat:
        now = self.clock.now()
        beat_id = "c_" + secrets.token_hex(4)
        with self.conn:
            self.conn.execute(
                "INSERT INTO beats(id,session_key,note,kind,next_at,interval_s,status,"
                "created_at,updated_at,max_fires,expires_at,scope,anchor_session_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    beat_id,
                    session_key,
                    note,
                    kind,
                    next_at,
                    interval_s,
                    Status.ACTIVE,
                    now,
                    now,
                    max_fires,
                    expires_at,
                    scope,
                    anchor_session_id,
                ),
            )
        return self.require_beat(beat_id)

    def get_beat(self, beat_id: str) -> Beat | None:
        return self._beat(self._fetch_one("SELECT * FROM beats WHERE id=?", (beat_id,)))

    def list_beats(self, session_key: str | None = None) -> list[Beat]:
        if session_key:
            rows = self._fetch_all(
                "SELECT * FROM beats WHERE session_key=? ORDER BY created_at,id", (session_key,)
            )
        else:
            rows = self._fetch_all("SELECT * FROM beats ORDER BY created_at,id")
        return [beat for row in rows if (beat := self._beat(row)) is not None]

    def due(self, now: float) -> list[Beat]:
        rows = self._fetch_all(
            "SELECT * FROM beats WHERE status=? AND next_at IS NOT NULL AND next_at<=? ORDER BY next_at,id",
            (Status.ACTIVE, now),
        )
        return [beat for row in rows if (beat := self._beat(row)) is not None]

    def update_beat(self, beat_id: str, patch: BeatPatch | None = None) -> Beat:
        updates = patch.items() if patch is not None else {}
        if not updates:
            return self.require_beat(beat_id)
        updates["updated_at"] = self.clock.now()
        columns = ", ".join(f"{key}=?" for key in updates)
        with self.conn:
            self.conn.execute(
                f"UPDATE beats SET {columns}, version=version+1 WHERE id=?",
                (*updates.values(), beat_id),
            )
        return self.require_beat(beat_id)

    def set_status(
        self, beat_id: str, status: Status, *, error: str = "", next_at: float | None = None
    ) -> Beat:
        return self.update_beat(beat_id, BeatPatch(status=status, last_error=error, next_at=next_at))

    def delete_beat(self, beat_id: str) -> bool:
        with self.conn:
            self.conn.execute("DELETE FROM occurrences WHERE beat_id=?", (beat_id,))
            cursor = self.conn.execute("DELETE FROM beats WHERE id=?", (beat_id,))
        return cursor.rowcount > 0

    def _session_values(
        self,
        session_key: str,
        *,
        current_session_id: str | None,
        platform: str | None,
        chat_id: str | None,
        human_at: float | None,
        human_session_id: str | None,
    ) -> tuple[str, str, str, float | None, str, int]:
        existing = self.get_session(session_key)
        current = (
            current_session_id
            if current_session_id is not None
            else (existing.current_session_id if existing else "")
        )
        plat = platform if platform is not None else (existing.platform if existing else "")
        chat = chat_id if chat_id is not None else (existing.chat_id if existing else "")
        last_human = human_at if human_at is not None else (existing.last_human_at if existing else None)
        human_session = (
            human_session_id
            if human_session_id is not None
            else (existing.human_session_id if existing else "")
        )
        unanswered = 0 if human_at is not None else (existing.unanswered_count if existing else 0)
        return current, plat, chat, last_human, human_session, unanswered

    def _write_session(
        self,
        session_key: str,
        values: tuple[str, str, str, float | None, str, int],
    ) -> None:
        self.conn.execute(
            "INSERT INTO sessions(session_key,current_session_id,platform,chat_id,last_human_at,"
            "human_session_id,unanswered_count) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(session_key) DO UPDATE SET "
            "current_session_id=excluded.current_session_id, platform=excluded.platform, "
            "chat_id=excluded.chat_id, last_human_at=excluded.last_human_at, "
            "human_session_id=excluded.human_session_id, "
            "unanswered_count=excluded.unanswered_count",
            (session_key, *values),
        )

    def touch_session(
        self,
        session_key: str,
        *,
        current_session_id: str | None = None,
        platform: str | None = None,
        chat_id: str | None = None,
        human_at: float | None = None,
        human_session_id: str | None = None,
    ) -> None:
        values = self._session_values(
            session_key,
            current_session_id=current_session_id,
            platform=platform,
            chat_id=chat_id,
            human_at=human_at,
            human_session_id=human_session_id,
        )
        with self.conn:
            self._write_session(session_key, values)

    def note_human_message(
        self,
        session_key: str,
        *,
        current_session_id: str | None = None,
        platform: str | None = None,
        chat_id: str | None = None,
        human_at: float,
        human_session_id: str | None = None,
    ) -> int:
        """Record a human message and resume muted beats in one transaction."""
        values = self._session_values(
            session_key,
            current_session_id=current_session_id,
            platform=platform,
            chat_id=chat_id,
            human_at=human_at,
            human_session_id=human_session_id,
        )
        now = self.clock.now()
        with self.conn:
            self._write_session(session_key, values)
            cursor = self.conn.execute(
                "UPDATE beats SET status=?, next_at=?, last_error='', "
                "updated_at=?, version=version+1 "
                "WHERE session_key=? AND status=? AND last_error=?",
                (
                    Status.ACTIVE,
                    now,
                    now,
                    session_key,
                    Status.PAUSED,
                    Reason.UNANSWERED_MUTE,
                ),
            )
        return cursor.rowcount

    def start_session(self, session_key: str, session_id: str) -> None:
        """A new session has no human proof until its first human message."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO sessions(session_key,current_session_id,unanswered_count,"
                "human_session_id,last_human_at) VALUES(?,?,0,'',NULL) "
                "ON CONFLICT(session_key) DO UPDATE SET "
                "current_session_id=excluded.current_session_id, "
                "unanswered_count=0, human_session_id='', last_human_at=NULL",
                (session_key, session_id or "__new__"),
            )

    def get_session(self, session_key: str) -> Session | None:
        row = self._fetch_one("SELECT * FROM sessions WHERE session_key=?", (session_key,))
        if row is None:
            return None
        data = _row_values(row)
        return Session(
            session_key=_text(data["session_key"]),
            current_session_id=_text(data["current_session_id"]),
            platform=_text(data["platform"]),
            chat_id=_text(data["chat_id"]),
            last_human_at=_opt_float(data["last_human_at"]),
            human_session_id=_text(data["human_session_id"]),
            last_beat_at=_opt_float(data["last_beat_at"]),
            unanswered_count=_int(data["unanswered_count"]),
        )

    def has_human(self, session_key: str) -> bool:
        session = self.get_session(session_key)
        if session is None or session.last_human_at is None:
            return False
        if not session.current_session_id or not session.human_session_id:
            return False
        return session.current_session_id == session.human_session_id

    def claim(self, beat: Beat, due_key: str, owner: str, now: float) -> bool:
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO occurrences(beat_id,due_key,state,claimed_by,claimed_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(beat_id,due_key) DO UPDATE SET "
                "state=?, claimed_by=excluded.claimed_by, claimed_at=excluded.claimed_at, "
                "finished_at=NULL, error='' "
                "WHERE occurrences.state NOT IN (?,?)",
                (
                    beat.id,
                    due_key,
                    OccurrenceState.CLAIMED,
                    owner,
                    now,
                    OccurrenceState.CLAIMED,
                    OccurrenceState.CLAIMED,
                    OccurrenceState.INJECTED,
                ),
            )
        return cursor.rowcount > 0

    def complete(self, beat: Beat, due_key: str, now: float, day: str) -> Beat:
        current = self.require_beat(beat.id)
        fires = current.fires + 1
        done = current.kind is Kind.AT or (current.max_fires is not None and fires >= current.max_fires)
        status = current.status
        next_at = current.next_at
        last_error = current.last_error
        error_count = current.error_count
        if current.status is Status.ACTIVE:
            status = Status.DONE if done else Status.ACTIVE
            if done:
                next_at = None
            else:
                interval = float(current.interval_s or 0)
                candidate = float(current.next_at or now) + interval
                next_at = candidate if candidate > now else now + interval
            last_error = ""
            error_count = 0
        with self.conn:
            self.conn.execute(
                "UPDATE occurrences SET state=?, finished_at=?, error='' WHERE beat_id=? AND due_key=?",
                (OccurrenceState.INJECTED, now, current.id, due_key),
            )
            self.conn.execute(
                "UPDATE beats SET fires=?, last_fired_at=?, updated_at=?, status=?, next_at=?, "
                "last_error=?, error_count=?, version=version+1 WHERE id=?",
                (fires, now, now, status, next_at, last_error, error_count, current.id),
            )
            self.conn.execute(
                "INSERT INTO ledger(day,session_key,messages,fires) VALUES(?,?,0,1) "
                "ON CONFLICT(day,session_key) DO UPDATE SET fires=fires+1",
                (day, current.session_key),
            )
            self.conn.execute(
                "UPDATE sessions SET last_beat_at=?, unanswered_count=unanswered_count+1 WHERE session_key=?",
                (now, current.session_key),
            )
        return self.require_beat(current.id)

    def record_message(self, beat_id: str, day: str) -> None:
        """Count a delivered (non-silent) reply against the daily message cap."""
        beat = self.get_beat(beat_id)
        if beat is None:
            return
        with self.conn:
            self.conn.execute(
                "INSERT INTO ledger(day,session_key,messages,fires) VALUES(?,?,1,0) "
                "ON CONFLICT(day,session_key) DO UPDATE SET messages=messages+1",
                (day, beat.session_key),
            )

    def fail(self, beat: Beat, due_key: str, now: float, error: str, retry_s: float) -> Beat:
        current = self.require_beat(beat.id)
        errors = current.error_count + 1
        status = current.status
        next_at = current.next_at
        if current.status is Status.ACTIVE:
            status = Status.FAILED if errors >= 10 else Status.ACTIVE
            next_at = None if status is Status.FAILED else now + retry_s
        with self.conn:
            self.conn.execute(
                "UPDATE occurrences SET state=?, finished_at=?, error=? WHERE beat_id=? AND due_key=?",
                (OccurrenceState.FAILED, now, error[:300], current.id, due_key),
            )
            self.conn.execute(
                "UPDATE beats SET status=?, next_at=?, updated_at=?, last_error=?, "
                "error_count=?, version=version+1 WHERE id=?",
                (status, next_at, now, error[:300], errors, current.id),
            )
        return self.require_beat(current.id)

    def defer(self, beat: Beat, next_at: float, error: str) -> Beat:
        return self.update_beat(beat.id, BeatPatch(next_at=next_at, last_error=error[:300]))

    def expire(self, beat: Beat) -> Beat:
        return self.set_status(beat.id, Status.EXPIRED, error=Reason.EXPIRED)

    def miss(self, beat: Beat) -> Beat:
        return self.set_status(beat.id, Status.MISSED, error=Reason.MISSED, next_at=None)

    def ledger(self, day: str, session_key: str) -> tuple[int, int]:
        row = self._fetch_one(
            "SELECT messages,fires FROM ledger WHERE day=? AND session_key=?", (day, session_key)
        )
        if row is None:
            return (0, 0)
        data = _row_values(row)
        return _int(data["messages"]), _int(data["fires"])

    def recover_stale(self, now: float, older_s: float = 300) -> int:
        with self.conn:
            cursor = self.conn.execute(
                "UPDATE occurrences SET state=?, finished_at=?, error=? WHERE state=? AND claimed_at<?",
                (
                    OccurrenceState.FAILED,
                    now,
                    Reason.STALE_CLAIM,
                    OccurrenceState.CLAIMED,
                    now - older_s,
                ),
            )
        return cursor.rowcount

    def record_noop(
        self, beat_id: str, *, after: int, factor: float, max_hours: float, pause_after: int, now: float
    ) -> None:
        beat = self.get_beat(beat_id)
        if beat is None or beat.kind is not Kind.EVERY:
            return
        streak = beat.noop_streak + 1
        if pause_after and streak >= pause_after:
            self.update_beat(
                beat_id,
                BeatPatch(
                    status=Status.PAUSED,
                    last_error=Reason.NOOP_PAUSE,
                    next_at=None,
                    noop_streak=streak,
                ),
            )
            return
        if after and streak >= after:
            interval = float(beat.interval_s or 0)
            delay = interval * (factor ** (streak - after + 1))
            delay = min(delay, max_hours * 3600)
            self.update_beat(
                beat_id,
                BeatPatch(
                    next_at=now + max(interval, delay),
                    last_error=Reason.NOOP_BACKOFF,
                    noop_streak=streak,
                ),
            )
            return
        self.update_beat(beat_id, BeatPatch(noop_streak=streak))

    def reset_noop(self, beat_id: str) -> None:
        self.update_beat(beat_id, BeatPatch(noop_streak=0, last_error=""))

    def mute(self, beat: Beat) -> Beat:
        return self.update_beat(
            beat.id,
            BeatPatch(
                status=Status.PAUSED,
                last_error=Reason.UNANSWERED_MUTE,
                next_at=None,
            ),
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.conn.close()
