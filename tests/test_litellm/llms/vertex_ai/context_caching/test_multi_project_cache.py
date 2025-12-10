"""
Test multi-project cache isolation.

This script demonstrates that caches are properly isolated between different
Vertex AI projects and locations.
"""

from litellm.llms.vertex_ai.context_caching.local_cache_manager import get_cache_manager


def test_multi_project_isolation():
    """Test that caches are isolated between different projects."""

    print("=" * 80)
    print("Testing Multi-Project Cache Isolation")
    print("=" * 80)

    cache_manager = get_cache_manager()
    cache_manager.clear_all()  # Start fresh

    # Same content, different projects
    cache_key = "content-hash-abc123"

    # Project 1
    cache_manager.set_cache(
        cache_key=cache_key,
        cache_id="projects/project-1/locations/global/cachedContents/cache-1",
        ttl_seconds=3600,
        vertex_project="project-1",
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )

    # Project 2 - same content, different project
    cache_manager.set_cache(
        cache_key=cache_key,
        cache_id="projects/project-2/locations/global/cachedContents/cache-2",
        ttl_seconds=3600,
        vertex_project="project-2",
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )

    # Project 3 - same project as 1, different location
    cache_manager.set_cache(
        cache_key=cache_key,
        cache_id="projects/project-1/locations/us-central1/cachedContents/cache-3",
        ttl_seconds=3600,
        vertex_project="project-1",
        vertex_location="us-central1",
        custom_llm_provider="vertex_ai"
    )

    print("\n[Test 1] Retrieve cache for Project 1, global")
    print("-" * 80)
    cache_1 = cache_manager.get_cache(
        cache_key=cache_key,
        vertex_project="project-1",
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )
    print(f"✓ Found: {cache_1}")
    assert cache_1 == "projects/project-1/locations/global/cachedContents/cache-1"

    print("\n[Test 2] Retrieve cache for Project 2, global")
    print("-" * 80)
    cache_2 = cache_manager.get_cache(
        cache_key=cache_key,
        vertex_project="project-2",
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )
    print(f"✓ Found: {cache_2}")
    assert cache_2 == "projects/project-2/locations/global/cachedContents/cache-2"

    print("\n[Test 3] Retrieve cache for Project 1, us-central1")
    print("-" * 80)
    cache_3 = cache_manager.get_cache(
        cache_key=cache_key,
        vertex_project="project-1",
        vertex_location="us-central1",
        custom_llm_provider="vertex_ai"
    )
    print(f"✓ Found: {cache_3}")
    assert cache_3 == "projects/project-1/locations/us-central1/cachedContents/cache-3"

    print("\n[Test 4] Query non-existent project")
    print("-" * 80)
    cache_4 = cache_manager.get_cache(
        cache_key=cache_key,
        vertex_project="project-999",
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )
    print(f"✓ Not found (expected): {cache_4}")
    assert cache_4 is None

    # Show statistics
    stats = cache_manager.get_stats()
    print("\n[Cache Statistics]")
    print("-" * 80)
    print(f"Total entries: {stats['total_entries']}")
    print(f"Valid entries: {stats['valid_entries']}")
    print(f"Cache keys (showing first 3):")
    for key in list(stats['cache_keys'])[:3]:
        print(f"  - {key}")

    print("\n" + "=" * 80)
    print("✅ All isolation tests passed!")
    print("=" * 80)
    print("\nKey Insight:")
    print("  Same content (cache_key) can exist in multiple projects/locations")
    print("  Each combination is treated as a separate cache entry")
    print("=" * 80)


def test_gemini_vs_vertex_ai():
    """Test that Google AI Studio (gemini) and Vertex AI are separate."""

    print("\n" + "=" * 80)
    print("Testing Gemini vs Vertex AI Isolation")
    print("=" * 80)

    cache_manager = get_cache_manager()
    cache_manager.clear_all()

    cache_key = "content-hash-xyz789"

    # Google AI Studio (no project scoping)
    cache_manager.set_cache(
        cache_key=cache_key,
        cache_id="gemini-cache-id-123",
        ttl_seconds=3600,
        vertex_project=None,
        vertex_location=None,
        custom_llm_provider="gemini"
    )

    # Vertex AI (with project scoping)
    cache_manager.set_cache(
        cache_key=cache_key,
        cache_id="projects/my-project/locations/global/cachedContents/456",
        ttl_seconds=3600,
        vertex_project="my-project",
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )

    print("\n[Test 1] Retrieve Gemini cache")
    print("-" * 80)
    gemini_cache = cache_manager.get_cache(
        cache_key=cache_key,
        custom_llm_provider="gemini"
    )
    print(f"✓ Found: {gemini_cache}")
    assert gemini_cache == "gemini-cache-id-123"

    print("\n[Test 2] Retrieve Vertex AI cache")
    print("-" * 80)
    vertex_cache = cache_manager.get_cache(
        cache_key=cache_key,
        vertex_project="my-project",
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )
    print(f"✓ Found: {vertex_cache}")
    assert vertex_cache == "projects/my-project/locations/global/cachedContents/456"

    print("\n" + "=" * 80)
    print("✅ Provider isolation test passed!")
    print("=" * 80)


