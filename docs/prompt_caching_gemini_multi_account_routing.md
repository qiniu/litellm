# Gemini Context Caching 多账号路由完全指南

## 📋 重要发现

你说得对！**Gemini 确实支持 Context Caching**,并且 LiteLLM 已经实现了对 Gemini Context Caching 的支持！

### Gemini vs Anthropic Cache 对比

| 特性 | Anthropic Claude | Google Gemini | 实现差异 |
|------|------------------|---------------|---------|
| **API 参数** | `cache_control` 在 messages 中 | `cache_control` 在 messages 中 | ✅ 相同 |
| **缓存机制** | 客户端指定,服务端自动缓存 | 需要先创建 cachedContent,然后引用 | ❌ **不同!** |
| **缓存位置** | Anthropic 服务端 | Google API 服务端 (`cachedContents` endpoint) | ❌ 不同 |
| **缓存共享** | 基于 API key | 基于 project/location | ❌ 不同 |
| **LiteLLM Router 支持** | ✅ 通过 `optional_pre_call_checks=["prompt_caching"]` | ⚠️ **部分支持** | ⚠️ 需要改进 |

---

## 核心问题分析

### 问题 1: LiteLLM Router 的 Prompt Caching 对 Gemini 的影响

**当前行为**:

```python
router = Router(
    model_list=[
        # Gemini deployment 1
        {
            "model_name": "gemini-pro",
            "litellm_params": {
                "model": "vertex_ai/gemini-1.5-pro",
                "vertex_project": "project-a",
                "vertex_location": "us-central1"
            },
            "model_info": {"id": "gemini-project-a"}
        },
        # Gemini deployment 2
        {
            "model_name": "gemini-pro",
            "litellm_params": {
                "model": "vertex_ai/gemini-1.5-pro",
                "vertex_project": "project-b",
                "vertex_location": "us-central1"
            },
            "model_info": {"id": "gemini-project-b"}
        }
    ],
    optional_pre_call_checks=["prompt_caching"]
)

# 第一次请求
response1 = await router.acompletion(
    model="gemini-pro",
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Large context..." * 500,
                 "cache_control": {"type": "ephemeral"}}
            ]
        }
    ]
)
# Router 选择: gemini-project-a
# LiteLLM 内部: 调用 ContextCachingEndpoints.check_and_create_cache()
# → 在 project-a 创建 cachedContent

# 第二次请求
response2 = await router.acompletion(
    model="gemini-pro",
    messages=[...]  # 相同的 cache_control 内容
)
# ⚠️ Router 强制路由到: gemini-project-a
# ✅ LiteLLM 检查: 在 project-a 找到 cachedContent,直接使用
```

**结论**:

✅ **对 Gemini 多账号间确实有效!**

但是有一些重要的区别和注意事项。

---

## Gemini Context Caching 工作原理

### 架构对比

#### Anthropic Claude 缓存流程

```
┌─────────────────────────────────────────────────────────┐
│ Client                                                  │
├─────────────────────────────────────────────────────────┤
│ 1. 发送请求 (带 cache_control)                         │
│    POST /v1/messages                                    │
│    {                                                    │
│      "messages": [                                      │
│        {"content": [..., "cache_control": {...}]}      │
│      ]                                                  │
│    }                                                    │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────┐
│ Anthropic API                                           │
├─────────────────────────────────────────────────────────┤
│ 2. 自动检查缓存                                         │
│    - 基于 API key + prompt 内容                         │
│    - 如果未缓存: 创建缓存                               │
│    - 如果已缓存: 使用缓存                               │
│                                                         │
│ 3. 返回 response                                        │
│    usage: {                                             │
│      cache_creation_input_tokens: 1024,  // 或 0       │
│      cache_read_input_tokens: 0          // 或 1024    │
│    }                                                    │
└─────────────────────────────────────────────────────────┘
```

#### Gemini Context Caching 流程

