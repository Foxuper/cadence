"""Scheduler runtime: cross-process lock, loop and lifecycle."""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ..core.engine import Engine
from ..core.policy import PolicyProvider
from ..core.store import Store
from ..protocols import Clock
from .api import home_scope

LOG = logging.getLogger("cadence")

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None


class SystemClock:
    def now(self) -> float:
        return time.time()


@dataclass
class ActiveBeat:
    turn_id: str
    key: str
    beat_id: str
    silent: bool | None = None


class FileLock:
    """One scheduler per profile, across processes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: BinaryIO | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")  # noqa: SIM115 - released by release()
        try:
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt

                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except (BlockingIOError, OSError):
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self) -> None:
        if self.handle is None:
            return
        with suppress(OSError):
            if fcntl is not None:
                fcntl.flock(self.handle, fcntl.LOCK_UN)
            else:
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        self.handle.close()
        self.handle = None


class Runtime:
    def __init__(
        self,
        *,
        home: Path,
        data_dir: Path,
        store: Store,
        engine: Engine,
        policy_provider: PolicyProvider,
        clock: Clock,
    ) -> None:
        self.home = home
        self.store = store
        self.engine = engine
        self.policy_provider = policy_provider
        self.clock = clock
        self.lock = FileLock(data_dir / "cadence.lock")
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.active: dict[str, ActiveBeat] = {}
        self._guard = threading.Lock()

    def start(self) -> bool:
        if not self.lock.acquire():
            LOG.warning("cadence: another scheduler already holds this profile")
            return False
        self.thread = threading.Thread(target=self._loop, name="cadence", daemon=True)
        self.thread.start()
        return True

    def _loop(self) -> None:
        try:
            with self.store.lock:
                self.store.recover_stale(self.clock.now())
        except Exception:
            LOG.warning("cadence: stale recovery failed", exc_info=True)
        while not self.stop.is_set():
            poll_s = 5.0
            try:
                with home_scope(self.home):
                    policy = self.policy_provider()
                    poll_s = max(1.0, policy.poll_s)
                    self.engine.tick()
            except Exception:
                LOG.warning("cadence: tick failed", exc_info=True)
            self.stop.wait(poll_s)

    def close(self) -> bool:
        """Stop the loop; returns False when the thread outlived the join timeout."""
        self.stop.set()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        self.lock.release()
        alive = self.thread is not None and self.thread.is_alive()
        if not alive:
            with suppress(Exception):
                self.store.close()
        return not alive

    def note_active(self, session_id: str, turn_id: str, key: str, beat_id: str) -> None:
        if not session_id:
            return
        with self._guard:
            self.active[session_id] = ActiveBeat(turn_id or "", key, beat_id)

    def mark_response(self, session_id: str, silent: bool) -> bool:
        if not session_id:
            return False
        with self._guard:
            entry = self.active.get(session_id)
            if entry is None:
                return False
            entry.silent = silent
            return True

    def pop_active(self, session_id: str, turn_id: str) -> ActiveBeat | None:
        with self._guard:
            entry = self.active.pop(session_id, None)
        if entry is None:
            return None
        if entry.turn_id and turn_id and entry.turn_id != turn_id:
            return None
        return entry

    def clear_active(self, session_id: str) -> None:
        with self._guard:
            self.active.pop(session_id, None)
