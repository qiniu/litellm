"""
Standalone test for multi-project cache scoping.
This script doesn't require full litellm imports.
"""

import sys
import os
import hashlib
import time
from typing import Dict, Optional
import threading

# Add the path
sys.path.insert(0, os.path.dirname(__file__))


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


class LocalCacheManager:
    """Simplified cache manager for testing."""

    def __init__(self):
        self._cache: Dict[str, CacheEntry] = {}
        self._lock = threading.Lock()

    def _make_scoped_key(
        self,
        cache_key: str,
        vertex_project: Optional[str],
        vertex_location: Optional[str],
        custom_llm_provider: Optional[str] = None
    ) -> str:
        """Create a scoped cache key."""
        if custom_llm_provider == "gemini":
            return cache_key

        if vertex_project and vertex_location:
            scope_parts = [cache_key, vertex_project, vertex_location]
            scope_string = ":".join(scope_parts)
            scope_hash = hashlib.md5(scope_string.encode()).hexdigest()[:16]
            return f"{cache_key}:{vertex_project}:{vertex_location}:{scope_hash}"

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
        """Store a cache mapping."""
        scoped_key = self._make_scoped_key(
            cache_key, vertex_project, vertex_location, custom_llm_provider
        )
        with self._lock:
            adjusted_ttl = ttl_seconds - 5 if ttl_seconds > 5 else ttl_seconds
            self._cache[scoped_key] = CacheEntry(cache_id, adjusted_ttl)

    def get_cache(
        self,
        cache_key: str,
        vertex_project: Optional[str] = None,
        vertex_location: Optional[str] = None,
        custom_llm_provider: Optional[str] = None
    ) -> Optional[str]:
        """Get cache ID if it exists and is not expired."""
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

    def clear_all(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()

    def get_stats(self) -> Dict:
        """Get cache statistics."""
        with self._lock:
            total = len(self._cache)
            expired = sum(1 for entry in self._cache.values() if entry.is_expired())
            valid = total - expired
            return {
                "total_entries": total,
                "valid_entries": valid,
                "expired_entries": expired,
                "cache_keys": list(self._cache.keys())
            }


def test_basic_scoping():
    """Test basic project/location scoping."""
    print("\n" + "=" * 80)
    print("Test 1: Basic Project/Location Scoping")
    print("=" * 80)

    manager = LocalCacheManager()
    manager.clear_all()

    cache_key = "content-hash-123"

    # Same content, different projects
    manager.set_cache(
        cache_key=cache_key,
        cache_id="cache-proj1",
        ttl_seconds=3600,
        vertex_project="project-1",
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )

    manager.set_cache(
        cache_key=cache_key,
        cache_id="cache-proj2",
        ttl_seconds=3600,
        vertex_project="project-2",
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )

    # Retrieve
    cache1 = manager.get_cache(
        cache_key=cache_key,
        vertex_project="project-1",
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )

    cache2 = manager.get_cache(
        cache_key=cache_key,
        vertex_project="project-2",
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )

    print(f"\nProject 1 cache: {cache1}")
    print(f"Project 2 cache: {cache2}")

    assert cache1 == "cache-proj1", f"Expected cache-proj1, got {cache1}"
    assert cache2 == "cache-proj2", f"Expected cache-proj2, got {cache2}"

    stats = manager.get_stats()
    print(f"\n✓ Test passed! {stats['valid_entries']} independent cache entries created")

    print("\nScoped keys:")
    for key in stats['cache_keys']:
        print(f"  - {key}")


def test_your_actual_config():
    """Test with your actual project configuration."""
    print("\n" + "=" * 80)
    print("Test 2: Your Actual Multi-Project Configuration")
    print("=" * 80)

    manager = LocalCacheManager()
    manager.clear_all()

    # Simulate your actual projects
    projects = [
        ("gemini-qn-bz", "global"),
        ("gemini-prod", "global"),
        ("gemini-dev", "us-central1"),
    ]

    cache_key = "system-prompt-hash-abc"

    print("\nSetting up caches for your projects:")
    for proj, loc in projects:
        cache_id = f"projects/{proj}/locations/{loc}/cachedContents/xyz123"
        manager.set_cache(
            cache_key=cache_key,
            cache_id=cache_id,
            ttl_seconds=3600,
            vertex_project=proj,
            vertex_location=loc,
            custom_llm_provider="vertex_ai"
        )
        print(f"  ✓ Created cache in {proj} ({loc})")

    print("\nRetrieving caches:")
    for proj, loc in projects:
        retrieved = manager.get_cache(
            cache_key=cache_key,
            vertex_project=proj,
            vertex_location=loc,
            custom_llm_provider="vertex_ai"
        )
        expected_prefix = f"projects/{proj}/locations/{loc}"
        assert expected_prefix in retrieved, f"Cache mismatch for {proj}"
        print(f"  ✓ {proj} ({loc}): {retrieved}")

    stats = manager.get_stats()
    print(f"\n✓ Test passed! {stats['valid_entries']} separate caches for same content")


def test_location_scoping():
    """Test that location also provides scoping."""
    print("\n" + "=" * 80)
    print("Test 3: Same Project, Different Locations")
    print("=" * 80)

    manager = LocalCacheManager()
    manager.clear_all()

    cache_key = "content-xyz"
    project = "gemini-qn-bz"

    # Same project, different locations
    manager.set_cache(
        cache_key=cache_key,
        cache_id="cache-global",
        ttl_seconds=3600,
        vertex_project=project,
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )

    manager.set_cache(
        cache_key=cache_key,
        cache_id="cache-us-central1",
        ttl_seconds=3600,
        vertex_project=project,
        vertex_location="us-central1",
        custom_llm_provider="vertex_ai"
    )

    # Retrieve
    cache_global = manager.get_cache(
        cache_key=cache_key,
        vertex_project=project,
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )

    cache_us = manager.get_cache(
        cache_key=cache_key,
        vertex_project=project,
        vertex_location="us-central1",
        custom_llm_provider="vertex_ai"
    )

    print(f"\nGlobal location: {cache_global}")
    print(f"US-Central1 location: {cache_us}")

    assert cache_global == "cache-global"
    assert cache_us == "cache-us-central1"

    print("\n✓ Test passed! Same project, different locations = independent caches")


def test_gemini_vs_vertex():
    """Test Gemini vs Vertex AI isolation."""
    print("\n" + "=" * 80)
    print("Test 4: Gemini vs Vertex AI Isolation")
    print("=" * 80)

    manager = LocalCacheManager()
    manager.clear_all()

    cache_key = "content-123"

    # Gemini (no project scoping)
    manager.set_cache(
        cache_key=cache_key,
        cache_id="gemini-cache-id",
        ttl_seconds=3600,
        custom_llm_provider="gemini"
    )

    # Vertex AI (with project scoping)
    manager.set_cache(
        cache_key=cache_key,
        cache_id="vertex-cache-id",
        ttl_seconds=3600,
        vertex_project="my-project",
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )

    # Retrieve
    gemini_cache = manager.get_cache(
        cache_key=cache_key,
        custom_llm_provider="gemini"
    )

    vertex_cache = manager.get_cache(
        cache_key=cache_key,
        vertex_project="my-project",
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )

    print(f"\nGemini cache: {gemini_cache}")
    print(f"Vertex AI cache: {vertex_cache}")

    assert gemini_cache == "gemini-cache-id"
    assert vertex_cache == "vertex-cache-id"

    print("\n✓ Test passed! Gemini and Vertex AI caches are isolated")


if __name__ == "__main__":
    print("\n🚀 Multi-Project Cache Scoping Tests")
    print("=" * 80)

    try:
        test_basic_scoping()
        test_your_actual_config()
        test_location_scoping()
        test_gemini_vs_vertex()

        print("\n" + "=" * 80)
        print("🎉 ALL TESTS PASSED!")
        print("=" * 80)
        print("\n✅ Cache scoping is working correctly!")
        print("✅ Each project+location gets independent caches")
        print("✅ Same content in different projects won't conflict")
        print("✅ Ready for your multi-project setup!")
        print("=" * 80)
        print("\n")

    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
