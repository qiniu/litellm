# 如何获取 Anthropic Prompt Caching 的 TTL 信息

## 问题描述

当使用 Claude 模型并指定 `cache_control` 的 TTL 为 1 小时时,如何获取返回的 usage 中关于缓存的详细信息(包括 5 分钟和 1 小时缓存的 token 数量)。

## 解决方案

LiteLLM 已经正确地解析了 Anthropic API 返回的缓存信息。这些信息存储在 `usage.prompt_tokens_details.cache_creation_token_details` 中。

### 代码示例

```python
import litellm
import asyncio

async def test_cache_with_ttl():
    response = await litellm.acompletion(
        model="anthropic/claude-3-7-sonnet-20250219",
        messages=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": "Long context here..." * 200,
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Your question here",
                        "cache_control": {"type": "ephemeral", "ttl": "5m"},
                    }
                ],
            },
        ],
        max_tokens=100,
    )

    # 基本的缓存信息
    print(f"cache_creation_input_tokens: {response.usage.cache_creation_input_tokens}")
    print(f"cache_read_input_tokens: {response.usage.cache_read_input_tokens}")

    # 详细的缓存创建信息 (按 TTL 分类)
    if response.usage.prompt_tokens_details:
        details = response.usage.prompt_tokens_details

        if details.cache_creation_token_details:
            cache_details = details.cache_creation_token_details
            print(f"\n缓存创建详情:")
            print(f"  5分钟缓存 tokens: {cache_details.ephemeral_5m_input_tokens}")
            print(f"  1小时缓存 tokens: {cache_details.ephemeral_1h_input_tokens}")

asyncio.run(test_cache_with_ttl())
```

### 访问路径

```python
# 访问缓存创建的 token 总数
response.usage.cache_creation_input_tokens

# 访问缓存读取的 token 总数
response.usage.cache_read_input_tokens

# 访问详细的缓存创建信息(按 TTL 分类)
response.usage.prompt_tokens_details.cache_creation_token_details.ephemeral_5m_input_tokens
response.usage.prompt_tokens_details.cache_creation_token_details.ephemeral_1h_input_tokens
```

## Usage 对象结构

```python
Usage(
    prompt_tokens=4720,           # 所有输入 tokens
    completion_tokens=120,         # 所有输出 tokens
    total_tokens=4840,            # 总计

    # 缓存相关的顶层字段
    cache_creation_input_tokens=2360,  # 创建缓存的 tokens
    cache_read_input_tokens=0,         # 从缓存读取的 tokens

    # 详细信息
    prompt_tokens_details=PromptTokensDetailsWrapper(
        cached_tokens=0,           # 命中缓存的 tokens
        cache_creation_tokens=2360,

        # TTL 详细信息
        cache_creation_token_details=CacheCreationTokenDetails(
            ephemeral_5m_input_tokens=100,   # 5分钟缓存的 tokens
            ephemeral_1h_input_tokens=2260   # 1小时缓存的 tokens
        )
    )
)
```

## 注意事项

1. **cache_creation_token_details** 仅在创建新缓存时才会有值
2. 当读取已有缓存时,`cache_read_input_tokens` 会大于 0,但 `cache_creation_token_details` 可能为 None
3. TTL 参数 (`"5m"` 或 `"1h"`) 决定了 tokens 被归类到哪个 TTL 桶中
4. 如果不指定 TTL,Anthropic 会使用默认的 TTL(通常是 5 分钟)

## 代码实现位置

- 解析逻辑: `litellm/llms/anthropic/chat/transformation.py` 的 `calculate_usage` 方法 (第 1193-1291 行)
- 类型定义: `litellm/types/utils.py` 中的 `CacheCreationTokenDetails` 类

## 测试

运行测试验证功能:

```bash
poetry run python test_cache_ttl_issue.py
```

这将验证缓存 token 详细信息是否被正确解析和返回。