```
┌─────────────────────────────────────────────────────────┐
│ Client (LiteLLM)                                        │
├─────────────────────────────────────────────────────────┤
│ 1. 发送请求 (带 cache_control)                         │
│    acompletion(messages=[                               │
│      {"content": [..., "cache_control": {...}]}        │
│    ])                                                   │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────┐
│ LiteLLM Internal (ContextCachingEndpoints)              │
├─────────────────────────────────────────────────────────┤
│ 2. 分离 cached 和 non-cached messages                   │
│    cached_messages, non_cached_messages =               │
│      separate_cached_messages(messages)                 │
│                                                         │
│ 3. 生成 cache_key                                       │
│    cache_key = get_cache_key(cached_messages, tools)   │
│                                                         │
│ 4. 检查 Google 是否已缓存                              │
│    GET /cachedContents                                  │
│    → 查找 displayName == cache_key 的 cachedContent    │
│                                                         │
│ 5a. 如果找到:                                          │
│     google_cache_name = "cachedContents/abc123"        │
│                                                         │
│ 5b. 如果未找到:                                        │
│     POST /cachedContents                                │
│     {                                                   │
│       "model": "gemini-1.5-pro",                        │
│       "contents": [...],  // 只包含 cached_messages    │
│       "displayName": cache_key,                         │
│       "ttl": "3600s"                                    │
│     }                                                   │
│     → 创建新的 cachedContent                           │
│     google_cache_name = response.name                   │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────┐
│ 6. 调用 Gemini API                                      │
│    POST /generateContent                                │
│    {                                                    │
│      "contents": non_cached_messages,  // 只有新消息   │
│      "cachedContent": google_cache_name // 引用缓存     │
│    }                                                    │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────┐
│ Google Gemini API                                       │
├─────────────────────────────────────────────────────────┤
│ 7. 使用 cachedContent + 新消息生成响应                  │
│                                                         │
│ 8. 返回 response                                        │
│    (注: Gemini 不返回 cache hit/miss 信息)             │
└─────────────────────────────────────────────────────────┘
```

### 关键差异

| 环节 | Anthropic | Gemini | 影响 |
|------|-----------|--------|------|
| **缓存创建** | 隐式(API 自动) | 显式(需要调用 cachedContents API) | Gemini 需要额外 API 调用 |
| **缓存引用** | 自动(基于内容) | 显式(传递 cachedContent ID) | Gemini 需要管理 cache ID |
| **缓存查询** | 不支持 | 支持(GET cachedContents) | Gemini 可以列出所有缓存 |
| **缓存共享** | API key 级别 | Project + Location 级别 | Gemini 更细粒度 |
| **Router 路由** | 基于 LiteLLM cache (Redis/内存) | 基于 LiteLLM cache (Redis/内存) | **相同!** |

---

## Gemini 多账号路由配置

### 场景 1: 使用 LiteLLM Router 的 Prompt Caching (推荐)

```python
from litellm import Router

router = Router(
    model_list=[
        # Vertex AI Project A (us-central1)
        {
            "model_name": "gemini-pro",
            "litellm_params": {
                "model": "vertex_ai/gemini-1.5-pro",
                "vertex_project": "my-project-a",
                "vertex_location": "us-central1",
                "vertex_credentials": "path/to/project-a-creds.json"
            },
            "model_info": {
                "id": "gemini-us-central1-project-a"  # ← 明确指定
            }
        },
        # Vertex AI Project B (us-central1)
        {
            "model_name": "gemini-pro",
            "litellm_params": {
                "model": "vertex_ai/gemini-1.5-pro",
                "vertex_project": "my-project-b",
                "vertex_location": "us-central1",
                "vertex_credentials": "path/to/project-b-creds.json"
            },
            "model_info": {
                "id": "gemini-us-central1-project-b"
            }
        },
        # Vertex AI Project C (us-west1)
        {
            "model_name": "gemini-pro",
            "litellm_params": {
                "model": "vertex_ai/gemini-1.5-pro",
                "vertex_project": "my-project-c",
                "vertex_location": "us-west1",
                "vertex_credentials": "path/to/project-c-creds.json"
            },
            "model_info": {
                "id": "gemini-us-west1-project-c"
            }
        }
    ],
    routing_strategy="simple-shuffle",
    optional_pre_call_checks=["prompt_caching"],  # ← 启用 prompt caching 路由

    # Redis 配置 (多进程/多实例共享)
    redis_host="localhost",
    redis_port=6379
)

# 使用示例
response1 = await router.acompletion(
    model="gemini-pro",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "You are an AI assistant with access to a large knowledge base." * 300,
                    "cache_control": {"type": "ephemeral", "ttl": "3600s"}  # 1小时
                }
            ]
        },
        {
            "role": "user",
            "content": "What is machine learning?"
        }
    ]
)

# 第一次请求:
# 1. Router 随机选择 → 假设: gemini-us-central1-project-a
# 2. LiteLLM 检查 project-a 的 cachedContents → 未找到
# 3. LiteLLM 创建 cachedContent in project-a
# 4. 调用 Gemini API with cachedContent
# 5. Router 保存 cache_key → gemini-us-central1-project-a 到 Redis

# 第二次请求 (相同的 cached 内容)
response2 = await router.acompletion(
    model="gemini-pro",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "You are an AI assistant with access to a large knowledge base." * 300,
                    "cache_control": {"type": "ephemeral", "ttl": "3600s"}
                }
            ]
        },
        {
            "role": "user",
            "content": "What is deep learning?"  # ← 不同的问题
        }
    ]
)

# 第二次请求:
# 1. Router 从 Redis 查询 cache_key → gemini-us-central1-project-a
# 2. Router 强制路由到 gemini-us-central1-project-a
# 3. LiteLLM 检查 project-a 的 cachedContents → ✅ 找到!
# 4. 调用 Gemini API with existing cachedContent
# 5. ✅ 复用缓存,节省成本!
```

