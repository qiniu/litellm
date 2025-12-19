from typing import List, Literal, Optional, Tuple, Union
from datetime import datetime
import os

import httpx

import litellm
from litellm._logging import verbose_proxy_logger
from litellm.caching.caching import Cache, LiteLLMCacheType
from litellm.litellm_core_utils.litellm_logging import Logging
from litellm.llms.custom_httpx.http_handler import (
    AsyncHTTPHandler,
    HTTPHandler,
    get_async_httpx_client,
)
from litellm.llms.openai.openai import AllMessageValues
from litellm.types.llms.vertex_ai import (
    CachedContentListAllResponseBody,
    VertexAICachedContentResponseObject,
)

from ..common_utils import VertexAIError
from ..vertex_llm_base import VertexBase
from .transformation import (
    separate_cached_messages,
    transform_openai_messages_to_gemini_context_caching,
    extract_ttl_from_cached_messages,
)
from .local_cache_manager import get_cache_manager

local_cache_obj = Cache(
    type=LiteLLMCacheType.LOCAL
)  # only used for calling 'get_cache_key' function


def parse_ttl_to_seconds(ttl_str: Optional[str]) -> float:
    """
    Parse TTL string to seconds.

    Args:
        ttl_str: TTL string like "3600s", "1.5s"

    Returns:
        TTL in seconds as float, or default 3600.0 if invalid
    """
    if not ttl_str:
        return 3600.0  # Default 1 hour

    import re
    pattern = r'^([0-9]*\.?[0-9]+)s$'
    match = re.match(pattern, ttl_str)

    if not match:
        return 3600.0

    try:
        return float(match.group(1))
    except ValueError:
        return 3600.0


def parse_expire_time_to_remaining_ttl(expire_time_str: str) -> Optional[float]:
    """
    Parse expireTime string (ISO 8601 format) and calculate remaining TTL in seconds.

    Args:
        expire_time_str: ISO 8601 format string like "2014-10-02T15:01:23Z"

    Returns:
        Remaining TTL in seconds, or None if parsing fails or already expired
    """
    if not expire_time_str:
        return None

    try:
        # Handle both 'Z' and timezone offset formats
        if expire_time_str.endswith('Z'):
            expire_time_str = expire_time_str.replace('Z', '+00:00')

        expire_time = datetime.fromisoformat(expire_time_str)
        current_time = datetime.now(expire_time.tzinfo)
        remaining_seconds = (expire_time - current_time).total_seconds()

        # Return None if already expired
        if remaining_seconds <= 0:
            return None

        return remaining_seconds
    except (ValueError, AttributeError, TypeError):
        # Return None if parsing fails
        return None


