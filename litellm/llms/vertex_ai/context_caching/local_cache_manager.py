import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Optional, TypedDict


class LocalCacheStats(TypedDict):
    total_entries: int
    valid_entries: int
    expired_entries: int
    estimated_memory_kb: float
    cleanup_interval_seconds: int
    cleanup_thread_alive: bool
    cache_keys_sample: list[str]


@dataclass(frozen=True, slots=True)
class CacheEntry:
    cache_id: str
    expire_time: float

    @classmethod
    def from_ttl(cls, cache_id: str, ttl_seconds: float) -> "CacheEntry":
        return cls(cache_id=cache_id, expire_time=time.time() + ttl_seconds)

    def is_expired(self) -> bool:
        return time.time() >= self.expire_time

    def time_until_expiry(self) -> float:
        return self.expire_time - time.time()


class LocalCacheManager:
    def __init__(self, cleanup_interval_seconds: int = 300) -> None:
        self._cache: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        self._cleanup_interval_seconds = cleanup_interval_seconds
        self._stop_cleanup = threading.Event()
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            daemon=True,
            name="vertex-context-cache-cleanup",
        )
        self._cleanup_thread.start()

    def _make_scoped_key(
        self,
        cache_key: str,
        vertex_project: Optional[str],
        vertex_location: Optional[str],
        custom_llm_provider: Optional[str] = None,
    ) -> str:
        if custom_llm_provider == "gemini":
            return cache_key

        if vertex_project is None or vertex_location is None:
            return cache_key

        scope_hash = hashlib.sha256(
            f"{cache_key}:{vertex_project}:{vertex_location}".encode()
        ).hexdigest()[:16]
        return f"{cache_key}:{vertex_project}:{vertex_location}:{scope_hash}"

    def set_cache(
        self,
        cache_key: str,
        cache_id: str,
        ttl_seconds: float,
        vertex_project: Optional[str] = None,
        vertex_location: Optional[str] = None,
        custom_llm_provider: Optional[str] = None,
    ) -> None:
        scoped_key = self._make_scoped_key(
            cache_key, vertex_project, vertex_location, custom_llm_provider
        )
        with self._lock:
            self._cache[scoped_key] = CacheEntry.from_ttl(cache_id, ttl_seconds)

    def get_cache(
        self,
        cache_key: str,
        vertex_project: Optional[str] = None,
        vertex_location: Optional[str] = None,
        custom_llm_provider: Optional[str] = None,
    ) -> Optional[str]:
        scoped_key = self._make_scoped_key(
            cache_key, vertex_project, vertex_location, custom_llm_provider
        )
        with self._lock:
            entry = self._cache.get(scoped_key)
            if entry is None:
                return None

            if entry.is_expired():
                del self._cache[scoped_key]
                return None

            return entry.cache_id

    def has_valid_cache(
        self,
        cache_key: str,
        vertex_project: Optional[str] = None,
        vertex_location: Optional[str] = None,
        custom_llm_provider: Optional[str] = None,
    ) -> bool:
        return (
            self.get_cache(
                cache_key, vertex_project, vertex_location, custom_llm_provider
            )
            is not None
        )

    def invalidate_cache(
        self,
        cache_key: str,
        vertex_project: Optional[str] = None,
        vertex_location: Optional[str] = None,
        custom_llm_provider: Optional[str] = None,
    ) -> None:
        scoped_key = self._make_scoped_key(
            cache_key, vertex_project, vertex_location, custom_llm_provider
        )
        with self._lock:
            self._cache.pop(scoped_key, None)

    def clear_all(self) -> None:
        with self._lock:
            self._cache.clear()

    def cleanup_expired(self) -> int:
        with self._lock:
            expired_keys = tuple(
                key for key, entry in self._cache.items() if entry.is_expired()
            )
            for key in expired_keys:
                del self._cache[key]
            return len(expired_keys)

    def get_stats(self) -> LocalCacheStats:
        with self._lock:
            total_entries = len(self._cache)
            expired_entries = sum(
                1 for entry in self._cache.values() if entry.is_expired()
            )
            return {
                "total_entries": total_entries,
                "valid_entries": total_entries - expired_entries,
                "expired_entries": expired_entries,
                "estimated_memory_kb": round((total_entries * 200) / 1024, 2),
                "cleanup_interval_seconds": self._cleanup_interval_seconds,
                "cleanup_thread_alive": self._cleanup_thread.is_alive(),
                "cache_keys_sample": list(self._cache.keys())[:10],
            }

    def shutdown(self) -> None:
        self._stop_cleanup.set()
        if self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=2)

    def _cleanup_loop(self) -> None:
        while not self._stop_cleanup.wait(timeout=self._cleanup_interval_seconds):
            self.cleanup_expired()


_global_cache_manager: Optional[LocalCacheManager] = None
_manager_lock = threading.Lock()


def get_cache_manager() -> LocalCacheManager:
    global _global_cache_manager

    if _global_cache_manager is not None:
        return _global_cache_manager

    with _manager_lock:
        if _global_cache_manager is None:
            _global_cache_manager = LocalCacheManager()
        return _global_cache_manager