### 工作原理详解

#### LiteLLM Router 层面

```python
# litellm/router_utils/pre_call_checks/prompt_caching_deployment_check.py

async def async_filter_deployments(self, model, healthy_deployments, messages, ...):
    # 1. 检查是否有 cache_control (token >= 1024)
    if is_prompt_caching_valid_prompt(messages=messages, model=model):

        # 2. 生成 cache_key (基于 cached messages)
        from litellm.router_utils.prompt_caching_cache import PromptCachingCache
        prompt_cache = PromptCachingCache(cache=self.cache)

        cache_key = PromptCachingCache.get_prompt_caching_cache_key(
            messages=messages,
            tools=None
        )
        # cache_key = "deployment:hash(cached_messages):prompt_caching"

        # 3. 从 Redis/内存查询 model_id
        model_id_dict = await prompt_cache.async_get_model_id(messages, tools)

        if model_id_dict is not None:
            model_id = model_id_dict["model_id"]
            # model_id = "gemini-us-central1-project-a"

            # 4. 过滤 deployments,只返回匹配的
            for deployment in healthy_deployments:
                if deployment["model_info"]["id"] == model_id:
                    return [deployment]  # ← 强制使用这个 deployment

    return healthy_deployments
```

#### Gemini Context Caching 层面

```python
# litellm/llms/vertex_ai/context_caching/vertex_ai_context_caching.py

async def async_check_and_create_cache(self, messages, ...):
    # 1. 分离 cached 和 non-cached messages
    cached_messages, non_cached_messages = separate_cached_messages(messages)

    if len(cached_messages) == 0:
        return messages, optional_params, None

    # 2. 生成 cache_key (不同于 Router 的 cache_key!)
    generated_cache_key = local_cache_obj.get_cache_key(
        messages=cached_messages,
        tools=tools
    )
    # 这是 Google 侧的 cache key (displayName)

    # 3. 检查 Google 是否已缓存
    # GET https://{location}-aiplatform.googleapis.com/v1/
    #     projects/{project}/locations/{location}/cachedContents
    google_cache_name = await self.async_check_cache(
        cache_key=generated_cache_key,
        ...
    )

    if google_cache_name:
        # ✅ 找到了! 返回 non_cached_messages 和 google_cache_name
        return non_cached_messages, optional_params, google_cache_name

    # 4. 未找到,创建新的 cachedContent
    # POST https://{location}-aiplatform.googleapis.com/v1/
    #      projects/{project}/locations/{location}/cachedContents
    cached_content_request_body = {
        "model": f"projects/{project}/locations/{location}/publishers/google/models/{model}",
        "contents": transformed_cached_messages,
        "displayName": generated_cache_key,  # ← 用于后续查找
        "ttl": "3600s"
    }

    response = await client.post(url, json=cached_content_request_body)
    google_cache_name = response.json()["name"]
    # google_cache_name = "cachedContents/abc123xyz"

    return non_cached_messages, optional_params, google_cache_name
```

---

## 重要细节和注意事项

### 1. 两层缓存机制

Gemini 使用了**两层缓存**:

```
┌──────────────────────────────────────────────────────────┐
│ 第 1 层: LiteLLM Router Cache (Redis/内存)               │
├──────────────────────────────────────────────────────────┤
│ 目的: 路由到相同的 deployment                            │
│ Key: deployment:hash(cached_messages):prompt_caching     │
│ Value: {"model_id": "gemini-us-central1-project-a"}      │
│ TTL: 300 秒 (5 分钟)                                     │
└──────────────────────────────────────────────────────────┘
                        ↓
┌──────────────────────────────────────────────────────────┐
│ 第 2 层: Google cachedContents (每个 project 独立)       │
├──────────────────────────────────────────────────────────┤
│ 目的: 存储实际的 cached 内容                             │
│ displayName: hash(cached_messages + tools)               │
│ 存储: Google Cloud (每个 project/location 独立)          │
│ TTL: 用户指定 (如 3600s)                                 │
└──────────────────────────────────────────────────────────┘
```