class ContextCachingEndpoints(VertexBase):
    """
    Covers context caching endpoints for Vertex AI + Google AI Studio

    v0: covers Google AI Studio
    """

    def __init__(self) -> None:
        self.local_cache_manager = get_cache_manager()

    def _get_token_and_url_context_caching(
        self,
        gemini_api_key: Optional[str],
        custom_llm_provider: Literal["vertex_ai", "vertex_ai_beta", "gemini"],
        api_base: Optional[str],
        vertex_project: Optional[str],
        vertex_location: Optional[str],
        vertex_auth_header: Optional[str],
    ) -> Tuple[Optional[str], str]:
        """
        Internal function. Returns the token and url for the call.

        Handles logic if it's google ai studio vs. vertex ai.

        Returns
            token, url
        """
        if custom_llm_provider == "gemini":
            auth_header = None
            endpoint = "cachedContents"
            url = "https://generativelanguage.googleapis.com/v1beta/{}?key={}".format(
                endpoint, gemini_api_key
            )
        elif custom_llm_provider == "vertex_ai":
            auth_header = vertex_auth_header
            endpoint = "cachedContents"
            if vertex_location == "global":
                url = f"https://aiplatform.googleapis.com/v1/projects/{vertex_project}/locations/{vertex_location}/{endpoint}"
            else:
                url = f"https://{vertex_location}-aiplatform.googleapis.com/v1/projects/{vertex_project}/locations/{vertex_location}/{endpoint}"
        else:
            auth_header = vertex_auth_header
            endpoint = "cachedContents"
            if vertex_location == "global":
                url = f"https://aiplatform.googleapis.com/v1beta1/projects/{vertex_project}/locations/{vertex_location}/{endpoint}"
            else:
                url = f"https://{vertex_location}-aiplatform.googleapis.com/v1beta1/projects/{vertex_project}/locations/{vertex_location}/{endpoint}"


        return self._check_custom_proxy(
            api_base=api_base,
            custom_llm_provider=custom_llm_provider,
            gemini_api_key=gemini_api_key,
            endpoint=endpoint,
            stream=None,
            auth_header=auth_header,
            url=url,
            model=None,
            vertex_project=vertex_project,
            vertex_location=vertex_location,
            vertex_api_version="v1beta1" if custom_llm_provider == "vertex_ai_beta" else "v1",
        )

    def check_cache(
        self,
        cache_key: str,
        client: HTTPHandler,
        headers: dict,
        api_key: str,
        api_base: Optional[str],
        logging_obj: Logging,
        custom_llm_provider: Literal["vertex_ai", "vertex_ai_beta", "gemini"],
        vertex_project: Optional[str],
        vertex_location: Optional[str],
        vertex_auth_header: Optional[str],
    ) -> Optional[str]:
        """
        Checks if content already cached on Google API.

        Note: Local cache should be checked by caller first to avoid redundant network requests.

        Returns
        - cached_content_name - str - cached content name stored on google. (if found.)
        OR
        - None
        """
        # Check with Google API (local cache check is done by caller)
        _, url = self._get_token_and_url_context_caching(
            gemini_api_key=api_key,
            custom_llm_provider=custom_llm_provider,
            api_base=api_base,
            vertex_project=vertex_project,
            vertex_location=vertex_location,
            vertex_auth_header=vertex_auth_header
        )
        try:
            ## LOGGING
            logging_obj.pre_call(
                input="",
                api_key="",
                additional_args={
                    "complete_input_dict": {},
                    "api_base": url,
                    "headers": headers,
                },
            )

            verbose_proxy_logger.debug(
                f"🔍 请求Google API查询缓存 - cache_key={cache_key[:50]}..., "
                f"project={vertex_project}, location={vertex_location}, "
                f"provider={custom_llm_provider}, PID={os.getpid()}, url={url}"
            )

            resp = client.get(url=url, headers=headers)
            resp.raise_for_status()

            verbose_proxy_logger.debug(
                f"✅ Google API查询缓存响应成功 - cache_key={cache_key[:50]}..., "
                f"status_code={resp.status_code}, PID={os.getpid()}"
            )
        except httpx.HTTPStatusError as e:
            verbose_proxy_logger.error(
                f"❌ Google API查询缓存失败 - cache_key={cache_key[:50]}..., "
                f"status_code={e.response.status_code}, PID={os.getpid()}"
            )
            if e.response.status_code == 403:
                return None
            raise VertexAIError(
                status_code=e.response.status_code, message=e.response.text
            )
        except Exception as e:
            verbose_proxy_logger.error(
                f"❌ Google API查询缓存异常 - cache_key={cache_key[:50]}..., "
                f"error={str(e)}, PID={os.getpid()}"
            )
            raise VertexAIError(status_code=500, message=str(e))
        raw_response = resp.json()
        logging_obj.post_call(original_response=raw_response)

        if "cachedContents" not in raw_response:
            return None

        all_cached_items = CachedContentListAllResponseBody(**raw_response)

        if "cachedContents" not in all_cached_items:
            return None

        for cached_item in all_cached_items["cachedContents"]:
            display_name = cached_item.get("displayName")
            if display_name is not None and display_name == cache_key:
                cache_id = cached_item.get("name")
                expire_time = cached_item.get("expireTime")

                if cache_id:
                    # Calculate remaining TTL from expireTime
                    if expire_time:
                        remaining_ttl = parse_expire_time_to_remaining_ttl(expire_time)
                        if remaining_ttl is None:
                            # Already expired, don't store in local cache
                            verbose_proxy_logger.debug(
                                f"⚠️  Google API找到缓存但已过期 - "
                                f"cache_id={cache_id}, name={display_name[:30]}..., "
                                f"expire_time={expire_time}, PID={os.getpid()}"
                            )
                            return None
                        ttl_seconds = remaining_ttl
                        verbose_proxy_logger.debug(
                            f"✅ Google API找到缓存 - cache_id={cache_id}, "
                            f"name={display_name[:30]}..., expire_time={expire_time}, "
                            f"剩余TTL={ttl_seconds:.1f}秒, PID={os.getpid()}"
                        )
                    else:
                        # If no expireTime, use default TTL
                        ttl_seconds = 3600.0
                        verbose_proxy_logger.debug(
                            f"✅ Google API找到缓存(无过期时间) - cache_id={cache_id}, "
                            f"name={display_name[:30]}..., 使用默认TTL={ttl_seconds}秒, PID={os.getpid()}"
                        )

                    # Store in local cache with accurate TTL
                    verbose_proxy_logger.debug(
                        f"Vertex AI 上下文缓存: 正在存储缓存到本地 - "
                        f"cache_id={cache_id}, cache_key={cache_key[:50]}..., ttl={ttl_seconds:.1f}秒"
                    )
                    self.local_cache_manager.set_cache(
                        cache_key=cache_key,
                        cache_id=cache_id,
                        ttl_seconds=ttl_seconds,
                        vertex_project=vertex_project,
                        vertex_location=vertex_location,
                        custom_llm_provider=custom_llm_provider
                    )

                return cache_id

        verbose_proxy_logger.debug(
            f"❌ Google API未找到匹配缓存 - cache_key={cache_key[:50]}..., "
            f"总缓存项数={len(all_cached_items.get('cachedContents', []))}, PID={os.getpid()}"
        )
        return None

    async def async_check_cache(
        self,
        cache_key: str,
        client: AsyncHTTPHandler,
        headers: dict,
        api_key: str,
        api_base: Optional[str],
        logging_obj: Logging,
        custom_llm_provider: Literal["vertex_ai", "vertex_ai_beta", "gemini"],
        vertex_project: Optional[str],
        vertex_location: Optional[str],
        vertex_auth_header: Optional[str]
    ) -> Optional[str]:
        """
        Async version - checks if content already cached on Google API.

        Note: Local cache should be checked by caller first to avoid redundant network requests.

        Returns
        - cached_content_name - str - cached content name stored on google. (if found.)
        OR
        - None
        """
        # Check with Google API (local cache check is done by caller)
        _, url = self._get_token_and_url_context_caching(
            gemini_api_key=api_key,
            custom_llm_provider=custom_llm_provider,
            api_base=api_base,
            vertex_project=vertex_project,
            vertex_location=vertex_location,
            vertex_auth_header=vertex_auth_header
        )
        try:
            ## LOGGING
            logging_obj.pre_call(
                input="",
                api_key="",
                additional_args={
                    "complete_input_dict": {},
                    "api_base": url,
                    "headers": headers,
                },
            )

            verbose_proxy_logger.debug(
                f"🔍 [异步]请求Google API查询缓存 - cache_key={cache_key[:50]}..., "
                f"project={vertex_project}, location={vertex_location}, "
                f"provider={custom_llm_provider}, PID={os.getpid()}, url={url}"
            )

            resp = await client.get(url=url, headers=headers)
            resp.raise_for_status()

            verbose_proxy_logger.debug(
                f"✅ [异步]Google API查询缓存响应成功 - cache_key={cache_key[:50]}..., "
                f"status_code={resp.status_code}, PID={os.getpid()}"
            )
        except httpx.HTTPStatusError as e:
            verbose_proxy_logger.error(
                f"❌ [异步]Google API查询缓存失败 - cache_key={cache_key[:50]}..., "
                f"status_code={e.response.status_code}, PID={os.getpid()}"
            )
            if e.response.status_code == 403:
                return None
            raise VertexAIError(
                status_code=e.response.status_code, message=e.response.text
            )
        except Exception as e:
            verbose_proxy_logger.error(
                f"❌ [异步]Google API查询缓存异常 - cache_key={cache_key[:50]}..., "
                f"error={str(e)}, PID={os.getpid()}"
            )
            raise VertexAIError(status_code=500, message=str(e))
        raw_response = resp.json()
        logging_obj.post_call(original_response=raw_response)

        if "cachedContents" not in raw_response:
            return None

        all_cached_items = CachedContentListAllResponseBody(**raw_response)

        if "cachedContents" not in all_cached_items:
            return None

        for cached_item in all_cached_items["cachedContents"]:
            display_name = cached_item.get("displayName")
            if display_name is not None and display_name == cache_key:
                cache_id = cached_item.get("name")
                expire_time = cached_item.get("expireTime")

                if cache_id:
                    # Calculate remaining TTL from expireTime
                    if expire_time:
                        remaining_ttl = parse_expire_time_to_remaining_ttl(expire_time)
                        if remaining_ttl is None:
                            # Already expired, don't store in local cache
                            verbose_proxy_logger.debug(
                                f"⚠️  [异步]Google API找到缓存但已过期 - "
                                f"cache_id={cache_id}, name={display_name[:30]}..., "
                                f"expire_time={expire_time}, PID={os.getpid()}"
                            )
                            return None
                        ttl_seconds = remaining_ttl
                        verbose_proxy_logger.debug(
                            f"✅ [异步]Google API找到缓存 - cache_id={cache_id}, "
                            f"name={display_name[:30]}..., expire_time={expire_time}, "
                            f"剩余TTL={ttl_seconds:.1f}秒, PID={os.getpid()}"
                        )
                    else:
                        # If no expireTime, use default TTL
                        ttl_seconds = 3600.0
                        verbose_proxy_logger.debug(
                            f"✅ [异步]Google API找到缓存(无过期时间) - cache_id={cache_id}, "
                            f"name={display_name[:30]}..., 使用默认TTL={ttl_seconds}秒, PID={os.getpid()}"
                        )

                    # Store in local cache with accurate TTL
                    verbose_proxy_logger.debug(
                        f"Vertex AI 上下文缓存: [异步] 正在存储缓存到本地 - "
                        f"cache_id={cache_id}, cache_key={cache_key[:50]}..., ttl={ttl_seconds:.1f}秒"
                    )
                    self.local_cache_manager.set_cache(
                        cache_key=cache_key,
                        cache_id=cache_id,
                        ttl_seconds=ttl_seconds,
                        vertex_project=vertex_project,
                        vertex_location=vertex_location,
                        custom_llm_provider=custom_llm_provider
                    )

                return cache_id

        verbose_proxy_logger.error(
            f"❌ [异步]Google API未找到匹配缓存 - cache_key={cache_key[:50]}..., "
            f"总缓存项数={len(all_cached_items.get('cachedContents', []))}, PID={os.getpid()}"
        )
        return None

    def check_and_create_cache(
        self,
        messages: List[AllMessageValues],  # receives openai format messages
        optional_params: dict,  # cache the tools if present, in case cache content exists in messages
        api_key: str,
        api_base: Optional[str],
        model: str,
        client: Optional[HTTPHandler],
        timeout: Optional[Union[float, httpx.Timeout]],
        logging_obj: Logging,
        custom_llm_provider: Literal["vertex_ai", "vertex_ai_beta", "gemini"],
        vertex_project: Optional[str],
        vertex_location: Optional[str],
        vertex_auth_header: Optional[str],
        extra_headers: Optional[dict] = None,
        cached_content: Optional[str] = None,
    ) -> Tuple[List[AllMessageValues], dict, Optional[str]]:
        """
        Receives
        - messages: List of dict - messages in the openai format

        Returns
        - messages - List[dict] - filtered list of messages in the openai format.
        - cached_content - str - the cache content id, to be passed in the gemini request body

        Follows - https://ai.google.dev/api/caching#request-body
        """
        verbose_proxy_logger.debug(
            f"🚀 [TEST] check_and_create_cache 被调用 - model={model}, "
            f"provider={custom_llm_provider}, messages_count={len(messages)}"
        )
        if cached_content is not None:
            verbose_proxy_logger.debug(
                f"Vertex AI 上下文缓存: 使用已提供的 cached_content={cached_content}, "
                f"跳过缓存处理"
            )
            return messages, optional_params, cached_content

        cached_messages, non_cached_messages = separate_cached_messages(
            messages=messages
        )

        verbose_proxy_logger.debug(
            f"Vertex AI 上下文缓存: 消息分离完成 - "
            f"缓存消息={len(cached_messages)}, 非缓存消息={len(non_cached_messages)}"
        )

        if len(cached_messages) == 0:
            verbose_proxy_logger.debug(
                "Vertex AI 上下文缓存: 没有找到缓存消息，跳过缓存处理"
            )
            return messages, optional_params, None

        tools = optional_params.pop("tools", None)

        ## AUTHORIZATION ##
        token, url = self._get_token_and_url_context_caching(
            gemini_api_key=api_key,
            custom_llm_provider=custom_llm_provider,
            api_base=api_base,
            vertex_project=vertex_project,
            vertex_location=vertex_location,
            vertex_auth_header=vertex_auth_header
        )

        headers = {
            "Content-Type": "application/json",
        }
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if extra_headers is not None:
            headers.update(extra_headers)

        if client is None or not isinstance(client, HTTPHandler):
            _params = {}
            if timeout is not None:
                if isinstance(timeout, float) or isinstance(timeout, int):
                    timeout = httpx.Timeout(timeout)
                _params["timeout"] = timeout
            client = HTTPHandler(**_params)  # type: ignore
        else:
            client = client

        ## Generate cache key
        generated_cache_key = local_cache_obj.get_cache_key(
            messages=cached_messages, tools=tools
        )
        verbose_proxy_logger.debug(
            f"Vertex AI 上下文缓存: 生成缓存键 cache_key={generated_cache_key[:50]}... "
            f"(项目={vertex_project}, 区域={vertex_location}, 提供商={custom_llm_provider})"
        )

        # OPTIMIZATION: Check local cache first (no network call, with project/location scope)
        local_cache_id = self.local_cache_manager.get_cache(
            cache_key=generated_cache_key,
            vertex_project=vertex_project,
            vertex_location=vertex_location,
            custom_llm_provider=custom_llm_provider
        )
        if local_cache_id is not None:
            # Found valid cache locally, return immediately
            verbose_proxy_logger.debug(
                f"Vertex AI 上下文缓存: ✅ 本地缓存命中 - cache_id={local_cache_id}, "
                f"cache_key={generated_cache_key[:50]}..."
            )
            return non_cached_messages, optional_params, local_cache_id

        verbose_proxy_logger.debug(
            f"Vertex AI 上下文缓存: ❌ 本地缓存未命中 - cache_key={generated_cache_key[:50]}..., "
            f"正在查询 Google API..."
        )

        ## CHECK IF CACHED ON GOOGLE (network call, but only if not in local cache)
        verbose_proxy_logger.debug(
            f"Vertex AI 上下文缓存: 查询 Google API 缓存 cache_key={generated_cache_key[:50]}..."
        )
        google_cache_name = self.check_cache(
            cache_key=generated_cache_key,
            client=client,
            headers=headers,
            api_key=api_key,
            api_base=api_base,
            logging_obj=logging_obj,
            custom_llm_provider=custom_llm_provider,
            vertex_project=vertex_project,
            vertex_location=vertex_location,
            vertex_auth_header=vertex_auth_header
        )
        if google_cache_name:
            verbose_proxy_logger.debug(
                f"Vertex AI 上下文缓存: ✅ Google API 缓存命中 - cache_id={google_cache_name}, "
                f"cache_key={generated_cache_key[:50]}..."
            )
            return non_cached_messages, optional_params, google_cache_name

        verbose_proxy_logger.debug(
            f"Vertex AI 上下文缓存: ❌ Google API 未找到缓存 - cache_key={generated_cache_key[:50]}..., "
            f"正在创建新缓存..."
        )

        ## TRANSFORM REQUEST
        cached_content_request_body = (
            transform_openai_messages_to_gemini_context_caching(
                model=model,
                messages=cached_messages,
                cache_key=generated_cache_key,
                custom_llm_provider=custom_llm_provider,
                vertex_project=vertex_project,
                vertex_location=vertex_location,
            )
        )

        cached_content_request_body["tools"] = tools

        ## LOGGING
        logging_obj.pre_call(
            input=messages,
            api_key="",
            additional_args={
                "complete_input_dict": cached_content_request_body,
                "api_base": url,
                "headers": headers,
            },
        )

        verbose_proxy_logger.debug(
            f"🚀 请求Google API创建新缓存 - cache_key={generated_cache_key[:50]}..., "
            f"project={vertex_project}, location={vertex_location}, "
            f"provider={custom_llm_provider}, PID={os.getpid()}, url={url}"
        )

        try:
            response = client.post(
                url=url, headers=headers, json=cached_content_request_body  # type: ignore
            )
            response.raise_for_status()

            verbose_proxy_logger.debug(
                f"✅ Google API创建缓存响应成功 - cache_key={generated_cache_key[:50]}..., "
                f"status_code={response.status_code}, PID={os.getpid()}"
            )
        except httpx.HTTPStatusError as err:
            error_code = err.response.status_code
            verbose_proxy_logger.error(
                f"❌ Google API创建缓存失败 - cache_key={generated_cache_key[:50]}..., "
                f"status_code={error_code}, PID={os.getpid()}, "
                f"错误={err.response.text[:200]}"
            )
            raise VertexAIError(status_code=error_code, message=err.response.text)
        except httpx.TimeoutException:
            verbose_proxy_logger.error(
                f"❌ Google API创建缓存超时 - cache_key={generated_cache_key[:50]}..., "
                f"PID={os.getpid()}"
            )
            raise VertexAIError(status_code=408, message="Timeout error occurred.")

        raw_response_cached = response.json()
        cached_content_response_obj = VertexAICachedContentResponseObject(
            name=raw_response_cached.get("name"), model=raw_response_cached.get("model")
        )

        cache_id = cached_content_response_obj["name"]

        # OPTIMIZATION: Store newly created cache in local cache manager (with project/location scope)
        # Extract TTL from the request body
        ttl_str = cached_content_request_body.get("ttl")
        if ttl_str:
            ttl_seconds = parse_ttl_to_seconds(ttl_str)
        else:
            # Extract from messages if not in request body
            ttl_str_from_messages = extract_ttl_from_cached_messages(cached_messages)
            ttl_seconds = parse_ttl_to_seconds(ttl_str_from_messages) if ttl_str_from_messages else 3600.0

        verbose_proxy_logger.debug(
            f"Vertex AI 上下文缓存: ✅ 成功创建新缓存 - cache_id={cache_id}, "
            f"cache_key={generated_cache_key[:50]}..., ttl={ttl_seconds}s, "
            f"正在存储到本地缓存..."
        )

        self.local_cache_manager.set_cache(
            cache_key=generated_cache_key,
            cache_id=cache_id,
            ttl_seconds=ttl_seconds,
            vertex_project=vertex_project,
            vertex_location=vertex_location,
            custom_llm_provider=custom_llm_provider
        )

        return (
            non_cached_messages,
            optional_params,
            cache_id,
        )

    async def async_check_and_create_cache(
        self,
        messages: List[AllMessageValues],  # receives openai format messages
        optional_params: dict,  # cache the tools if present, in case cache content exists in messages
        api_key: str,
        api_base: Optional[str],
        model: str,
        client: Optional[AsyncHTTPHandler],
        timeout: Optional[Union[float, httpx.Timeout]],
        logging_obj: Logging,
        custom_llm_provider: Literal["vertex_ai", "vertex_ai_beta", "gemini"],
        vertex_project: Optional[str],
        vertex_location: Optional[str],
        vertex_auth_header: Optional[str],
        extra_headers: Optional[dict] = None,
        cached_content: Optional[str] = None,
    ) -> Tuple[List[AllMessageValues], dict, Optional[str]]:
        """
        Receives
        - messages: List of dict - messages in the openai format

        Returns
        - messages - List[dict] - filtered list of messages in the openai format.
        - cached_content - str - the cache content id, to be passed in the gemini request body

        Follows - https://ai.google.dev/api/caching#request-body
        """
        verbose_proxy_logger.debug(
            f"🚀 [异步] async_check_and_create_cache 被调用 - model={model}, "
            f"provider={custom_llm_provider}, messages_count={len(messages)}"
        )
        if cached_content is not None:
            verbose_proxy_logger.debug(
                f"Vertex AI 上下文缓存: [异步] 使用已提供的 cached_content={cached_content}, "
                f"跳过缓存处理"
            )
            return messages, optional_params, cached_content

        cached_messages, non_cached_messages = separate_cached_messages(
            messages=messages
        )

        verbose_proxy_logger.debug(
            f"Vertex AI 上下文缓存: [异步] 消息分离完成 - "
            f"缓存消息={len(cached_messages)}, 非缓存消息={len(non_cached_messages)}"
        )

        if len(cached_messages) == 0:
            verbose_proxy_logger.debug(
                "Vertex AI 上下文缓存: [异步] 没有找到缓存消息，跳过缓存处理"
            )
            return messages, optional_params, None

        tools = optional_params.pop("tools", None)

        ## AUTHORIZATION ##
        token, url = self._get_token_and_url_context_caching(
            gemini_api_key=api_key,
            custom_llm_provider=custom_llm_provider,
            api_base=api_base,
            vertex_project=vertex_project,
            vertex_location=vertex_location,
            vertex_auth_header=vertex_auth_header
        )

        headers = {
            "Content-Type": "application/json",
        }
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if extra_headers is not None:
            headers.update(extra_headers)

        if client is None or not isinstance(client, AsyncHTTPHandler):
            client = get_async_httpx_client(
                params={"timeout": timeout}, llm_provider=litellm.LlmProviders.VERTEX_AI
            )
        else:
            client = client

        ## Generate cache key
        generated_cache_key = local_cache_obj.get_cache_key(
            messages=cached_messages, tools=tools
        )
        verbose_proxy_logger.debug(
            f"Vertex AI 上下文缓存: [异步] 生成缓存键 cache_key={generated_cache_key[:50]}... "
            f"(项目={vertex_project}, 区域={vertex_location}, 提供商={custom_llm_provider})"
        )

        # OPTIMIZATION: Check local cache first (with project/location scope)
        local_cache_id = self.local_cache_manager.get_cache(
            cache_key=generated_cache_key,
            vertex_project=vertex_project,
            vertex_location=vertex_location,
            custom_llm_provider=custom_llm_provider
        )
        if local_cache_id is not None:
            # Found valid cache locally, return immediately
            verbose_proxy_logger.debug(
                f"Vertex AI 上下文缓存: ✅ [异步] 本地缓存命中 - cache_id={local_cache_id}, "
                f"cache_key={generated_cache_key[:50]}..."
            )
            return non_cached_messages, optional_params, local_cache_id

        verbose_proxy_logger.debug(
            f"Vertex AI 上下文缓存: ❌ [异步] 本地缓存未命中 - cache_key={generated_cache_key[:50]}..., "
            f"正在查询 Google API..."
        )

        ## CHECK IF CACHED ON GOOGLE
        verbose_proxy_logger.debug(
            f"Vertex AI 上下文缓存: [异步] 查询 Google API 缓存 cache_key={generated_cache_key[:50]}..."
        )
        google_cache_name = await self.async_check_cache(
            cache_key=generated_cache_key,
            client=client,
            headers=headers,
            api_key=api_key,
            api_base=api_base,
            logging_obj=logging_obj,
            custom_llm_provider=custom_llm_provider,
            vertex_project=vertex_project,
            vertex_location=vertex_location,
            vertex_auth_header=vertex_auth_header
        )

        if google_cache_name:
            verbose_proxy_logger.debug(
                f"Vertex AI 上下文缓存: ✅ [异步] Google API 缓存命中 - cache_id={google_cache_name}, "
                f"cache_key={generated_cache_key[:50]}..."
            )
            return non_cached_messages, optional_params, google_cache_name

        verbose_proxy_logger.debug(
            f"Vertex AI 上下文缓存: ❌ [异步] Google API 未找到缓存 - cache_key={generated_cache_key[:50]}..., "
            f"正在创建新缓存..."
        )

        ## TRANSFORM REQUEST
        cached_content_request_body = (
            transform_openai_messages_to_gemini_context_caching(
                model=model,
                messages=cached_messages,
                cache_key=generated_cache_key,
                custom_llm_provider=custom_llm_provider,
                vertex_project=vertex_project,
                vertex_location=vertex_location,
            )
        )

        cached_content_request_body["tools"] = tools

        ## LOGGING
        logging_obj.pre_call(
            input=messages,
            api_key="",
            additional_args={
                "complete_input_dict": cached_content_request_body,
                "api_base": url,
                "headers": headers,
            },
        )

        verbose_proxy_logger.debug(
            f"🚀 [异步]请求Google API创建新缓存 - cache_key={generated_cache_key[:50]}..., "
            f"project={vertex_project}, location={vertex_location}, "
            f"provider={custom_llm_provider}, PID={os.getpid()}, url={url}"
        )

        try:
            response = await client.post(
                url=url, headers=headers, json=cached_content_request_body  # type: ignore
            )
            response.raise_for_status()

            verbose_proxy_logger.debug(
                f"✅ [异步]Google API创建缓存响应成功 - cache_key={generated_cache_key[:50]}..., "
                f"status_code={response.status_code}, PID={os.getpid()}"
            )
        except httpx.HTTPStatusError as err:
            error_code = err.response.status_code
            verbose_proxy_logger.error(
                f"❌ [异步]Google API创建缓存失败 - cache_key={generated_cache_key[:50]}..., "
                f"status_code={error_code}, PID={os.getpid()}, "
                f"错误={err.response.text[:200]}"
            )
            raise VertexAIError(status_code=error_code, message=err.response.text)
        except httpx.TimeoutException:
            verbose_proxy_logger.error(
                f"❌ [异步]Google API创建缓存超时 - cache_key={generated_cache_key[:50]}..., "
                f"PID={os.getpid()}"
            )
            raise VertexAIError(status_code=408, message="Timeout error occurred.")

        raw_response_cached = response.json()
        cached_content_response_obj = VertexAICachedContentResponseObject(
            name=raw_response_cached.get("name"), model=raw_response_cached.get("model")
        )

        cache_id = cached_content_response_obj["name"]

        # OPTIMIZATION: Store in local cache (with project/location scope)
        ttl_str = cached_content_request_body.get("ttl")
        if ttl_str:
            ttl_seconds = parse_ttl_to_seconds(ttl_str)
        else:
            ttl_str_from_messages = extract_ttl_from_cached_messages(cached_messages)
            ttl_seconds = parse_ttl_to_seconds(ttl_str_from_messages) if ttl_str_from_messages else 3600.0

        verbose_proxy_logger.debug(
            f"Vertex AI 上下文缓存: ✅ [异步] 成功创建新缓存 - cache_id={cache_id}, "
            f"cache_key={generated_cache_key[:50]}..., ttl={ttl_seconds}s, "
            f"正在存储到本地缓存..."
        )

        self.local_cache_manager.set_cache(
            cache_key=generated_cache_key,
            cache_id=cache_id,
            ttl_seconds=ttl_seconds,
            vertex_project=vertex_project,
            vertex_location=vertex_location,
            custom_llm_provider=custom_llm_provider
        )

        return (
            non_cached_messages,
            optional_params,
            cache_id,
        )

    def get_cache(self):
        pass

    async def async_get_cache(self):
        pass
