from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from litellm.llms.vertex_ai.context_caching.local_cache_manager import (
    LocalCacheManager,
)
from litellm.llms.vertex_ai.context_caching.vertex_ai_context_caching import (
    ContextCachingEndpoints,
    parse_expire_time_to_remaining_ttl,
    parse_ttl_to_seconds,
)


class FakeLogging:
    def pre_call(self, *args, **kwargs) -> None:
        return None

    def post_call(self, *args, **kwargs) -> None:
        return None


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                message=self.text,
                request=httpx.Request("GET", "https://example.com"),
                response=httpx.Response(self.status_code, text=self.text),
            )


class FakeClient:
    def __init__(self, responses: tuple[FakeResponse, ...]) -> None:
        self._responses = responses
        self.urls: list[str] = []

    def get(self, url: str, headers: Optional[dict] = None) -> FakeResponse:
        self.urls.append(url)
        return self._responses[len(self.urls) - 1]


def test_local_cache_is_scoped_by_project_location_and_provider() -> None:
    manager = LocalCacheManager(cleanup_interval_seconds=3600)
    try:
        manager.set_cache(
            cache_key="shared-content",
            cache_id="projects/project-a/locations/global/cachedContents/1",
            ttl_seconds=3600,
            vertex_project="project-a",
            vertex_location="global",
            custom_llm_provider="vertex_ai",
        )
        manager.set_cache(
            cache_key="shared-content",
            cache_id="projects/project-b/locations/global/cachedContents/2",
            ttl_seconds=3600,
            vertex_project="project-b",
            vertex_location="global",
            custom_llm_provider="vertex_ai",
        )
        manager.set_cache(
            cache_key="shared-content",
            cache_id="gemini-cache",
            ttl_seconds=3600,
            custom_llm_provider="gemini",
        )

        assert (
            manager.get_cache(
                "shared-content",
                vertex_project="project-a",
                vertex_location="global",
                custom_llm_provider="vertex_ai",
            )
            == "projects/project-a/locations/global/cachedContents/1"
        )
        assert (
            manager.get_cache(
                "shared-content",
                vertex_project="project-b",
                vertex_location="global",
                custom_llm_provider="vertex_ai",
            )
            == "projects/project-b/locations/global/cachedContents/2"
        )
        assert (
            manager.get_cache("shared-content", custom_llm_provider="gemini")
            == "gemini-cache"
        )
        assert (
            manager.get_cache(
                "shared-content",
                vertex_project="project-c",
                vertex_location="global",
                custom_llm_provider="vertex_ai",
            )
            is None
        )
    finally:
        manager.shutdown()


def test_local_cache_expires_and_cleans_entries() -> None:
    manager = LocalCacheManager(cleanup_interval_seconds=3600)
    try:
        manager.set_cache("expired", "cache-id", ttl_seconds=-1)

        assert manager.get_cache("expired") is None
        assert manager.get_stats()["total_entries"] == 0

        manager.set_cache("expired", "cache-id", ttl_seconds=-1)
        assert manager.cleanup_expired() == 1
        assert manager.get_stats()["valid_entries"] == 0
    finally:
        manager.shutdown()


def test_parse_ttl_and_expire_time() -> None:
    expire_time = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()

    assert parse_ttl_to_seconds("1.5s") == 1.5
    assert parse_ttl_to_seconds("invalid") == 3600.0
    assert parse_ttl_to_seconds(None) == 3600.0
    assert parse_expire_time_to_remaining_ttl(expire_time) is not None
    assert parse_expire_time_to_remaining_ttl("invalid") is None


def test_check_cache_paginates_and_backfills_local_cache() -> None:
    manager = LocalCacheManager(cleanup_interval_seconds=3600)
    endpoint = ContextCachingEndpoints(local_cache_manager=manager)
    expire_time = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
    client = FakeClient(
        responses=(
            FakeResponse(
                {
                    "cachedContents": [
                        {
                            "displayName": "other-key",
                            "name": "projects/p/locations/global/cachedContents/1",
                        }
                    ],
                    "nextPageToken": "page-2",
                }
            ),
            FakeResponse(
                {
                    "cachedContents": [
                        {
                            "displayName": "target-key",
                            "name": "projects/p/locations/global/cachedContents/2",
                            "expireTime": expire_time,
                        }
                    ]
                }
            ),
        )
    )

    try:
        cache_id = endpoint.check_cache(
            cache_key="target-key",
            client=client,  # type: ignore[arg-type]
            headers={},
            api_key="api-key",
            api_base=None,
            logging_obj=FakeLogging(),  # type: ignore[arg-type]
            custom_llm_provider="vertex_ai",
            vertex_project="project-a",
            vertex_location="global",
            vertex_auth_header="Bearer token",
            model="gemini-2.0-flash",
        )

        assert cache_id == "projects/p/locations/global/cachedContents/2"
        assert client.urls[1].endswith("?pageToken=page-2")
        assert (
            manager.get_cache(
                "target-key",
                vertex_project="project-a",
                vertex_location="global",
                custom_llm_provider="vertex_ai",
            )
            == "projects/p/locations/global/cachedContents/2"
        )
    finally:
        manager.shutdown()
