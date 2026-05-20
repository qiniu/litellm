"""
Local cache manager for Vertex AI / Gemini context caching.

This module provides local in-memory caching of Google's cached content IDs
to avoid redundant network requests to check if cache exists.

IMPORTANT: Caches are scoped by project/location to handle multi-project setups.

Usage:
    manager = LocalCacheManager()

    # Store cache mapping (with project/location scope)
    manager.set_cache(
        cache_key="content-hash-xxx",
        cache_id="projects/my-project/locations/global/cachedContents/123",
        ttl_seconds=3600,
        vertex_project="my-project",
        vertex_location="global"
    )

    # Retrieve cache if not expired (must match project/location)
    cache_id = manager.get_cache(
        cache_key="content-hash-xxx",
        vertex_project="my-project",
        vertex_location="global"
    )
"""

import time
import hashlib
import os
from typing import Dict, Optional, Tuple
import threading
from litellm._logging import verbose_proxy_logger


class CacheEntry:
    """Represents a single cache entry with expiration."""

    def __init__(self, cache_id: str, ttl_seconds: float):
        self.cache_id = cache_id
        self.created_at = time.time()
        self.ttl_seconds = ttl_seconds
        self.expire_time = self.created_at + ttl_seconds

    def is_expired(self) -> bool:
        """Check if this cache entry has expired."""
        return time.time() >= self.expire_time

    def time_until_expiry(self) -> float:
        """Get seconds until expiration (negative if already expired)."""
        return self.expire_time - time.time()


