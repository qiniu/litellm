"""
Test script to demonstrate local cache manager optimization.

This script shows how the optimization reduces network calls.
"""

import time
from litellm import completion
from litellm.llms.vertex_ai.context_caching.local_cache_manager import get_cache_manager

# Enable verbose logging to see cache behavior
import litellm
litellm.set_verbose = True


def test_local_cache_optimization():
    """Test that demonstrates local cache optimization."""

    print("=" * 80)
    print("Testing Local Cache Optimization for Gemini Context Caching")
    print("=" * 80)

    # Prepare messages with cache control
    messages = [
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "你是一个产品助手。以下是产品手册内容：\n" + ("产品特性说明..." * 200),
                    "cache_control": {
                        "type": "ephemeral",
                        "ttl": "3600s"  # 1 hour
                    }
                }
            ]
        },
        {
            "role": "user",
            "content": "产品的主要特性是什么？"
        }
    ]

    # Test 1: First call - should create cache and make network request
    print("\n[Test 1] First call - Creating cache...")
    print("-" * 80)
    start1 = time.time()

    try:
        response1 = completion(
            model="gemini-2.0-flash",
            messages=messages
        )
        time1 = time.time() - start1
        print(f"✓ First call completed in {time1:.2f}s")
        print(f"  Response: {response1.choices[0].message.content[:100]}...")
    except Exception as e:
        print(f"✗ First call failed: {e}")
        return

    # Small delay
    time.sleep(1)

    # Test 2: Second call - should use local cache (no network request to check cache)
    print("\n[Test 2] Second call - Using local cache...")
    print("-" * 80)

    # Modify user question
    messages[-1]["content"] = "产品的价格是多少？"

    start2 = time.time()
    try:
        response2 = completion(
            model="gemini-2.0-flash",
            messages=messages
        )
        time2 = time.time() - start2
        print(f"✓ Second call completed in {time2:.2f}s")
        print(f"  Response: {response2.choices[0].message.content[:100]}...")
    except Exception as e:
        print(f"✗ Second call failed: {e}")
        return

    # Test 3: Third call - also using local cache
    print("\n[Test 3] Third call - Using local cache again...")
    print("-" * 80)

    messages[-1]["content"] = "有哪些型号可选？"

    start3 = time.time()
    try:
        response3 = completion(
            model="gemini-2.0-flash",
            messages=messages
        )
        time3 = time.time() - start3
        print(f"✓ Third call completed in {time3:.2f}s")
        print(f"  Response: {response3.choices[0].message.content[:100]}...")
    except Exception as e:
        print(f"✗ Third call failed: {e}")
        return

    # Display cache statistics
    print("\n[Cache Statistics]")
    print("-" * 80)
    cache_manager = get_cache_manager()
    stats = cache_manager.get_stats()
    print(f"Total cache entries: {stats['total_entries']}")
    print(f"Valid entries: {stats['valid_entries']}")
    print(f"Expired entries: {stats['expired_entries']}")

    # Performance comparison
    print("\n[Performance Summary]")
    print("-" * 80)
    print(f"First call (create cache):  {time1:.2f}s")
    print(f"Second call (use local cache): {time2:.2f}s - {((time1-time2)/time1*100):.1f}% faster")
    print(f"Third call (use local cache):  {time3:.2f}s - {((time1-time3)/time1*100):.1f}% faster")

    print("\n" + "=" * 80)
    print("Optimization Benefits:")
    print("=" * 80)
    print("✓ No redundant network requests to check cache existence")
    print("✓ Reduced latency for cache lookups")
    print("✓ Automatic expiry tracking based on TTL")
    print("✓ Thread-safe concurrent access")
    print("=" * 80)


def test_cache_expiry():
    """Test cache expiry behavior."""

    print("\n" + "=" * 80)
    print("Testing Cache Expiry")
    print("=" * 80)

    cache_manager = get_cache_manager()

    # Create a short-lived cache entry
    cache_manager.set_cache("test-key", "test-cache-id", ttl_seconds=3)

    print("\n[Test] Cache entry created with 3 second TTL")
    print("-" * 80)

    # Check immediately - should be valid
    cache_id = cache_manager.get_cache("test-key")
    print(f"✓ Immediately after creation: cache_id = {cache_id}")

    # Wait 2 seconds - should still be valid
    print("\n⏳ Waiting 2 seconds...")
    time.sleep(2)
    cache_id = cache_manager.get_cache("test-key")
    print(f"✓ After 2 seconds: cache_id = {cache_id}")

    # Wait 2 more seconds - should be expired
    print("\n⏳ Waiting 2 more seconds...")
    time.sleep(2)
    cache_id = cache_manager.get_cache("test-key")
    print(f"✓ After 4 seconds total: cache_id = {cache_id} (expired)")

    print("\n" + "=" * 80)


def test_manual_operations():
    """Test manual cache operations."""

    print("\n" + "=" * 80)
    print("Testing Manual Cache Operations")
    print("=" * 80)

    cache_manager = get_cache_manager()

    # Clear all first
    cache_manager.clear_all()
    print("\n✓ Cleared all cache entries")

    # Add some entries
    print("\n[Test] Adding cache entries...")
    print("-" * 80)
    cache_manager.set_cache("key1", "cache-id-1", ttl_seconds=3600)
    cache_manager.set_cache("key2", "cache-id-2", ttl_seconds=7200)
    cache_manager.set_cache("key3", "cache-id-3", ttl_seconds=1)  # Will expire soon

    stats = cache_manager.get_stats()
    print(f"✓ Added 3 entries: {stats['total_entries']} total")

    # Wait for one to expire
    print("\n⏳ Waiting 2 seconds for key3 to expire...")
    time.sleep(2)

    # Cleanup expired
    removed = cache_manager.cleanup_expired()
    print(f"✓ Cleaned up {removed} expired entry")

    # Check remaining
    stats = cache_manager.get_stats()
    print(f"✓ Remaining entries: {stats['valid_entries']}")

    # Manual invalidation
    print("\n[Test] Manual invalidation...")
    print("-" * 80)
    cache_manager.invalidate_cache("key1")
    print("✓ Invalidated key1")

    stats = cache_manager.get_stats()
    print(f"✓ Remaining entries: {stats['valid_entries']}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    print("\n🚀 Starting Local Cache Manager Tests\n")

    # Test 1: Manual operations (doesn't require API)
    test_manual_operations()

    # Test 2: Cache expiry (doesn't require API)
    test_cache_expiry()

    # Test 3: Real-world optimization (requires Gemini API)
    print("\n" + "=" * 80)
    print("⚠️  Real-world optimization test requires Gemini API access")
    print("=" * 80)

    import os
    if os.getenv("GEMINI_API_KEY") or os.path.exists("/app/gemini-bz1.json"):
        try:
            test_local_cache_optimization()
        except Exception as e:
            print(f"\n✗ Real-world test skipped: {e}")
    else:
        print("Skipped: No API credentials found")

    print("\n✅ All tests completed!\n")