**两层缓存的协同**:

```python
# 第一次请求 → 路由到 project-a
# 1. Router cache: 无
# 2. Router 选择: project-a (随机)
# 3. Google cachedContents (project-a): 无
# 4. 创建 cachedContent in project-a → "cachedContents/abc123"
# 5. 保存到 Router cache: cache_key → project-a

# 第二次请求 (5分钟内)
# 1. Router cache: 命中! → project-a
# 2. Router 强制使用: project-a
# 3. Google cachedContents (project-a): 命中! → "cachedContents/abc123"
# 4. 直接使用 cachedContent
# 5. ✅✅ 双层命中,最优性能!

# 第三次请求 (6分钟后,Router cache 过期)
# 1. Router cache: 未命中 (TTL 过期)
# 2. Router 随机选择: project-b
# 3. Google cachedContents (project-b): 无 (不同 project!)
# 4. 创建 cachedContent in project-b
# 5. 保存到 Router cache: cache_key → project-b
# ⚠️ 问题: 即使 project-a 的 cachedContent 还有效,也不会使用
```

### 2. 跨 Project 缓存不共享

**重要**: Gemini 的 cachedContents 是 **per-project, per-location** 的!

```python
# Project A 创建的 cachedContent
# → 只能在 Project A 中使用
# → Project B 无法访问

# 这与 Anthropic 不同:
# Anthropic: 基于 API key,可以跨多个 deployment 共享
# Gemini: 基于 project + location,完全隔离
```

**影响**:

```python
router = Router(
    model_list=[
        # Project A
        {"model_name": "gemini", ..., "vertex_project": "project-a"},
        # Project B
        {"model_name": "gemini", ..., "vertex_project": "project-b"},
    ],
    optional_pre_call_checks=["prompt_caching"]
)

# 第一次请求 → project-a (创建 cachedContent)
response1 = await router.acompletion(...)

# Router cache 过期后
# 第二次请求 → project-b (无法使用 project-a 的 cachedContent!)
response2 = await router.acompletion(...)
# ⚠️ 需要在 project-b 重新创建 cachedContent
```

### 3. TTL 配置

Gemini 支持自定义 TTL:

```python
messages = [
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "Large context...",
                "cache_control": {
                    "type": "ephemeral",
                    "ttl": "3600s"  # ← 1小时 (必须是 "XXXs" 格式)
                }
            }
        ]
    }
]

# Gemini 支持的 TTL 格式:
# - "3600s" (1小时)
# - "7200s" (2小时)
# - "300s" (5分钟)
# ⚠️ 必须以 "s" 结尾,表示秒
```

**与 Router cache TTL 的关系**:

```python
# Router cache TTL: 300秒 (5分钟,固定)
# → 控制多久后可能路由到不同 deployment

# Gemini cachedContent TTL: 用户指定 (如 3600s)
# → 控制 Google 侧缓存的有效期

# 最佳实践:
# - Gemini cachedContent TTL > Router cache TTL
# - 例如: cachedContent = 3600s, Router = 300s
# - 这样即使 Router cache 过期,重新路由到同一 project 仍能命中缓存
```

### 4. 成本分析

Gemini Context Caching 的成本:

```
# Gemini 1.5 Pro 定价 (示例)
Normal Input: $3.50 per 1M tokens
Cached Input (read): $0.88 per 1M tokens (75% 折扣)
Output: $10.50 per 1M tokens

# 场景: 10,000 tokens 的 context,使用 10 次
# 不使用缓存:
cost_no_cache = 10,000 * 10 * $3.50 / 1M = $0.35

# 使用缓存:
# - 第一次: 创建缓存 (正常价格)
# - 后续 9 次: 读取缓存 (75% 折扣)
cost_with_cache = (10,000 * $3.50 / 1M) + (10,000 * 9 * $0.88 / 1M)
                = $0.035 + $0.0792
                = $0.1142

# 节省: $0.35 - $0.1142 = $0.2358 (67% 成本降低)
```

**注意**: Gemini 还有 cachedContent 存储费用:

```
Storage cost: 约 $1.00 per million tokens per hour

# 示例: 10,000 tokens 缓存 1 小时
storage_cost = 10,000 * $1.00 / 1M = $0.01

# 总成本 with cache = $0.1142 + $0.01 = $0.1242
# 仍然节省: $0.35 - $0.1242 = $0.2258 (65%)
```

---

## 推荐配置

### 配置 1: 单 Model Name + 多 Projects (最简单)

```yaml
# config.yaml
model_list:
  # Gemini deployments (多个 projects)
  - model_name: gemini-pro
    litellm_params:
      model: vertex_ai/gemini-1.5-pro
      vertex_project: my-project-a
      vertex_location: us-central1
      vertex_credentials: /path/to/project-a-creds.json
    model_info:
      id: gemini-project-a-us-central1

  - model_name: gemini-pro
    litellm_params:
      model: vertex_ai/gemini-1.5-pro
      vertex_project: my-project-b
      vertex_location: us-central1
      vertex_credentials: /path/to/project-b-creds.json
    model_info:
      id: gemini-project-b-us-central1

  - model_name: gemini-pro
    litellm_params:
      model: vertex_ai/gemini-1.5-pro
      vertex_project: my-project-c
      vertex_location: us-west1
      vertex_credentials: /path/to/project-c-creds.json
    model_info:
      id: gemini-project-c-us-west1

router_settings:
  routing_strategy: simple-shuffle

environment_variables:
  REDIS_HOST: localhost
  REDIS_PORT: "6379"
```

```python
router = Router(
    config_file_path="config.yaml",
    optional_pre_call_checks=["prompt_caching"]
)

# 使用
response = await router.acompletion(
    model="gemini-pro",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "System instructions..." * 300,
                    "cache_control": {"type": "ephemeral", "ttl": "3600s"}
                }
            ]
        },
        {"role": "user", "content": "Question?"}
    ]
)
```

**优点**:
- ✅ 自动负载均衡
- ✅ 相同 cached 内容会路由到相同 project
- ✅ 简单配置

**缺点**:
- ⚠️ Router cache 过期后可能路由到不同 project
- ⚠️ 需要在新 project 重新创建 cachedContent

### 配置 2: 按 Project/Location 分组 (推荐生产)

```yaml
model_list:
  # US Central1 - Project A
  - model_name: gemini-us-central1-a
    litellm_params:
      model: vertex_ai/gemini-1.5-pro
      vertex_project: project-a
      vertex_location: us-central1
    model_info:
      id: gemini-project-a-us-central1

  # US Central1 - Project B
  - model_name: gemini-us-central1-b
    litellm_params:
      model: vertex_ai/gemini-1.5-pro
      vertex_project: project-b
      vertex_location: us-central1
    model_info:
      id: gemini-project-b-us-central1

  # US West1 - Project C
  - model_name: gemini-us-west1-c
    litellm_params:
      model: vertex_ai/gemini-1.5-pro
      vertex_project: project-c
      vertex_location: us-west1
    model_info:
      id: gemini-project-c-us-west1
```

```python
# 客户端需要知道使用哪个 model_name
response = await router.acompletion(
    model="gemini-us-central1-a",  # ← 明确指定 project
    messages=[...]
)
```

**优点**:
- ✅ 完全控制使用哪个 project
- ✅ cachedContent 不会跨 project 混用
- ✅ 更可预测的行为

**缺点**:
- ⚠️ 客户端需要管理多个 model names
- ⚠️ 失去自动负载均衡的灵活性

---

## 与 Claude 混合使用

```yaml
model_list:
  # Claude deployments (支持 Anthropic Prompt Caching)
  - model_name: claude-sonnet
    litellm_params:
      model: anthropic/claude-3-7-sonnet-20250219
      api_key: sk-ant-key1
    model_info:
      id: claude-1

  - model_name: claude-sonnet
    litellm_params:
      model: anthropic/claude-3-7-sonnet-20250219
      api_key: sk-ant-key2
    model_info:
      id: claude-2

  # Gemini deployments (支持 Context Caching)
  - model_name: gemini-pro
    litellm_params:
      model: vertex_ai/gemini-1.5-pro
      vertex_project: project-a
      vertex_location: us-central1
    model_info:
      id: gemini-a

  - model_name: gemini-pro
    litellm_params:
      model: vertex_ai/gemini-1.5-pro
      vertex_project: project-b
      vertex_location: us-central1
    model_info:
      id: gemini-b
```