class LocalCacheManager:
    """
    Thread-safe local cache manager for Vertex AI context caching.

    Stores mapping of (cache_key, project, location) -> cache_id with expiration tracking.
    This avoids redundant network requests to check if cache exists on Google's servers.

    Each cache is scoped by Vertex AI project and location to support multi-project setups.

    Features:
    - Automatic background cleanup of expired entries every 5 minutes
    - Thread-safe operations with lock protection
    - Lazy deletion on get_cache() calls
    """

    def __init__(self, cleanup_interval_seconds: int = 300):
        """
        Initialize the cache manager.

        Args:
            cleanup_interval_seconds: How often to run background cleanup (default: 300s = 5 minutes)
        """
        self._cache: Dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        self._cleanup_interval = cleanup_interval_seconds
        self._cleanup_thread: Optional[threading.Thread] = None
        self._stop_cleanup = threading.Event()

        verbose_proxy_logger.info(
            f"Local cache: LocalCacheManager.__init__ called, PID={os.getpid()}, "
            f"instance_id={id(self)}, cleanup_interval={cleanup_interval_seconds}s"
        )

        # Start background cleanup thread
        self._start_cleanup_thread()

    def _make_scoped_key(
        self,
        cache_key: str,
        vertex_project: Optional[str],
        vertex_location: Optional[str],
        custom_llm_provider: Optional[str] = None
    ) -> str:
        """
        Create a scoped cache key that includes project and location.

        This ensures that caches are isolated per project/location combination.

        Args:
            cache_key: Base cache key from message content
            vertex_project: Vertex AI project ID (e.g., "gemini-qn-bz")
            vertex_location: Vertex AI location (e.g., "global", "us-central1")
            custom_llm_provider: Provider type (e.g., "vertex_ai", "gemini")

        Returns:
            Scoped cache key that uniquely identifies this cache across projects
        """
        # For Google AI Studio (gemini provider), no project scoping needed
        if custom_llm_provider == "gemini":
            return cache_key

        # For Vertex AI, include project and location in scope
        if vertex_project and vertex_location:
            scope_parts = [cache_key, vertex_project, vertex_location]
            scope_string = ":".join(scope_parts)
            # Use hash to keep key length reasonable
            scope_hash = hashlib.md5(scope_string.encode()).hexdigest()[:16]
            return f"{cache_key}:{vertex_project}:{vertex_location}:{scope_hash}"

        # Fallback: just use the base cache key
        return cache_key

    def set_cache(
        self,
        cache_key: str,
        cache_id: str,
        ttl_seconds: float,
        vertex_project: Optional[str] = None,
        vertex_location: Optional[str] = None,
        custom_llm_provider: Optional[str] = None
    ) -> None:
        """
        Store a cache mapping locally with project/location scope.

        Args:
            cache_key: The cache key (displayName) used to identify the cache
            cache_id: The Google cache ID (name field from API response)
            ttl_seconds: Time-to-live in seconds
            vertex_project: Vertex AI project ID (for scoping)
            vertex_location: Vertex AI location (for scoping)
            custom_llm_provider: Provider type (for scoping)
        """
        scoped_key = self._make_scoped_key(
            cache_key, vertex_project, vertex_location, custom_llm_provider
        )

        with self._lock:
            # Add a small buffer to avoid edge cases where local cache
            # thinks it's valid but Google has just expired it
            # Dynamic buffer: 2% of TTL, minimum 3s, maximum 10s, rounded to integer
            verbose_proxy_logger.debug(
                f"Local cache: storing cache entry cache_key={cache_key[:30]}..., "
                f"Google TTL={int(ttl_seconds)}s"
                f"PID={os.getpid()}, instance_id={id(self)}, current_entries={len(self._cache)}"
            )

            self._cache[scoped_key] = CacheEntry(cache_id, ttl_seconds)

            verbose_proxy_logger.debug(
                f"Local cache: store complete scoped_key={scoped_key}, "
                f"entries_after_store={len(self._cache)}"
            )

    def get_cache(
        self,
        cache_key: str,
        vertex_project: Optional[str] = None,
        vertex_location: Optional[str] = None,
        custom_llm_provider: Optional[str] = None
    ) -> Optional[str]:
        """
        Get cache ID if it exists and is not expired, scoped by project/location.

        Args:
            cache_key: The cache key to lookup
            vertex_project: Vertex AI project ID (must match set_cache)
            vertex_location: Vertex AI location (must match set_cache)
            custom_llm_provider: Provider type (must match set_cache)

        Returns:
            Cache ID if found and valid, None otherwise
        """
        scoped_key = self._make_scoped_key(
            cache_key, vertex_project, vertex_location, custom_llm_provider
        )

        with self._lock:
            total_items = len(self._cache)
            entry = self._cache.get(scoped_key)

            if entry is None:
                # Show all cache keys for debugging
                all_keys_sample = list(self._cache.keys())[:5]  # First 5 keys
                verbose_proxy_logger.debug(
                    f"Local cache: get_cache miss - "
                    f"lookup_key={scoped_key}, "
                    f"total_entries={total_items}, PID={os.getpid()}, instance_id={id(self)}, "
                    f"existing_key_samples(first_5)={all_keys_sample}"
                )
                return None

            if entry.is_expired():
                # Clean up expired entry
                del self._cache[scoped_key]
                verbose_proxy_logger.debug(
                    f"Local cache: get_cache found expired entry - scoped_key={scoped_key}"
                )
                return None

            verbose_proxy_logger.debug(
                f"Local cache: get_cache hit - scoped_key={scoped_key}, "
                f"remaining_ttl={entry.time_until_expiry():.1f}s, PID={os.getpid()}"
            )
            return entry.cache_id

    def has_valid_cache(
        self,
        cache_key: str,
        vertex_project: Optional[str] = None,
        vertex_location: Optional[str] = None,
        custom_llm_provider: Optional[str] = None
    ) -> bool:
        """
        Check if a valid cache exists for the given key and scope.

        Args:
            cache_key: The cache key to check
            vertex_project: Vertex AI project ID
            vertex_location: Vertex AI location
            custom_llm_provider: Provider type

        Returns:
            True if cache exists and is not expired, False otherwise
        """
        return self.get_cache(
            cache_key, vertex_project, vertex_location, custom_llm_provider
        ) is not None

    def invalidate_cache(
        self,
        cache_key: str,
        vertex_project: Optional[str] = None,
        vertex_location: Optional[str] = None,
        custom_llm_provider: Optional[str] = None
    ) -> None:
        """
        Manually invalidate a cache entry.

        Args:
            cache_key: The cache key to invalidate
            vertex_project: Vertex AI project ID
            vertex_location: Vertex AI location
            custom_llm_provider: Provider type
        """
        scoped_key = self._make_scoped_key(
            cache_key, vertex_project, vertex_location, custom_llm_provider
        )

        with self._lock:
            if scoped_key in self._cache:
                del self._cache[scoped_key]

    def clear_all(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()

    def _start_cleanup_thread(self) -> None:
        """
        Start background thread for periodic cleanup of expired entries.

        The thread runs as a daemon, so it won't prevent the program from exiting.
        """
        def cleanup_loop():
            verbose_proxy_logger.info(
                f"Local cache: background cleanup thread started, interval={self._cleanup_interval}s, "
                f"instance_id={id(self)}"
            )

            while not self._stop_cleanup.is_set():
                # Wait for cleanup interval or until stop signal
                self._stop_cleanup.wait(timeout=self._cleanup_interval)

                if self._stop_cleanup.is_set():
                    break

                # Perform cleanup
                try:
                    removed = self.cleanup_expired()
                    if removed > 0:
                        verbose_proxy_logger.debug(
                            f"Local cache: background cleanup complete, removed {removed} expired entries"
                        )
                except Exception as e:
                    verbose_proxy_logger.error(
                        f"Local cache: background cleanup failed - {str(e)}"
                    )

            verbose_proxy_logger.debug("Local cache: background cleanup thread stopped")

        self._cleanup_thread = threading.Thread(
            target=cleanup_loop,
            daemon=True,  # Daemon thread won't prevent program exit
            name="vertex-cache-cleanup"
        )
        self._cleanup_thread.start()

    def shutdown(self) -> None:
        """
        Gracefully shutdown the background cleanup thread.

        This method is optional - the daemon thread will exit automatically
        when the program exits. But calling this provides a cleaner shutdown.
        """
        verbose_proxy_logger.debug("Local cache: stopping background cleanup thread...")
        self._stop_cleanup.set()

        if self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=2)
            if self._cleanup_thread.is_alive():
                verbose_proxy_logger.warning(
                    "Local cache: background cleanup thread did not stop within 2 seconds"
                )
            else:
                verbose_proxy_logger.debug("Local cache: background cleanup thread stopped")

    def cleanup_expired(self) -> int:
        """
        Remove all expired cache entries.

        Returns:
            Number of entries removed
        """
        with self._lock:
            expired_keys = [
                key for key, entry in self._cache.items()
                if entry.is_expired()
            ]
            for key in expired_keys:
                del self._cache[key]
            return len(expired_keys)

    def get_stats(self) -> Dict:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache statistics including:
            - total_entries: Total number of cache entries
            - valid_entries: Number of non-expired entries
            - expired_entries: Number of expired entries (still in memory)
            - estimated_memory_kb: Rough memory usage estimate
            - cleanup_interval: Background cleanup interval in seconds
            - cleanup_thread_alive: Whether cleanup thread is running
            - cache_keys_sample: First 10 cache keys (for debugging)
        """
        with self._lock:
            total = len(self._cache)
            expired = sum(1 for entry in self._cache.values() if entry.is_expired())
            valid = total - expired

            # Estimate memory usage (rough approximation)
            # Each entry: ~200 bytes (cache_id string + timestamps + overhead)
            estimated_memory_kb = (total * 200) / 1024

            return {
                "total_entries": total,
                "valid_entries": valid,
                "expired_entries": expired,
                "estimated_memory_kb": round(estimated_memory_kb, 2),
                "cleanup_interval_seconds": self._cleanup_interval,
                "cleanup_thread_alive": (
                    self._cleanup_thread.is_alive()
                    if self._cleanup_thread
                    else False
                ),
                "cache_keys_sample": list(self._cache.keys())[:10],  # First 10 keys only
            }


# Global singleton instance
_global_cache_manager: Optional[LocalCacheManager] = None
_manager_lock = threading.Lock()


def get_cache_manager() -> LocalCacheManager:
    """
    Get the global singleton cache manager instance.

    Returns:
        Global LocalCacheManager instance
    """
    global _global_cache_manager

    if _global_cache_manager is None:
        with _manager_lock:
            if _global_cache_manager is None:
                _global_cache_manager = LocalCacheManager()
                verbose_proxy_logger.info(
                    f"Local cache: created new global cache manager instance ID={id(_global_cache_manager)}"
                )
    else:
        verbose_proxy_logger.debug(
            f"Local cache: returning existing global cache manager instance ID={id(_global_cache_manager)}, "
            f"current_entries={len(_global_cache_manager._cache)}"
        )

    return _global_cache_manager
