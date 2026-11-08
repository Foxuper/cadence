from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from cadence.hermes.runtime import FileLock

ROOT = Path(__file__).resolve().parents[2]

CHILD = """
import sys, time
from pathlib import Path
from cadence.hermes.runtime import FileLock

lock = FileLock(Path(sys.argv[1]))
marker = Path(sys.argv[2])
acquired = lock.acquire()
marker.write_text("ok" if acquired else "no", encoding="utf-8")
if acquired:
    time.sleep(30)
"""


def _wait_for(path: Path, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return path.read_text(encoding="utf-8")
        time.sleep(0.02)
    raise AssertionError(f"child did not write {path}")


def _acquire_with_retry(lock: FileLock, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if lock.acquire():
            return True
        time.sleep(0.05)
    return False


def test_file_lock_is_cross_process(tmp_path: Path) -> None:
    lock_path = tmp_path / "cadence.lock"
    marker = tmp_path / "ready"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    proc = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(lock_path), str(marker)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert _wait_for(marker) == "ok"
        parent = FileLock(lock_path)
        assert parent.acquire() is False
        parent.release()
    finally:
        proc.terminate()
        try:
            _, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, stderr = proc.communicate(timeout=10)
        assert not stderr, f"child failed: {stderr}"

    # The OS releases the lock when the child dies; a stale file must not block us.
    final = FileLock(lock_path)
    assert _acquire_with_retry(final)
    final.release()