```python
router = Router(
    config_file_path="config.yaml",
    optional_pre_call_checks=["prompt_caching"]
)

# 使用 Claude (Anthropic Prompt Caching)
claude_response = await router.acompletion(
    model="claude-sonnet",
    messages=[
        {
            "role": "system",
            "content": [
                {"type": "text", "text": "System...",
                 "cache_control": {"type": "ephemeral"}}
            ]
        },
        {"role": "user", "content": "Question?"}
    ]
)

# 使用 Gemini (Context Caching)
gemini_response = await router.acompletion(
    model="gemini-pro",
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Context...",
                 "cache_control": {"type": "ephemeral", "ttl": "3600s"}}
            ]
        },
        {"role": "user", "content": "Question?"}
    ]
)
```

**✅ 两者可以同时使用!**
- Claude 使用 Anthropic 的 prompt caching 机制
- Gemini 使用 Google 的 context caching 机制
- Router 的 `optional_pre_call_checks=["prompt_caching"]` 对两者都生效

---

## 验证和调试

### 验证 Gemini Context Caching

```python
import litellm
litellm.set_verbose = True  # 启用详细日志

router = Router(...)

# 第一次请求
print("=== First Request ===")
response1 = await router.acompletion(
    model="gemini-pro",
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Context..." * 500,
                 "cache_control": {"type": "ephemeral", "ttl": "3600s"}}
            ]
        },
        {"role": "user", "content": "Q1?"}
    ]
)

deployment1 = response1._hidden_params.get("model_id")
print(f"Deployment: {deployment1}")

# 查看日志,应该看到:
# POST https://.../cachedContents  ← 创建 cachedContent
# POST https://.../generateContent ← 使用 cachedContent 生成响应

await asyncio.sleep(2)

# 第二次请求
print("\n=== Second Request ===")
response2 = await router.acompletion(
    model="gemini-pro",
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Context..." * 500,
                 "cache_control": {"type": "ephemeral", "ttl": "3600s"}}
            ]
        },
        {"role": "user", "content": "Q2?"}
    ]
)

deployment2 = response2._hidden_params.get("model_id")
print(f"Deployment: {deployment2}")

# 查看日志,应该看到:
# GET https://.../cachedContents   ← 查找 cachedContent
# POST https://.../generateContent ← 使用已有 cachedContent

if deployment1 == deployment2:
    print("✅ Router: Same deployment (prompt caching working)")
else:
    print("❌ Router: Different deployments")
```

### 手动查看 cachedContents

```python
# 列出某个 project 的所有 cachedContents
import httpx
from google.auth import default
from google.auth.transport.requests import Request

credentials, project = default()
credentials.refresh(Request())

location = "us-central1"
url = f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/cachedContents"

headers = {
    "Authorization": f"Bearer {credentials.token}"
}

response = httpx.get(url, headers=headers)
print(response.json())

# 输出示例:
# {
#   "cachedContents": [
#     {
#       "name": "cachedContents/abc123...",
#       "displayName": "litellm_cache_key_...",
#       "model": "projects/.../models/gemini-1.5-pro",
#       "createTime": "2025-01-22T10:00:00Z",
#       "expireTime": "2025-01-22T11:00:00Z",
#       "ttl": "3600s"
#     }
#   ]
# }
```

---

## 总结

### 核心要点

1. ✅ **Gemini 支持 Context Caching**,LiteLLM 已实现
2. ✅ **LiteLLM Router 的 `prompt_caching` 对 Gemini 生效**
3. ⚠️ **Gemini 缓存是 per-project 的**,不跨 project 共享
4. ⚠️ **两层缓存**: Router cache (5分钟) + Google cachedContents (用户指定)
5. ✅ **与 Claude 可以混用**,互不干扰

### 推荐做法

**多 Gemini 账号场景**:

```python
# 1. 使用相同 model_name + optional_pre_call_checks
router = Router(
    model_list=[
        {"model_name": "gemini-pro", "vertex_project": "a", ...},
        {"model_name": "gemini-pro", "vertex_project": "b", ...},
    ],
    optional_pre_call_checks=["prompt_caching"],
    redis_host="localhost",  # ← 必须配置 Redis!
    redis_port=6379
)

# 2. 使用 cache_control
await router.acompletion(
    model="gemini-pro",
    messages=[{
        "content": [{
            "text": "...",
            "cache_control": {"type": "ephemeral", "ttl": "3600s"}
        }]
    }]
)

# 3. 接受 Router cache 过期后可能切换 project 的行为
#    (或延长 Router cache TTL,但需要修改代码)
```

---

**维护者**: LiteLLM Team
**最后更新**: 2025-12-22
**版本**: 1.0.0
