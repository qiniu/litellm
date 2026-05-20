# Vertex AI Context Caching with Local Cache Layer

This document consolidates the context caching notes from `bilibili-main` into one reference.

## What changed

`ContextCachingEndpoints` now uses a local in-memory cache manager before querying Google cache APIs:

1. Generate the cache key from cache-eligible prompt segments.
2. Try local cache first (scoped by provider/project/location).
3. On miss, query Google cached contents.
4. If not found remotely, create a new cache entry.
5. Write cache metadata back to local cache with TTL.

This flow applies to both sync and async paths.

## Why this helps

- Reduces repetitive remote lookup calls for hot prompts.
- Lowers latency for repeated requests that share stable cached segments.
- Keeps cache separation safe across projects/locations.
- Preserves fallback behavior by still querying Google on local miss.

## Local cache behavior

`LocalCacheManager` stores:

- key: scoped key derived from `cache_key + provider + project + location`
- value: remote cache id (`cachedContents/...`)
- metadata: created time, TTL, expire time

Characteristics:

- Thread-safe read/write via lock.
- Lazy expiry cleanup during reads.
- Periodic background cleanup thread.
- Singleton accessor (`get_cache_manager()`).

## TTL handling

TTL can come from:

- request `ttl` field (for newly created remote cache), or
- `expireTime` returned by Google (for discovered remote cache)

When remote `expireTime` exists, remaining TTL is computed and used for local storage.
If parsing fails or no value is available, default TTL is used.

## Scope rules

- `gemini` provider: base key scope.
- `vertex_ai` provider: key is additionally scoped by project + location.

This prevents accidental cache reuse across environments.

## Logging updates

All newly introduced cache flow logs were normalized to English:

- local cache hit/miss
- remote lookup/create request lifecycle
- TTL and expiry details
- sync/async flow visibility

## Operational notes

- Local cache is a performance optimization layer, not a source of truth.
- Remote cache remains authoritative.
- On process restart, local cache resets and rebuilds from traffic naturally.
- In multi-worker deployments, each worker has its own local cache state.
