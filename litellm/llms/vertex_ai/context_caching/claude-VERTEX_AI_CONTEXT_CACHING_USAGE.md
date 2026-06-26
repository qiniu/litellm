# Vertex AI Context Caching 使用流程

本文档说明如何在 LiteLLM 中使用 Vertex AI 的 Context Caching 功能（针对 Gemini 模型）。

## 目录

1. [概述](#概述)
2. [核心概念](#核心概念)
3. [使用步骤](#使用步骤)
4. [代码流程详解](#代码流程详解)
5. [示例代码](#示例代码)
6. [注意事项](#注意事项)

---

## 概述

Vertex AI 的 Context Caching 功能允许您缓存经常使用的上下文内容（如长文档、系统提示等），以减少重复发送相同内容的成本和延迟。LiteLLM 支持通过在消息中添加 `cache_control` 参数来启用此功能。

**支持的接口类型**：
- 同步 chat completion（`completion()`）
- 异步 chat completion（`acompletion()`）
- 流式响应

**支持的 Provider**：
- `vertex_ai`
- `vertex_ai_beta`
- `gemini`（Google AI Studio）

---

## 核心概念

### 1. Cache Control 参数

在消息的 content 中添加 `cache_control` 字段来标记需要缓存的内容：

```python
{
    "type": "text",
    "text": "这是需要缓存的内容",
    "cache_control": {
        "type": "ephemeral",
        "ttl": "3600s"  # 可选，缓存时长（秒）
    }
}
```

### 2. 缓存范围

- **项目级别（Project）**: 缓存在 Vertex AI 项目级别隔离
- **区域级别（Location）**: 缓存在不同地区（如 us-central1、europe-west1）之间隔离
- **Provider 级别**: 不同 Provider（vertex_ai vs gemini）的缓存相互独立

### 3. 缓存层级

LiteLLM 实现了两层缓存机制：

1. **本地缓存（Local Cache）**
   - 使用内存 TTL 缓存存储 cache_key → cache_id 的映射
   - 避免重复的网络请求
   - 自动过期清理

2. **Google API 缓存（Remote Cache）**
   - 实际存储在 Google 服务器上
   - 通过 cachedContents API 管理
   - 需要网络请求查询

---

## 使用步骤

### 步骤 1: 准备带有 cache_control 的消息

```python
messages = [
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "这是一个很长的上下文，我想缓存它",
                "cache_control": {"type": "ephemeral", "ttl": "3600s"}
            }
        ]
    },
    {
        "role": "user",
        "content": "基于上面的上下文，回答这个问题..."
    }
]
```

**重要规则**：
- 带有 `cache_control` 的消息必须是**连续的**
- 只有**第一个连续块**的缓存消息会被缓存
- 如果中间有非缓存消息打断，后续的 cache_control 会被忽略

### 步骤 2: 调用同步 completion 接口

```python
import litellm

response = litellm.completion(
    model="vertex_ai/gemini-1.5-pro",
    messages=messages,
    vertex_project="your-project-id",
    vertex_location="us-central1"
)
```

### 步骤 3: 后续请求自动复用缓存

当您再次发送**相同的缓存内容**时，LiteLLM 会自动：
1. 检查本地缓存
2. 如果本地缓存未命中，查询 Google API 缓存
3. 如果找到有效缓存，复用缓存 ID
4. 如果未找到，创建新的缓存

---

## 代码流程详解

### 整体流程图

```
用户调用 completion()
    ↓
VertexLLM.completion() [vertex_and_google_ai_studio_gemini.py:2314]
    ↓
sync_transform_request_body() [transformation.py:551]
    ↓
ContextCachingEndpoints.check_and_create_cache() [vertex_ai_context_caching.py:324]
    ↓
分离缓存消息 separate_cached_messages() [transformation.py:117]
    ↓
生成 cache_key = get_cache_key(messages + tools)
    ↓
┌─────────────────────────────────────────────┐
│ 1. 检查本地缓存 (local_cache_manager)      │
│    - 作用域：project + location + provider │
└─────────────────────────────────────────────┘
    ↓ 未命中
┌─────────────────────────────────────────────┐
│ 2. 检查 Google API 缓存                     │
│    GET /cachedContents                      │
│    匹配 displayName == cache_key            │
└─────────────────────────────────────────────┘
    ↓ 未找到
┌─────────────────────────────────────────────┐
│ 3. 创建新缓存                               │
│    POST /cachedContents                     │
│    - 转换消息格式                           │
│    - 提取 TTL                               │
│    - 存储到本地缓存                         │
└─────────────────────────────────────────────┘
    ↓
返回: (filtered_messages, optional_params, cached_content_id)
    ↓
构建最终请求体 _transform_request_body()
    - contents: 非缓存消息
    - cachedContent: 缓存 ID
    ↓
发送请求到 Vertex AI
```

### 关键代码位置

#### 1. 同步 Completion 入口

**文件**: `litellm/llms/vertex_ai/gemini/vertex_and_google_ai_studio_gemini.py`

**方法**: `VertexLLM.completion()` (行 2314)

```python
def completion(
    self,
    model: str,
    messages: list,
    ...
    vertex_project: Optional[str],
    vertex_location: Optional[str],
    ...
) -> Union[ModelResponse, CustomStreamWrapper]:
    # 调用 sync_transform_request_body 转换请求
    data = sync_transform_request_body(
        gemini_api_key=gemini_api_key,
        messages=messages,
        model=model,
        vertex_project=vertex_project,
        vertex_location=vertex_location,
        vertex_auth_header=auth_header,
        ...
    )
```

#### 2. 请求转换与缓存处理

**文件**: `litellm/llms/vertex_ai/gemini/transformation.py`

**方法**: `sync_transform_request_body()` (行 551)

```python
def sync_transform_request_body(...) -> RequestBody:
    from ..context_caching.vertex_ai_context_caching import ContextCachingEndpoints

    context_caching_endpoints = ContextCachingEndpoints()

    # 检查和创建缓存
    messages, optional_params, cached_content = (
        context_caching_endpoints.check_and_create_cache(
            messages=messages,
            optional_params=optional_params,
            vertex_project=vertex_project,
            vertex_location=vertex_location,
            ...
        )
    )

    # 构建最终请求体
    return _transform_request_body(
        messages=messages,  # 已过滤的非缓存消息
        cached_content=cached_content,  # 缓存 ID
        ...
    )
```

#### 3. 核心缓存逻辑

**文件**: `litellm/llms/vertex_ai/context_caching/vertex_ai_context_caching.py`

**方法**: `ContextCachingEndpoints.check_and_create_cache()` (行 324)

```python
def check_and_create_cache(
    self,
    messages: List[AllMessageValues],
    optional_params: dict,
    vertex_project: Optional[str],
    vertex_location: Optional[str],
    ...
) -> Tuple[List[AllMessageValues], dict, Optional[str]]:
    # 1. 如果用户直接提供了 cached_content，直接返回
    if cached_content is not None:
        return messages, optional_params, cached_content

    # 2. 分离缓存消息和非缓存消息
    cached_messages, non_cached_messages = separate_cached_messages(messages)

    if len(cached_messages) == 0:
        return messages, optional_params, None

    # 3. 生成 cache_key（基于消息内容和工具定义）
    generated_cache_key = local_cache_obj.get_cache_key(
        messages=cached_messages,
        tools=tools
    )

    # 4. 检查本地缓存（带 project/location 作用域）
    local_cache_id = self.local_cache_manager.get_cache(
        cache_key=generated_cache_key,
        vertex_project=vertex_project,
        vertex_location=vertex_location,
        custom_llm_provider=custom_llm_provider
    )
    if local_cache_id is not None:
        return non_cached_messages, optional_params, local_cache_id

    # 5. 检查 Google API 缓存
    google_cache_name = self.check_cache(
        cache_key=generated_cache_key,
        vertex_project=vertex_project,
        vertex_location=vertex_location,
        ...
    )
    if google_cache_name:
        return non_cached_messages, optional_params, google_cache_name

    # 6. 创建新缓存
    cached_content_request_body = transform_openai_messages_to_gemini_context_caching(
        model=model,
        messages=cached_messages,
        cache_key=generated_cache_key,
        ...
    )

    response = client.post(url=url, json=cached_content_request_body)
    cache_id = response.json().get("name")

    # 7. 存储到本地缓存
    ttl_seconds = parse_ttl_to_seconds(cached_content_request_body.get("ttl"))
    self.local_cache_manager.set_cache(
        cache_key=generated_cache_key,
        cache_id=cache_id,
        ttl_seconds=ttl_seconds,
        vertex_project=vertex_project,
        vertex_location=vertex_location,
        custom_llm_provider=custom_llm_provider
    )

    return non_cached_messages, optional_params, cache_id
```

#### 4. 消息分离逻辑

**文件**: `litellm/llms/vertex_ai/context_caching/transformation.py`

**方法**: `separate_cached_messages()` (行 117)

```python
def separate_cached_messages(
    messages: List[AllMessageValues],
) -> Tuple[List[AllMessageValues], List[AllMessageValues]]:
    """
    分离缓存消息和非缓存消息。

    规则：
    - 只提取第一个连续的缓存消息块
    - 使用 get_first_continuous_block_idx() 找到连续块的结束位置
    """
    cached_messages: List[AllMessageValues] = []
    non_cached_messages: List[AllMessageValues] = []

    # 找到所有带 cache_control 的消息
    filtered_messages: List[Tuple[int, AllMessageValues]] = []
    for idx, message in enumerate(messages):
        if is_cached_message(message=message):
            filtered_messages.append((idx, message))

    # 找到第一个连续块
    last_continuous_block_idx = get_first_continuous_block_idx(filtered_messages)

    if filtered_messages and last_continuous_block_idx is not None:
        first_cached_idx = filtered_messages[0][0]
        last_cached_idx = filtered_messages[last_continuous_block_idx][0]

        cached_messages = messages[first_cached_idx : last_cached_idx + 1]
        non_cached_messages = (
            messages[:first_cached_idx] + messages[last_cached_idx + 1 :]
        )
    else:
        non_cached_messages = messages

    return cached_messages, non_cached_messages
```

#### 5. 缓存消息转换

**文件**: `litellm/llms/vertex_ai/context_caching/transformation.py`

**方法**: `transform_openai_messages_to_gemini_context_caching()` (行 157)

```python
def transform_openai_messages_to_gemini_context_caching(
    model: str,
    messages: List[AllMessageValues],
    cache_key: str,
    vertex_project: Optional[str],
    vertex_location: Optional[str],
    custom_llm_provider: Literal["vertex_ai", "vertex_ai_beta", "gemini"],
) -> CachedContentRequestBody:
    # 1. 提取 TTL
    ttl = extract_ttl_from_cached_messages(messages)

    # 2. 处理 system message
    supports_system_message = get_supports_system_message(model, custom_llm_provider)
    transformed_system_messages, new_messages = _transform_system_message(
        supports_system_message, messages
    )

    # 3. 转换消息格式为 Gemini format
    transformed_messages = _gemini_convert_messages_with_history(
        messages=new_messages,
        model=model
    )

    # 4. 构建缓存请求体
    model_name = "models/{}".format(model)
    if custom_llm_provider in ["vertex_ai", "vertex_ai_beta"]:
        model_name = f"projects/{vertex_project}/locations/{vertex_location}/publishers/google/{model_name}"

    data = CachedContentRequestBody(
        contents=transformed_messages,
        model=model_name,
        displayName=cache_key,  # 使用 cache_key 作为 displayName
    )

    # 5. 添加 TTL 和 system instruction
    if ttl:
        data["ttl"] = ttl
    if transformed_system_messages:
        data["system_instruction"] = transformed_system_messages

    return data
```

#### 6. 最终请求体构建

**文件**: `litellm/llms/vertex_ai/gemini/transformation.py`

**方法**: `_transform_request_body()` (行 455)

```python
def _transform_request_body(
    messages: List[AllMessageValues],
    model: str,
    optional_params: dict,
    cached_content: Optional[str],  # 缓存 ID
    ...
) -> RequestBody:
    # 转换消息格式
    content = litellm.VertexGeminiConfig()._transform_messages(
        messages=messages,  # 这里的 messages 已经是过滤后的非缓存消息
        model=model
    )

    # 构建请求体
    data = RequestBody(contents=content)

    # 如果有缓存 ID，添加到请求体
    if cached_content is not None:
        data["cachedContent"] = cached_content

    # ... 添加其他参数（tools, generation_config 等）

    return data
```

---

## 示例代码

### 基础示例：缓存长文档

```python
import litellm

# 第一次调用：创建缓存
messages = [
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": """这是一个很长的文档内容...
                [假设这里有 10000 字的内容]
                """,
                "cache_control": {
                    "type": "ephemeral",
                    "ttl": "3600s"  # 缓存 1 小时
                }
            }
        ]
    },
    {
        "role": "user",
        "content": "请总结上面文档的主要内容"
    }
]

response = litellm.completion(
    model="vertex_ai/gemini-1.5-pro",
    messages=messages,
    vertex_project="your-project-id",
    vertex_location="us-central1"
)

print(response.choices[0].message.content)

# 第二次调用：自动复用缓存（相同的长文档内容）
messages2 = [
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": """这是一个很长的文档内容...
                [相同的 10000 字内容]
                """,
                "cache_control": {
                    "type": "ephemeral",
                    "ttl": "3600s"
                }
            }
        ]
    },
    {
        "role": "user",
        "content": "列出文档中的关键要点"  # 不同的问题
    }
]

# 这次调用会自动使用缓存，节省成本
response2 = litellm.completion(
    model="vertex_ai/gemini-1.5-pro",
    messages=messages2,
    vertex_project="your-project-id",
    vertex_location="us-central1"
)
```

### 直接使用已有的 cached_content

如果您已经有一个 cached_content ID，可以直接使用：

```python
response = litellm.completion(
    model="vertex_ai/gemini-1.5-pro",
    messages=[
        {
            "role": "user",
            "content": "基于缓存的上下文回答问题"
        }
    ],
    cached_content="projects/123/locations/us-central1/cachedContents/abc123",
    vertex_project="your-project-id",
    vertex_location="us-central1"
)
```

### 缓存系统提示词

```python
messages = [
    {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": "你是一个专业的法律顾问，擅长解答合同相关问题...",
                "cache_control": {
                    "type": "ephemeral",
                    "ttl": "7200s"  # 缓存 2 小时
                }
            }
        ]
    },
    {
        "role": "user",
        "content": "这份合同有什么风险？"
    }
]
```

### 使用工具（Tools）时的缓存

```python
messages = [
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "长文档内容...",
                "cache_control": {"type": "ephemeral"}
            }
        ]
    }
]

response = litellm.completion(
    model="vertex_ai/gemini-1.5-pro",
    messages=messages,
    tools=[
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "获取天气信息",
                "parameters": {...}
            }
        }
    ],
    vertex_project="your-project-id",
    vertex_location="us-central1"
)

# 注意：cache_key 会同时包含 messages 和 tools
# 如果 tools 定义改变，缓存会失效
```

---

## 注意事项

### 1. 缓存作用域

- **不同项目之间**: 缓存不共享
- **不同区域之间**: 缓存不共享（us-central1 vs europe-west1）
- **不同 Provider 之间**: vertex_ai 和 gemini 的缓存不共享

### 2. Cache Key 计算

Cache key 基于以下内容计算：
- 缓存消息的内容（content）
- Tools 定义（如果有）

**任何一个改变都会导致缓存失效：**
- 消息文本内容改变
- Tools 定义改变
- 消息顺序改变

### 3. TTL（Time To Live）

- **格式**: 字符串，如 `"3600s"`（秒为单位）
- **默认值**: 如果不指定，默认为 3600 秒（1 小时）
- **有效性检查**: 系统会检查缓存是否过期

### 4. 连续性要求

带有 `cache_control` 的消息必须是连续的：

```python
# ✅ 正确：连续的缓存消息
messages = [
    {"role": "user", "content": [{"type": "text", "text": "A", "cache_control": {...}}]},
    {"role": "user", "content": [{"type": "text", "text": "B", "cache_control": {...}}]},
    {"role": "user", "content": "C"}  # 非缓存消息
]
# 结果：A 和 B 会被缓存

# ❌ 错误：非连续的缓存消息
messages = [
    {"role": "user", "content": [{"type": "text", "text": "A", "cache_control": {...}}]},
    {"role": "user", "content": "B"},  # 非缓存消息打断
    {"role": "user", "content": [{"type": "text", "text": "C", "cache_control": {...}}]}
]
# 结果：只有 A 会被缓存，C 的 cache_control 会被忽略
```

### 5. 性能优化

LiteLLM 的两层缓存设计：

1. **本地缓存**:
   - 优先检查，避免不必要的网络请求
   - 使用 TTL 自动过期
   - 按 project + location + provider 隔离

2. **Google API 缓存**:
   - 只在本地缓存未命中时查询
   - 实际存储位置

### 6. 错误处理

- 如果创建缓存失败，请求会继续执行（不会中断）
- 如果查询缓存时返回 403，会被忽略（可能是权限问题）
- 如果缓存已过期，会自动创建新缓存

### 7. 成本优化建议

- **缓存长内容**: 对于长文档、长系统提示词，使用缓存可以显著降低成本
- **合理设置 TTL**: 根据内容更新频率设置合适的 TTL
- **避免频繁变化**: 如果内容经常变化，缓存效果会很差
- **批量处理**: 对于多个基于相同上下文的问题，使用相同的缓存内容

### 8. 调试

查看缓存相关日志：

```python
import litellm
litellm.set_verbose = True  # 开启详细日志

response = litellm.completion(...)
# 日志中会显示：
# - 是否使用了本地缓存
# - 是否查询了 Google API 缓存
# - 是否创建了新缓存
```

### 9. 异步支持

异步版本的流程完全相同，只需使用 `acompletion()`:

```python
import asyncio
import litellm

async def main():
    response = await litellm.acompletion(
        model="vertex_ai/gemini-1.5-pro",
        messages=messages,
        vertex_project="your-project-id",
        vertex_location="us-central1"
    )
    print(response.choices[0].message.content)

asyncio.run(main())
```

---

## 总结

Vertex AI Context Caching 在 LiteLLM 中的实现提供了：

1. **透明的缓存机制**: 用户只需在消息中添加 `cache_control`，其余由 LiteLLM 自动处理
2. **多层缓存优化**: 本地缓存 + Google API 缓存，减少网络请求
3. **作用域隔离**: 按 project/location/provider 隔离，确保缓存安全性
4. **自动 TTL 管理**: 自动处理缓存过期和清理
5. **同步/异步支持**: 同时支持同步和异步调用

通过合理使用 Context Caching，可以显著降低使用 Gemini 模型的成本，特别是在处理长文档、频繁使用相同上下文的场景中。