def test_realistic_multi_project_scenario():
    """Test a realistic scenario with multiple projects."""

    print("\n" + "=" * 80)
    print("Realistic Multi-Project Scenario")
    print("=" * 80)

    cache_manager = get_cache_manager()
    cache_manager.clear_all()

    # Simulate your actual configuration
    projects = [
        {
            "name": "gemini-qn-bz",
            "location": "global",
            "content": "Document A content",
            "cache_id": "projects/gemini-qn-bz/locations/global/cachedContents/111"
        },
        {
            "name": "gemini-prod",
            "location": "us-central1",
            "content": "Document A content",  # Same content!
            "cache_id": "projects/gemini-prod/locations/us-central1/cachedContents/222"
        },
        {
            "name": "gemini-dev",
            "location": "global",
            "content": "Document A content",  # Same content!
            "cache_id": "projects/gemini-dev/locations/global/cachedContents/333"
        }
    ]

    print("\n[Setup] Creating caches for 3 projects with same content")
    print("-" * 80)

    for proj in projects:
        # In real scenario, cache_key would be a hash of content
        cache_key = f"hash-{hash(proj['content'])}"

        cache_manager.set_cache(
            cache_key=cache_key,
            cache_id=proj['cache_id'],
            ttl_seconds=3600,
            vertex_project=proj['name'],
            vertex_location=proj['location'],
            custom_llm_provider="vertex_ai"
        )
        print(f"✓ Cached in {proj['name']} ({proj['location']})")

    print("\n[Test] Retrieving caches for each project")
    print("-" * 80)

    for proj in projects:
        cache_key = f"hash-{hash(proj['content'])}"

        retrieved = cache_manager.get_cache(
            cache_key=cache_key,
            vertex_project=proj['name'],
            vertex_location=proj['location'],
            custom_llm_provider="vertex_ai"
        )

        print(f"✓ {proj['name']} ({proj['location']}): {retrieved}")
        assert retrieved == proj['cache_id'], f"Expected {proj['cache_id']}, got {retrieved}"

    stats = cache_manager.get_stats()
    print(f"\n📊 Total independent cache entries: {stats['valid_entries']}")

    print("\n" + "=" * 80)
    print("✅ Realistic scenario test passed!")
    print("=" * 80)
    print("\nWhat this means:")
    print("  - Each project maintains its own cache space")
    print("  - Same content in different projects = different caches")
    print("  - No cross-project cache contamination")
    print("  - Each cache tracked independently with its own TTL")
    print("=" * 80)


def test_invalidation_scoping():
    """Test that invalidation is properly scoped."""

    print("\n" + "=" * 80)
    print("Testing Scoped Cache Invalidation")
    print("=" * 80)

    cache_manager = get_cache_manager()
    cache_manager.clear_all()

    cache_key = "shared-content-hash"

    # Add caches for 2 projects
    cache_manager.set_cache(
        cache_key=cache_key,
        cache_id="cache-proj1",
        ttl_seconds=3600,
        vertex_project="project-1",
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )

    cache_manager.set_cache(
        cache_key=cache_key,
        cache_id="cache-proj2",
        ttl_seconds=3600,
        vertex_project="project-2",
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )

    print("\n[Test 1] Both caches exist initially")
    print("-" * 80)
    cache1 = cache_manager.get_cache(
        cache_key=cache_key,
        vertex_project="project-1",
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )
    cache2 = cache_manager.get_cache(
        cache_key=cache_key,
        vertex_project="project-2",
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )
    print(f"✓ Project 1: {cache1}")
    print(f"✓ Project 2: {cache2}")
    assert cache1 and cache2

    print("\n[Test 2] Invalidate project-1 cache only")
    print("-" * 80)
    cache_manager.invalidate_cache(
        cache_key=cache_key,
        vertex_project="project-1",
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )

    cache1_after = cache_manager.get_cache(
        cache_key=cache_key,
        vertex_project="project-1",
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )
    cache2_after = cache_manager.get_cache(
        cache_key=cache_key,
        vertex_project="project-2",
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )

    print(f"✓ Project 1 after invalidation: {cache1_after} (should be None)")
    print(f"✓ Project 2 after invalidation: {cache2_after} (should still exist)")

    assert cache1_after is None, "Project 1 cache should be invalidated"
    assert cache2_after is not None, "Project 2 cache should still exist"

    print("\n" + "=" * 80)
    print("✅ Scoped invalidation test passed!")
    print("=" * 80)


if __name__ == "__main__":
    print("\n🚀 Starting Multi-Project Cache Isolation Tests\n")

    test_multi_project_isolation()
    test_gemini_vs_vertex_ai()
    test_realistic_multi_project_scenario()
    test_invalidation_scoping()

    print("\n" + "=" * 80)
    print("🎉 ALL MULTI-PROJECT TESTS PASSED!")
    print("=" * 80)
    print("\nSummary:")
    print("  ✅ Caches properly isolated by project + location")
    print("  ✅ Same content in different projects = independent caches")
    print("  ✅ Gemini and Vertex AI caches are separate")
    print("  ✅ Invalidation respects project/location scope")
    print("  ✅ Ready for production multi-project setups")
    print("=" * 80)
    print("\n")
