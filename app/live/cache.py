"""TTL cache shared by the two uvicorn workers.

Each entry lives in process memory *and* in a small file under a temp directory,
so the two workers on the 1 GiB instance share one copy of each upstream fetch
instead of hitting NEA / Open-Meteo / FIRMS twice. A refresh holds an advisory
file lock (``fcntl.flock``), so when both workers go cold at once only one fetches
and the other reads what it wrote. That matters for the rate limits: data.gov.sg
allows 6 real-time calls per 10 s without a key, and Open-Meteo's free tier
counts every coordinate as a call against 600/min.

If the disk is unavailable (read-only FS, no ``fcntl``) the cache degrades to
per-process memory only. A failed refresh serves the previous value, marked
stale, until ``max_stale`` has passed; after that the source is unavailable.

Two codecs: ``json`` for small dicts, ``npz`` for dicts of numpy arrays (FIRMS
points, the CAMS field). ``npz`` loads with ``allow_pickle=False``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.live.common import SourceUnavailable, describe_error, log

try:  # POSIX only; the container and dev machines both have it
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


def _default_dir() -> Path | None:
    raw = os.environ.get("WWHE_LIVE_CACHE_DIR")
    if raw == "":
        return None  # explicitly disabled
    return Path(raw) if raw else Path(tempfile.gettempdir()) / "whenwillhazeend-live"


_disk_dir: Path | None = _default_dir()
_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def set_disk_dir(path: Path | None) -> None:
    """Point the disk layer somewhere else, or disable it with ``None`` (tests)."""
    global _disk_dir
    _disk_dir = path


def disk_dir() -> Path | None:
    if _disk_dir is None:
        return None
    try:
        _disk_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return _disk_dir


def _safe(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", key)


@dataclass
class Entry:
    value: Any
    fetched_at: float  # epoch seconds
    meta: dict = field(default_factory=dict)

    def age(self) -> float:
        return time.time() - self.fetched_at


@dataclass
class Result:
    value: Any
    fetched_at: float
    stale: bool = False
    error: str | None = None
    meta: dict = field(default_factory=dict)


class Store:
    """One namespace of cached values (memory first, then disk)."""

    def __init__(self, namespace: str, codec: str = "json") -> None:
        assert codec in ("json", "npz")
        self.namespace = namespace
        self.codec = codec
        self._mem: dict[str, Entry] = {}
        self._lock = threading.Lock()

    # -- disk ------------------------------------------------------------- #
    def _path(self, key: str) -> Path | None:
        d = disk_dir()
        if d is None:
            return None
        ext = ".json" if self.codec == "json" else ".npz"
        return d / f"{_safe(self.namespace)}__{_safe(key)}{ext}"

    def _read_disk(self, key: str) -> Entry | None:
        path = self._path(key)
        if path is None or not path.is_file():
            return None
        try:
            if self.codec == "json":
                raw = json.loads(path.read_text("utf-8"))
                return Entry(raw["value"], float(raw["fetched_at"]), raw.get("meta") or {})
            import numpy as np

            with np.load(path, allow_pickle=False) as z:
                header = json.loads(str(z["__header__"]))
                value = {k: z[k] for k in z.files if k != "__header__"}
            return Entry(value, float(header["fetched_at"]), header.get("meta") or {})
        except Exception as exc:  # corrupt / partial / foreign file: ignore it
            log.warning("live cache: unreadable %s (%s)", path.name, describe_error(exc))
            return None

    def _write_disk(self, key: str, entry: Entry) -> None:
        path = self._path(key)
        if path is None:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=path.suffix)
            with os.fdopen(fd, "wb") as fh:
                if self.codec == "json":
                    fh.write(json.dumps({"value": entry.value, "fetched_at": entry.fetched_at,
                                         "meta": entry.meta}, separators=(",", ":")).encode())
                else:
                    import numpy as np

                    header = json.dumps({"fetched_at": entry.fetched_at, "meta": entry.meta})
                    np.savez(fh, __header__=np.array(header), **entry.value)
            os.replace(tmp, path)
        except Exception as exc:
            log.warning("live cache: could not write %s (%s)", path.name, describe_error(exc))
            try:
                os.unlink(tmp)  # type: ignore[possibly-undefined]
            except Exception:
                pass

    # -- public ----------------------------------------------------------- #
    def get(self, key: str, fresh_for: float | None = None) -> Entry | None:
        """Newest of memory and disk. If memory is younger than ``fresh_for``
        seconds the disk is not consulted."""
        with self._lock:
            mem = self._mem.get(key)
        if mem is not None and fresh_for is not None and mem.age() < fresh_for:
            return mem
        disk = self._read_disk(key)
        best = mem
        if disk is not None and (best is None or disk.fetched_at > best.fetched_at):
            best = disk
            with self._lock:
                self._mem[key] = disk
        return best

    def put(self, key: str, value: Any, meta: dict | None = None,
            fetched_at: float | None = None) -> Entry:
        entry = Entry(value, fetched_at if fetched_at is not None else time.time(), meta or {})
        with self._lock:
            self._mem[key] = entry
        self._write_disk(key, entry)
        return entry

    def clear(self) -> None:
        with self._lock:
            self._mem.clear()


@contextmanager
def single_flight(name: str, timeout: float) -> Iterator[bool]:
    """Hold a per-name lock across threads *and* processes for a refresh.

    Yields ``True`` if the lock was acquired within ``timeout`` seconds, else
    ``False`` (the caller should then serve whatever is cached).
    """
    with _thread_locks_guard:
        tlock = _thread_locks.setdefault(name, threading.Lock())
    if not tlock.acquire(timeout=max(0.0, timeout)):
        yield False
        return
    fh = None
    try:
        d = disk_dir()
        if d is not None and fcntl is not None:
            try:
                fh = open(d / f"{_safe(name)}.lock", "a+")
            except OSError:
                fh = None
        if fh is not None:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        yield False
                        return
                    time.sleep(0.1)
        yield True
    finally:
        if fh is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            fh.close()
        tlock.release()


def cached(store: Store, key: str, *, ttl: float, max_stale: float,
           loader: Callable[[Entry | None], tuple[Any, dict]],
           lock_timeout: float = 30.0) -> Result:
    """Return a fresh value, refreshing through ``loader`` when needed.

    ``loader(previous_entry)`` returns ``(value, meta)``; it receives the previous
    entry so it can make a conditional request (FIRMS ETag). Any exception from
    the loader is caught: the previous value is served as stale if it is younger
    than ``max_stale``, otherwise :class:`SourceUnavailable` is raised.
    """
    entry = store.get(key, fresh_for=ttl)
    if entry is not None and entry.age() < ttl:
        return Result(entry.value, entry.fetched_at, meta=entry.meta)

    with single_flight(f"{store.namespace}:{key}", timeout=lock_timeout) as acquired:
        entry = store.get(key)  # another worker may have refreshed meanwhile
        if entry is not None and entry.age() < ttl:
            return Result(entry.value, entry.fetched_at, meta=entry.meta)
        if not acquired:
            if entry is not None and entry.age() < max_stale:
                return Result(entry.value, entry.fetched_at, stale=True,
                              error="refresh in progress elsewhere", meta=entry.meta)
            raise SourceUnavailable("refresh in progress elsewhere; nothing cached yet")
        try:
            value, meta = loader(entry)
        except Exception as exc:
            reason = describe_error(exc)
            log.warning("live source %s:%s refresh failed: %s", store.namespace, key, reason)
            if entry is not None and entry.age() < max_stale:
                return Result(entry.value, entry.fetched_at, stale=True, error=reason,
                              meta=entry.meta)
            raise SourceUnavailable(reason) from exc
        new = store.put(key, value, meta)
        return Result(new.value, new.fetched_at, meta=new.meta)
