# LiteLLM Router Prompt Caching 完全指南

## 目录

1. [概述](#概述)
2. [工作原理](#工作原理)
3. [核心概念](#核心概念)
4. [配置方式](#配置方式)
5. [使用示例](#使用示例)
6. [最佳实践](#最佳实践)
7. [故障排查](#故障排查)
8. [常见问题 FAQ](#常见问题-faq)

---

## 概述

LiteLLM Router 的 **Prompt Caching** 功能通过智能路由机制,确保相同可缓存内容的请求始终路由到同一个 deployment,从而最大化 Anthropic Prompt Caching 的命中率,显著降低成本(最高可节省 90% 的 input token 成本)。

### 关键特性

- ✅ 自动识别可缓存的 prompt (>= 1024 tokens)
- ✅ 智能路由到相同 deployment
- ✅ 支持跨进程共享缓存 (通过 Redis)
- ✅ 5 分钟 TTL 缓存窗口
- ✅ 支持多个 AWS 账号/API keys 的负载均衡

### 支持的模型

- Anthropic Claude (直连)
- AWS Bedrock Claude
- Google Vertex AI Claude

---

## 工作原理

### 整体流程图

```
┌─────────────────────────────────────────────────────────────────┐
│ 第一次请求 (messages1 - 包含 cache_control)                     │
└─────────────────────────────────────────────────────────────────┘
                          ↓
        ┌────────────────────────────────────┐
        │ 1. Router 检查 prompt_caching      │
        │    - 计算 tokens (>= 1024?)        │
        │    - 提取可缓存前缀                 │
        │    - 生成 cache_key (hash)         │
        └────────────────────────────────────┘
                          ↓
        ┌────────────────────────────────────┐
        │ 2. 查询缓存 (DualCache)            │
        │    - 内存缓存: 无                   │
        │    - Redis: 无                      │
        └────────────────────────────────────┘
                          ↓
        ┌────────────────────────────────────┐
        │ 3. 正常路由逻辑                     │
        │    - simple-shuffle 随机选择        │
        │    - 假设选中: deployment-2        │
        └────────────────────────────────────┘
                          ↓
        ┌────────────────────────────────────┐
        │ 4. 调用 Anthropic API              │
        │    deployment-2 → API call         │
        │    Response: cache_creation_tokens │
        └────────────────────────────────────┘
                          ↓
        ┌────────────────────────────────────┐
        │ 5. 成功后保存 model_id             │
        │    cache_key → deployment-2        │
        │    存储到: 内存 + Redis (TTL=300s) │
        └────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ 第二次请求 (messages2 - 相同可缓存内容,不同 user message)       │
└─────────────────────────────────────────────────────────────────┘
                          ↓
        ┌────────────────────────────────────┐
        │ 1. Router 检查 prompt_caching      │
        │    - 提取可缓存前缀 (相同!)         │
        │    - 生成 cache_key (相同 hash!)   │
        └────────────────────────────────────┘
                          ↓
        ┌────────────────────────────────────┐
        │ 2. 查询缓存                         │
        │    - 内存缓存: 命中! deployment-2   │
        └────────────────────────────────────┘
                          ↓
        ┌────────────────────────────────────┐
        │ 3. 强制路由到 deployment-2         │
        │    - 跳过正常的路由逻辑             │
        │    - 直接返回 [deployment-2]       │
        └────────────────────────────────────┘
                          ↓
        ┌────────────────────────────────────┐
        │ 4. 调用 Anthropic API              │
        │    deployment-2 → API call         │
        │    Response: cache_read_tokens ✨   │
        │    (成本降低 90%!)                  │
        └────────────────────────────────────┘
```

### 核心代码路径

1. **Pre-call Check**: `litellm/router_utils/pre_call_checks/prompt_caching_deployment_check.py`
   - `async_filter_deployments()` - 查询缓存并过滤 deployments
   - `async_log_success_event()` - 成功后保存 model_id

2. **Cache Key 生成**: `litellm/router_utils/prompt_caching_cache.py`
   - `get_prompt_caching_cache_key()` - 生成稳定的缓存键
   - `extract_cacheable_prefix()` - 提取可缓存的 messages 前缀

3. **缓存存储**: `litellm/caching/dual_cache.py`
   - `DualCache` - 同时写入内存和 Redis
   - 内存缓存: 进程内快速访问
   - Redis 缓存: 跨进程/多实例共享

---

## 核心概念

### 1. Deployment

**Definition**: Deployment 是一个独特的 API 端点配置,由 `model_name` 和 `litellm_params` 共同决定。

**Deployment ID 生成**:
```python
# 自动生成 (基于所有 litellm_params 的 SHA256 hash)
def _generate_model_id(model_group, litellm_params):
    # 包括: model, api_key, api_base, region, credentials 等所有参数
    concat_str = model_group + json.dumps(litellm_params)
    return hashlib.sha256(concat_str.encode()).hexdigest()
```

**相同 vs 不同 Deployment**:

```python
# ✅ 相同 Deployment (所有参数完全一致)
deployment_1 = {
    "model_name": "claude-sonnet",
    "litellm_params": {
        "model": "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
        "aws_access_key_id": "AKIA_ACCOUNT_A",
        "aws_region_name": "us-east-1"
    }
}

# ❌ 不同 Deployment (不同 AWS 账号)
deployment_2 = {
    "model_name": "claude-sonnet",
    "litellm_params": {
        "model": "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
        "aws_access_key_id": "AKIA_ACCOUNT_B",  # ← 不同!
        "aws_region_name": "us-east-1"
    }
}
```

**重要**: 不同的 AWS 账号/API keys = 不同的 Anthropic cache,因此需要不同的 deployment。

### 2. Cache Key

**生成逻辑**:

```python
cache_key = f"deployment:{hash}:prompt_caching"

# hash 基于:
# 1. 可缓存的 messages 前缀 (不包括 cache_control 之后的内容)
# 2. tools (如果有)
```

**可缓存前缀提取规则**:

```python
# 示例 messages
messages = [
    {
        "role": "system",
        "content": [
            {"type": "text", "text": "Block 1"},              # ← 包含 (在最后一个 cache_control 之前)
            {"type": "text", "text": "Block 2",
             "cache_control": {"type": "ephemeral"}},         # ← 包含 (最后一个有 cache_control)
        ]
    },
    {
        "role": "user",
        "content": "User question?"                          # ← 不包含 (在 cache_control 之后)
    }
]

# 可缓存前缀 = system message 的 Block 1 + Block 2
# user message 不影响 cache key!
```

**关键特性**:
- ✅ 相同的可缓存内容 → 相同的 cache key
- ✅ 不同的 user message → **相同的** cache key
- ❌ 修改任何 cache_control 之前的内容 → 不同的 cache key

### 3. DualCache (双层缓存)

**架构**:

```
┌─────────────────────────────────────────────┐
│              DualCache                      │
├─────────────────────────────────────────────┤
│                                             │
│  ┌─────────────────┐   ┌────────────────┐  │
│  │  In-Memory      │   │     Redis      │  │
│  │  Cache          │   │     Cache      │  │
│  ├─────────────────┤   ├────────────────┤  │
│  │ • 进程内        │   │ • 跨进程共享   │  │
│  │ • 最快访问      │   │ • 持久化       │  │
│  │ • 独立存储      │   │ • 分布式       │  │
│  └─────────────────┘   └────────────────┘  │
└─────────────────────────────────────────────┘

读取顺序: 内存 → Redis → None
写入策略: 同时写入内存和 Redis
```

**读取流程**:

```python
async def async_get_cache(key):
    # 1. 先查内存 (最快)
    result = await in_memory_cache.async_get_cache(key)
    if result is not None:
        return result  # 内存命中,直接返回

    # 2. 查 Redis (跨进程共享)
    result = await redis_cache.async_get_cache(key)
    if result is not None:
        # 回写到内存缓存 (warm up)
        await in_memory_cache.async_set_cache(key, result)

    return result
```

**多进程场景**:

| 场景 | 内存缓存 | Redis 缓存 | 跨进程共享 |
|------|---------|-----------|------------|
| 单进程 (无 Redis) | ✅ | ❌ | ❌ |
| 多进程 (无 Redis) | ✅ (独立) | ❌ | ❌ 不能共享 |
| 多进程 (有 Redis) | ✅ (独立) | ✅ | ✅ **可以共享** |

---

## 配置方式

### 方式 1: Python 代码 (推荐)

```python
from litellm import Router

router = Router(
    model_list=[
        {
            "model_name": "claude-sonnet",
            "litellm_params": {
                "model": "anthropic/claude-3-7-sonnet-20250219",
                "api_key": "sk-ant-key-1",
            },
            "model_info": {
                "id": "anthropic-deployment-1"  # ← 推荐手动指定
            }
        },
        {
            "model_name": "claude-sonnet",
            "litellm_params": {
                "model": "anthropic/claude-3-7-sonnet-20250219",
                "api_key": "sk-ant-key-2",
            },
            "model_info": {
                "id": "anthropic-deployment-2"
            }
        }
    ],
    routing_strategy="simple-shuffle",

    # ← 核心配置: 启用 prompt caching 路由
    optional_pre_call_checks=["prompt_caching"],

    # Redis 配置 (多进程环境必须)
    redis_host="localhost",
    redis_port=6379,
    redis_password="your-password",  # 如果需要
)

# 使用
response = await router.acompletion(
    model="claude-sonnet",
    messages=[
        {
            "role": "system",
            "content": [
                {"type": "text", "text": "System prompt..."},
                {"type": "text", "text": "Large context..." * 500,
                 "cache_control": {"type": "ephemeral"}}
            ]
        },
        {"role": "user", "content": "Your question?"}
    ]
)
```

### 方式 2: YAML 配置文件

```yaml
# config.yaml

# Model 列表
model_list:
  # Deployment 1: Anthropic 直连 (API Key 1)
  - model_name: claude-sonnet
    litellm_params:
      model: anthropic/claude-3-7-sonnet-20250219
      api_key: os.environ/ANTHROPIC_API_KEY_1
    model_info:
      id: anthropic-direct-1
      base_model: anthropic/claude-3-7-sonnet-20250219

  # Deployment 2: Anthropic 直连 (API Key 2)
  - model_name: claude-sonnet
    litellm_params:
      model: anthropic/claude-3-7-sonnet-20250219
      api_key: os.environ/ANTHROPIC_API_KEY_2
    model_info:
      id: anthropic-direct-2

  # Deployment 3: AWS Bedrock (账号 A)
  - model_name: claude-sonnet
    litellm_params:
      model: bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0
      aws_access_key_id: os.environ/AWS_ACCESS_KEY_ID_A
      aws_secret_access_key: os.environ/AWS_SECRET_ACCESS_KEY_A
      aws_region_name: us-east-1
    model_info:
      id: bedrock-us-east-1-account-a

  # Deployment 4: AWS Bedrock (账号 B)
  - model_name: claude-sonnet
    litellm_params:
      model: bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0
      aws_access_key_id: os.environ/AWS_ACCESS_KEY_ID_B
      aws_secret_access_key: os.environ/AWS_SECRET_ACCESS_KEY_B
      aws_region_name: us-west-2
    model_info:
      id: bedrock-us-west-2-account-b

# Router 设置
router_settings:
  routing_strategy: simple-shuffle
  num_retries: 2
  timeout: 300

# Redis 配置 (多进程必须)
environment_variables:
  REDIS_HOST: localhost
  REDIS_PORT: "6379"
  REDIS_PASSWORD: your-redis-password
```

```python
# 加载配置
from litellm import Router

router = Router(
    config_file_path="config.yaml",
    optional_pre_call_checks=["prompt_caching"]  # ← 在代码中添加
)
```

### 方式 3: LiteLLM Proxy

```yaml
# litellm_config.yaml
model_list:
  - model_name: claude-sonnet
    litellm_params:
      model: anthropic/claude-3-7-sonnet-20250219
      api_key: os.environ/ANTHROPIC_API_KEY
    model_info:
      id: claude-sonnet-1

  - model_name: claude-sonnet
    litellm_params:
      model: anthropic/claude-3-7-sonnet-20250219
      api_key: os.environ/ANTHROPIC_API_KEY_2
    model_info:
      id: claude-sonnet-2

environment_variables:
  REDIS_HOST: localhost
  REDIS_PORT: "6379"
```

```python
# 启动 proxy 时添加 pre-call checks
# proxy_server.py 或启动脚本中
from litellm.proxy.proxy_server import router

router.add_optional_pre_call_checks(["prompt_caching"])
```

### model_info 配置详解

**是否必须配置?** ❌ 不是必须的

- 如果不配置,会自动生成 UUID: `"a1b2c3d4-e5f6-..."`
- ⚠️ **但强烈推荐手动配置**,原因如下:

**自动生成 UUID 的问题**:

```python
# 第一次启动
deployment_id = "a1b2c3d4-..." (自动生成)
cache["hash123"] = "a1b2c3d4-..."  # 存储到 Redis

# 服务重启后
deployment_id = "f7g8h9i0-..." (新的 UUID!)
cache["hash123"] = "a1b2c3d4-..."  # Redis 中的旧 ID
# ⚠️ 找不到匹配的 deployment,cache 失效!
```

**手动指定 ID 的优势**:

```python
# 第一次启动
deployment_id = "claude-sonnet-1" (手动指定)
cache["hash123"] = "claude-sonnet-1"

# 服务重启后
deployment_id = "claude-sonnet-1" (相同!)
cache["hash123"] = "claude-sonnet-1"
# ✅ 完美匹配,cache 继续生效!
```

**推荐的命名规范**:

```yaml
model_info:
  id: "{provider}-{model}-{region/account}-{index}"

# 示例:
# - anthropic-claude-sonnet-1
# - bedrock-us-east-1-account-a
# - vertexai-us-central1-project-x
```

---

## 使用示例

### 示例 1: 基础用法 - 大型文档分析

```python
from litellm import Router

# 配置 Router
router = Router(
    model_list=[
        {
            "model_name": "claude-sonnet",
            "litellm_params": {
                "model": "anthropic/claude-3-7-sonnet-20250219",
                "api_key": "sk-ant-...",
            },
            "model_info": {"id": "claude-1"}
        },
        {
            "model_name": "claude-sonnet",
            "litellm_params": {
                "model": "anthropic/claude-3-7-sonnet-20250219",
                "api_key": "sk-ant-...",
            },
            "model_info": {"id": "claude-2"}
        }
    ],
    optional_pre_call_checks=["prompt_caching"],
    redis_host="localhost",
    redis_port=6379
)

# 准备大型文档
legal_document = """
Article 1: This agreement...
Article 2: The parties agree...
...
""" * 500  # 大量文本,确保 >= 1024 tokens

# 第一次请求
print("=== 第一次请求 ===")
response1 = await router.acompletion(
    model="claude-sonnet",
    messages=[
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "You are a legal document analyzer."
                },
                {
                    "type": "text",
                    "text": legal_document,
                    "cache_control": {"type": "ephemeral"}  # ← 缓存长文档
                }
            ]
        },
        {
            "role": "user",
            "content": "What are the key terms in Article 1?"
        }
    ]
)

# 检查使用情况
print(f"Model ID: {response1._hidden_params.get('model_id')}")
print(f"Prompt tokens: {response1.usage.prompt_tokens}")
print(f"Cache creation: {response1.usage.cache_creation_input_tokens}")
print(f"Cache read: {response1.usage.cache_read_input_tokens}")

# 输出:
# Model ID: claude-1
# Prompt tokens: 5000
# Cache creation: 4800  ← 首次创建缓存
# Cache read: 0

# 第二次请求 (不同问题,相同文档)
print("\n=== 第二次请求 (5秒后) ===")
await asyncio.sleep(5)

response2 = await router.acompletion(
    model="claude-sonnet",
    messages=[
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "You are a legal document analyzer."
                },
                {
                    "type": "text",
                    "text": legal_document,  # ← 相同的文档
                    "cache_control": {"type": "ephemeral"}
                }
            ]
        },
        {
            "role": "user",
            "content": "Summarize Article 2?"  # ← 不同的问题
        }
    ]
)

print(f"Model ID: {response2._hidden_params.get('model_id')}")
print(f"Prompt tokens: {response2.usage.prompt_tokens}")
print(f"Cache creation: {response2.usage.cache_creation_input_tokens}")
print(f"Cache read: {response2.usage.cache_read_input_tokens}")

# 输出:
# Model ID: claude-1  ← 相同的 deployment!
# Prompt tokens: 5000
# Cache creation: 0
# Cache read: 4800  ← 命中缓存!成本节省 90%
```

### 示例 2: 多轮对话场景

```python
# 场景: 客服机器人,多个用户使用相同的 system prompt
router = Router(
    model_list=[...],
    optional_pre_call_checks=["prompt_caching"],
    redis_host="localhost",
    redis_port=6379
)

# 共享的 system prompt
CUSTOMER_SERVICE_PROMPT = """
You are a helpful customer service assistant for Acme Corp.

Company Policies:
- Return policy: 30 days...
- Shipping: Free shipping on orders over $50...
- Warranty: 1 year warranty on all products...
...
""" * 300  # 确保 >= 1024 tokens

# 用户 A 的第一次对话
async def handle_user_a_conversation():
    # 第一轮
    response1 = await router.acompletion(
        model="claude-sonnet",
        messages=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": CUSTOMER_SERVICE_PROMPT,
                        "cache_control": {"type": "ephemeral", "ttl": "1h"}
                    }
                ]
            },
            {"role": "user", "content": "How do I return a product?"}
        ]
    )
    # → 创建缓存,路由到 deployment-X

    # 第二轮 (继续缓存历史对话)
    response2 = await router.acompletion(
        model="claude-sonnet",
        messages=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": CUSTOMER_SERVICE_PROMPT,
                        "cache_control": {"type": "ephemeral", "ttl": "1h"}
                    }
                ]
            },
            {"role": "user", "content": "How do I return a product?"},
            {"role": "assistant", "content": response1.choices[0].message.content},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "What if it's been 35 days?",
                        "cache_control": {"type": "ephemeral"}  # 缓存对话历史
                    }
                ]
            }
        ]
    )
    # → 命中缓存,继续使用 deployment-X

# 用户 B 的第一次对话 (同时进行)
async def handle_user_b_conversation():
    response = await router.acompletion(
        model="claude-sonnet",
        messages=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": CUSTOMER_SERVICE_PROMPT,  # ← 相同的 system prompt
                        "cache_control": {"type": "ephemeral", "ttl": "1h"}
                    }
                ]
            },
            {"role": "user", "content": "What's your shipping policy?"}
        ]
    )
    # → 命中缓存,也路由到 deployment-X!
    # → 不同用户,相同 system prompt,共享缓存

# 并发执行
await asyncio.gather(
    handle_user_a_conversation(),
    handle_user_b_conversation()
)
```

### 示例 3: 多 AWS 账号负载均衡

```python
# 场景: 使用多个 AWS 账号以绕过 rate limit
router = Router(
    model_list=[
        # AWS 账号 A - us-east-1
        {
            "model_name": "claude-sonnet",
            "litellm_params": {
                "model": "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
                "aws_access_key_id": "AKIA_ACCOUNT_A",
                "aws_secret_access_key": "secret_a",
                "aws_region_name": "us-east-1"
            },
            "model_info": {
                "id": "bedrock-us-east-1-account-a"
            }
        },
        # AWS 账号 B - us-east-1
        {
            "model_name": "claude-sonnet",
            "litellm_params": {
                "model": "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
                "aws_access_key_id": "AKIA_ACCOUNT_B",
                "aws_secret_access_key": "secret_b",
                "aws_region_name": "us-east-1"
            },
            "model_info": {
                "id": "bedrock-us-east-1-account-b"
            }
        },
        # AWS 账号 C - us-west-2
        {
            "model_name": "claude-sonnet",
            "litellm_params": {
                "model": "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
                "aws_access_key_id": "AKIA_ACCOUNT_C",
                "aws_secret_access_key": "secret_c",
                "aws_region_name": "us-west-2"
            },
            "model_info": {
                "id": "bedrock-us-west-2-account-c"
            }
        }
    ],
    optional_pre_call_checks=["prompt_caching"],
    routing_strategy="simple-shuffle",
    redis_host="localhost",
    redis_port=6379
)

# 使用
# 第一次请求: 随机选择 → 假设选中 bedrock-us-east-1-account-b
response1 = await router.acompletion(
    model="claude-sonnet",
    messages=[
        {
            "role": "system",
            "content": [
                {"type": "text", "text": "System prompt..."},
                {"type": "text", "text": "Large context..." * 500,
                 "cache_control": {"type": "ephemeral"}}
            ]
        },
        {"role": "user", "content": "Question 1?"}
    ]
)
# → deployment: bedrock-us-east-1-account-b
# → cache key 保存: hash123 → bedrock-us-east-1-account-b

# 后续请求: 强制路由到 bedrock-us-east-1-account-b
response2 = await router.acompletion(
    model="claude-sonnet",
    messages=[
        {
            "role": "system",
            "content": [
                {"type": "text", "text": "System prompt..."},
                {"type": "text", "text": "Large context..." * 500,
                 "cache_control": {"type": "ephemeral"}}
            ]
        },
        {"role": "user", "content": "Question 2?"}  # 不同问题
    ]
)
# → deployment: bedrock-us-east-1-account-b (强制相同)
# → 在 AWS 账号 B 的 Anthropic cache 中命中!
```

### 示例 4: 监控和调试

```python
import json
from datetime import datetime

# 添加日志记录
class PromptCachingMonitor:
    def __init__(self):
        self.stats = {
            "total_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "deployments_used": {},
            "cost_saved": 0.0
        }

    def log_request(self, response):
        self.stats["total_requests"] += 1

        # 提取信息
        model_id = response._hidden_params.get("model_id", "unknown")
        cache_read = response.usage.cache_read_input_tokens or 0
        cache_create = response.usage.cache_creation_input_tokens or 0

        # 记录 deployment 使用
        if model_id not in self.stats["deployments_used"]:
            self.stats["deployments_used"][model_id] = 0
        self.stats["deployments_used"][model_id] += 1

        # 判断 cache hit/miss
        if cache_read > 0:
            self.stats["cache_hits"] += 1
            # 计算成本节省 (Claude 3.5 Sonnet 定价)
            normal_cost = cache_read * 3.00 / 1_000_000
            cache_cost = cache_read * 0.30 / 1_000_000
            self.stats["cost_saved"] += (normal_cost - cache_cost)
        else:
            self.stats["cache_misses"] += 1

        # 打印详细日志
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Request #{self.stats['total_requests']}")
        print(f"  Deployment: {model_id}")
        print(f"  Prompt tokens: {response.usage.prompt_tokens}")
        print(f"  Cache create: {cache_create}")
        print(f"  Cache read: {cache_read} {'✅ HIT' if cache_read > 0 else '❌ MISS'}")
        print()

    def print_summary(self):
        hit_rate = (self.stats["cache_hits"] / self.stats["total_requests"] * 100
                   if self.stats["total_requests"] > 0 else 0)

        print("\n" + "="*60)
        print("PROMPT CACHING SUMMARY")
        print("="*60)
        print(f"Total Requests: {self.stats['total_requests']}")
        print(f"Cache Hits: {self.stats['cache_hits']}")
        print(f"Cache Misses: {self.stats['cache_misses']}")
        print(f"Hit Rate: {hit_rate:.1f}%")
        print(f"Cost Saved: ${self.stats['cost_saved']:.4f}")
        print("\nDeployments Used:")
        for deployment, count in self.stats["deployments_used"].items():
            print(f"  - {deployment}: {count} requests")
        print("="*60)

# 使用监控器
monitor = PromptCachingMonitor()

# 执行多次请求
for i in range(10):
    response = await router.acompletion(
        model="claude-sonnet",
        messages=[...]
    )
    monitor.log_request(response)
    await asyncio.sleep(1)

# 打印总结
monitor.print_summary()

# 输出示例:
# ============================================================
# PROMPT CACHING SUMMARY
# ============================================================
# Total Requests: 10
# Cache Hits: 9
# Cache Misses: 1
# Hit Rate: 90.0%
# Cost Saved: $0.1215
#
# Deployments Used:
#   - claude-1: 10 requests
# ============================================================
```

### 示例 5: 与 Streaming 结合使用

```python
# Streaming 模式也支持 Prompt Caching
response = await router.acompletion(
    model="claude-sonnet",
    messages=[
        {
            "role": "system",
            "content": [
                {"type": "text", "text": "System prompt..."},
                {"type": "text", "text": "Large context..." * 500,
                 "cache_control": {"type": "ephemeral"}}
            ]
        },
        {"role": "user", "content": "Explain this in detail."}
    ],
    stream=True,
    stream_options={"include_usage": True}  # ← 重要: 获取 usage 信息
)

print("Streaming response:")
async for chunk in response:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)

    # 最后一个 chunk 包含 usage 信息
    if hasattr(chunk, "usage") and chunk.usage:
        print("\n\nUsage:")
        print(f"  Cache read: {chunk.usage.cache_read_input_tokens}")
        print(f"  Cache create: {chunk.usage.cache_creation_input_tokens}")
        print(f"  Total prompt: {chunk.usage.prompt_tokens}")
```

---

## 最佳实践

### 1. 始终配置 Redis (生产环境)

```python
# ❌ 不推荐: 无 Redis
router = Router(
    model_list=[...],
    optional_pre_call_checks=["prompt_caching"]
    # 没有 Redis 配置
)
# 问题: 多进程/多实例无法共享缓存

# ✅ 推荐: 配置 Redis
router = Router(
    model_list=[...],
    optional_pre_call_checks=["prompt_caching"],
    redis_host="your-redis-host",
    redis_port=6379,
    redis_password="your-password"
)
```

### 2. 手动指定 model_info.id

```python
# ❌ 不推荐: 使用自动生成的 UUID
{
    "model_name": "claude-sonnet",
    "litellm_params": {...}
    # 没有 model_info
}
# 问题: 重启后 ID 改变,cache 映射失效

# ✅ 推荐: 手动指定有意义的 ID
{
    "model_name": "claude-sonnet",
    "litellm_params": {...},
    "model_info": {
        "id": "claude-sonnet-prod-1"  # 稳定的 ID
    }
}
```

### 3. 合理设置 cache_control TTL

```python
# 场景 1: 短期对话 (默认 5 分钟)
{
    "type": "text",
    "text": content,
    "cache_control": {"type": "ephemeral"}  # 5 分钟
}

# 场景 2: 长期上下文 (1 小时)
{
    "type": "text",
    "text": content,
    "cache_control": {"type": "ephemeral", "ttl": "1h"}
}

# 策略建议:
# - System prompt: 1h (很少变化)
# - 文档/知识库: 1h (静态内容)
# - 对话历史: 5m (快速迭代)
```

### 4. 分层缓存策略

```python
messages = [
    # 第 1 层: 系统指令 (最稳定,1 小时)
    {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": "You are an expert assistant...",
                "cache_control": {"type": "ephemeral", "ttl": "1h"}
            }
        ]
    },
    # 第 2 层: 长期上下文 (静态文档,1 小时)
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "Here is a large knowledge base: ...",
                "cache_control": {"type": "ephemeral", "ttl": "1h"}
            }
        ]
    },
    # 第 3 层: 对话历史 (动态内容,5 分钟)
    {
        "role": "assistant",
        "content": "Previous response..."
    },
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "Follow-up question",
                "cache_control": {"type": "ephemeral"}  # 5 分钟
            }
        ]
    }
]
```

### 5. 监控缓存效果

```python
def analyze_cache_effectiveness(responses):
    """分析 cache 命中率和成本节省"""
    total = len(responses)
    hits = sum(1 for r in responses if r.usage.cache_read_input_tokens > 0)

    total_cache_read = sum(r.usage.cache_read_input_tokens or 0 for r in responses)

    # Claude 3.5 Sonnet 定价
    normal_cost = total_cache_read * 3.00 / 1_000_000
    cache_cost = total_cache_read * 0.30 / 1_000_000
    savings = normal_cost - cache_cost

    print(f"Cache Hit Rate: {hits/total*100:.1f}%")
    print(f"Total Cache Tokens: {total_cache_read}")
    print(f"Cost Savings: ${savings:.4f}")
    print(f"Cost Reduction: {(1 - cache_cost/normal_cost)*100:.1f}%")
```

### 6. 错误处理和降级

```python
async def safe_completion_with_cache(router, model, messages):
    """带错误处理的 completion"""
    try:
        # 首次尝试: 使用 prompt caching
        response = await router.acompletion(
            model=model,
            messages=messages,
            timeout=30
        )
        return response

    except Exception as e:
        # 如果缓存路由失败,可以降级到普通路由
        print(f"Prompt caching failed: {e}")

        # 选项 1: 重试,不使用 cache_control
        messages_no_cache = remove_cache_control(messages)
        return await router.acompletion(
            model=model,
            messages=messages_no_cache
        )

def remove_cache_control(messages):
    """移除所有 cache_control"""
    import copy
    messages_copy = copy.deepcopy(messages)

    for message in messages_copy:
        if isinstance(message.get("content"), list):
            for block in message["content"]:
                if isinstance(block, dict):
                    block.pop("cache_control", None)
        message.pop("cache_control", None)

    return messages_copy
```

### 7. 确保最小 Token 要求

```python
from litellm.utils import token_counter

def is_prompt_cacheable(messages, tools=None):
    """检查 prompt 是否满足缓存要求"""
    token_count = token_counter(
        messages=messages,
        tools=tools,
        model="claude-3-7-sonnet-20250219"
    )

    min_tokens = 1024
    if token_count < min_tokens:
        print(f"⚠️ Warning: Prompt only has {token_count} tokens, "
              f"needs {min_tokens} for caching")
        return False

    return True

# 使用前检查
if is_prompt_cacheable(messages):
    response = await router.acompletion(model="claude-sonnet", messages=messages)
else:
    print("Prompt too short for caching, consider adding more context")
```

---

## 故障排查

### 问题 1: Cache 没有命中

**症状**:
```python
response.usage.cache_read_input_tokens == 0  # 总是 0
```

**可能原因和解决方案**:

#### 1.1 Prompt 小于 1024 tokens

```python
# 检查
from litellm.utils import token_counter

token_count = token_counter(
    messages=messages,
    model="claude-3-7-sonnet-20250219"
)
print(f"Token count: {token_count}")

# 解决: 确保 prompt >= 1024 tokens
if token_count < 1024:
    print("❌ Prompt too short for caching")
```

#### 1.2 没有启用 prompt_caching pre-call check

```python
# 检查
print(router.optional_pre_call_checks)

# 解决: 添加配置
router = Router(
    model_list=[...],
    optional_pre_call_checks=["prompt_caching"]  # ← 必须添加!
)
```

#### 1.3 cache_control 位置不正确

```python
# ❌ 错误: cache_control 在中间的 block
{
    "role": "user",
    "content": [
        {"type": "text", "text": "Part 1",
         "cache_control": {"type": "ephemeral"}},  # ← 错误位置
        {"type": "text", "text": "Part 2"}
    ]
}

# ✅ 正确: cache_control 在最后一个 block
{
    "role": "user",
    "content": [
        {"type": "text", "text": "Part 1"},
        {"type": "text", "text": "Part 2",
         "cache_control": {"type": "ephemeral"}}  # ← 正确位置
    ]
}
```

#### 1.4 可缓存内容发生变化

```python
# 第一次请求
messages1 = [
    {
        "role": "system",
        "content": [
            {"type": "text", "text": "Version 1 of context",
             "cache_control": {"type": "ephemeral"}}
        ]
    }
]

# 第二次请求 (内容改变)
messages2 = [
    {
        "role": "system",
        "content": [
            {"type": "text", "text": "Version 2 of context",  # ← 内容改变!
             "cache_control": {"type": "ephemeral"}}
        ]
    }
]

# 结果: cache key 不同,无法命中
```

#### 1.5 Cache 过期 (TTL 超时)

```python
# 检查 TTL
# 默认: 5 分钟
# 1h: 60 分钟

# 第一次请求
response1 = await router.acompletion(...)  # t=0

# 6 分钟后的请求
await asyncio.sleep(360)  # 超过 5 分钟 TTL
response2 = await router.acompletion(...)  # t=360s
# ❌ cache 已过期,无法命中

# 解决: 使用更长的 TTL
{"cache_control": {"type": "ephemeral", "ttl": "1h"}}
```

### 问题 2: 路由到不同的 Deployment

**症状**:
```python
response1._hidden_params["model_id"] = "deployment-1"
response2._hidden_params["model_id"] = "deployment-2"  # 不同!
```

**可能原因和解决方案**:

#### 2.1 没有配置 Redis (多进程)

```python
# 问题: 进程 A 的缓存,进程 B 拿不到
# 解决: 配置 Redis

router = Router(
    model_list=[...],
    optional_pre_call_checks=["prompt_caching"],
    redis_host="localhost",  # ← 添加 Redis
    redis_port=6379
)
```

#### 2.2 model_info.id 不稳定 (使用了 UUID)

```python
# 检查配置
for deployment in router.model_list:
    print(f"ID: {deployment['model_info']['id']}")

# 如果看到 UUID: "a1b2c3d4-..."
# 解决: 手动指定 ID
model_list = [
    {
        "model_name": "claude-sonnet",
        "litellm_params": {...},
        "model_info": {
            "id": "claude-sonnet-1"  # ← 手动指定
        }
    }
]
```

#### 2.3 Cache key 已过期

```python
# 检查 Router 的 cache TTL (默认 300 秒)
# 解决: 确保请求间隔 < 5 分钟

# 或者延长 TTL (在缓存存储时)
await cache.async_add_model_id(
    model_id=model_id,
    messages=messages,
    tools=None,
    ttl=600  # 延长到 10 分钟
)
```

### 问题 3: Redis 连接失败

**症状**:
```python
ConnectionError: Error connecting to Redis
```

**解决方案**:

```python
# 检查 Redis 连接
import redis

try:
    r = redis.Redis(host='localhost', port=6379, password='your-password')
    r.ping()
    print("✅ Redis connection OK")
except Exception as e:
    print(f"❌ Redis connection failed: {e}")

# 常见问题:
# 1. Redis 未启动
#    解决: redis-server
#
# 2. 端口被占用
#    解决: 检查 redis.conf 中的端口配置
#
# 3. 密码错误
#    解决: 检查 REDIS_PASSWORD 环境变量
#
# 4. 防火墙阻止
#    解决: 允许 6379 端口
```

### 问题 4: 调试模式

```python
# 启用详细日志
import litellm
litellm.set_verbose = True

# 查看 Router 内部状态
router = Router(...)

# 检查 pre-call checks
print(f"Pre-call checks: {litellm.callbacks}")

# 检查 deployments
for i, deployment in enumerate(router.model_list):
    print(f"\nDeployment {i}:")
    print(f"  Model: {deployment['litellm_params']['model']}")
    print(f"  ID: {deployment['model_info']['id']}")

# 检查缓存内容
if router.cache.redis_cache:
    # 列出所有 prompt caching 相关的 keys
    keys = router.cache.redis_cache.redis_client.keys("deployment:*:prompt_caching")
    for key in keys:
        value = router.cache.redis_cache.redis_client.get(key)
        print(f"Cache: {key} → {value}")
```

---

## 常见问题 FAQ

### Q1: Prompt Caching 会增加延迟吗?

**A**: 几乎没有影响

- **首次请求**: 额外 < 10ms (查询缓存未命中)
- **后续请求**: 额外 < 5ms (内存缓存命中)
- **Anthropic 侧**: Cache hit 反而**更快** (减少 token 处理)

### Q2: 可以同时使用多个 pre-call checks 吗?

**A**: 可以

```python
router = Router(
    model_list=[...],
    optional_pre_call_checks=[
        "prompt_caching",           # Prompt caching 路由
        "router_budget_limiting",   # 预算限制
    ]
)
```

### Q3: 不同用户的 cache 会互相影响吗?

**A**: 不会

- Anthropic 的 cache 基于 API key 隔离
- 不同 AWS 账号的 cache 完全独立
- LiteLLM Router 通过 deployment ID 区分

### Q4: Cache hit rate 低怎么办?

**分析原因**:

```python
# 检查 1: Token 数量是否足够?
token_count = token_counter(messages=messages, model="...")
print(f"Tokens: {token_count} (需要 >= 1024)")

# 检查 2: 可缓存内容是否稳定?
# 提取可缓存前缀并打印
from litellm.router_utils.prompt_caching_cache import PromptCachingCache
cacheable = PromptCachingCache.extract_cacheable_prefix(messages)
print(f"Cacheable content: {cacheable}")

# 检查 3: 请求间隔是否 < 5 分钟?
# 如果间隔太长,考虑使用 ttl="1h"

# 检查 4: 是否使用了稳定的 model_info.id?
```

### Q5: 如何验证 Prompt Caching 是否生效?

**验证方法**:

```python
# 方法 1: 检查 usage
response = await router.acompletion(...)

if response.usage.cache_read_input_tokens > 0:
    print("✅ Prompt Caching 生效!")
else:
    print("❌ Prompt Caching 未生效")

# 方法 2: 检查 model_id
model_id_1 = response1._hidden_params.get("model_id")
model_id_2 = response2._hidden_params.get("model_id")

if model_id_1 == model_id_2:
    print("✅ 路由到相同 deployment")
else:
    print("❌ 路由到不同 deployment")

# 方法 3: 检查 Redis 缓存
from litellm.router_utils.prompt_caching_cache import PromptCachingCache

cache_key = PromptCachingCache.get_prompt_caching_cache_key(messages, tools=None)
print(f"Cache key: {cache_key}")

cached_value = await router.cache.async_get_cache(cache_key)
print(f"Cached model_id: {cached_value}")
```

### Q6: 可以在 Vertex AI 上使用吗?

**A**: 可以,但有限制

```python
# Vertex AI 配置
router = Router(
    model_list=[
        {
            "model_name": "claude-sonnet",
            "litellm_params": {
                "model": "vertex_ai/claude-3-5-sonnet-v2@20241022",
                "vertex_project": "your-project",
                "vertex_location": "us-central1"
            },
            "model_info": {"id": "vertex-claude-1"}
        }
    ],
    optional_pre_call_checks=["prompt_caching"]
)

# ⚠️ 注意: Vertex AI 不支持 anthropic-beta headers
# 但 cache_control 字段仍然有效
```

### Q7: 成本节省如何计算?

**计算公式** (以 Claude 3.5 Sonnet 为例):

```python
# 定价 (per 1M tokens)
NORMAL_INPUT = 3.00      # 普通 input
CACHE_WRITE = 3.75       # 写入缓存 (+25%)
CACHE_READ = 0.30        # 读取缓存 (-90%)

# 场景: 5000 tokens 的 prompt,使用 10 次
tokens = 5000
requests = 10

# 不使用缓存
cost_no_cache = tokens * requests * NORMAL_INPUT / 1_000_000
print(f"不使用缓存: ${cost_no_cache:.4f}")
# = 5000 * 10 * 3.00 / 1M = $0.1500

# 使用缓存
cost_write = tokens * CACHE_WRITE / 1_000_000
cost_read = tokens * (requests - 1) * CACHE_READ / 1_000_000
cost_with_cache = cost_write + cost_read
print(f"使用缓存: ${cost_with_cache:.4f}")
# = (5000 * 3.75 / 1M) + (5000 * 9 * 0.30 / 1M) = $0.0322

# 节省
savings = cost_no_cache - cost_with_cache
print(f"节省: ${savings:.4f} ({savings/cost_no_cache*100:.1f}%)")
# = $0.1178 (78.5%)
```

### Q8: 如何清除缓存?

```python
# 清除特定 cache key
cache_key = PromptCachingCache.get_prompt_caching_cache_key(messages, tools=None)
await router.cache.async_delete_cache(cache_key)

# 清除所有缓存
router.cache.flush_cache()

# 只清除 Redis (保留内存)
if router.cache.redis_cache:
    router.cache.redis_cache.flush_cache()
```

### Q9: 支持其他模型吗?

**A**: 目前仅支持 Anthropic Claude

- ✅ Anthropic Claude 3.x / 4.x (直连)
- ✅ AWS Bedrock Claude
- ✅ Vertex AI Claude
- ❌ OpenAI (不支持 prompt caching)
- ❌ 其他模型

**检测**:

```python
from litellm.utils import is_prompt_caching_valid_prompt

# 检查模型是否支持
is_supported = is_prompt_caching_valid_prompt(
    model="anthropic/claude-3-7-sonnet-20250219",
    messages=messages
)
```

### Q10: 线上环境部署建议?

**推荐配置**:

```python
# 生产环境配置
router = Router(
    model_list=[
        # 多个 deployments 实现负载均衡
        {...},
        {...},
        {...}
    ],

    # 核心配置
    optional_pre_call_checks=["prompt_caching"],
    routing_strategy="simple-shuffle",

    # Redis 配置 (必须!)
    redis_host="your-redis-cluster",
    redis_port=6379,
    redis_password="strong-password",

    # 重试和超时
    num_retries=2,
    timeout=300,

    # 错误处理
    fallbacks=[
        {"gpt-4": ["claude-sonnet"]}  # 降级策略
    ]
)

# 监控
# - 定期检查 cache hit rate
# - 监控 Redis 连接状态
# - 记录 deployment 使用分布
# - 追踪成本节省情况
```

---

## 相关资源

### 官方文档

- [Anthropic Prompt Caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)
- [LiteLLM Router](https://docs.litellm.ai/docs/routing)
- [LiteLLM Caching](https://docs.litellm.ai/docs/caching/redis_cache)

### 代码位置

- Router 核心: `litellm/router.py`
- Pre-call Check: `litellm/router_utils/pre_call_checks/prompt_caching_deployment_check.py`
- Cache Key 生成: `litellm/router_utils/prompt_caching_cache.py`
- DualCache: `litellm/caching/dual_cache.py`

### 测试用例

- Router 测试: `tests/router_unit_tests/test_router_prompt_caching.py`
- Anthropic 测试: `tests/local_testing/test_anthropic_prompt_caching.py`

### 相关文档

- [Anthropic Cache Control 实现文档](./anthropic_cache_control_implementation.md)
- [Anthropic Beta Header 实现文档](./anthropic_beta_header_implementation.md)

---

## 总结

LiteLLM Router 的 Prompt Caching 功能通过智能路由机制,确保相同可缓存内容的请求路由到同一个 deployment,从而最大化 Anthropic Prompt Caching 的效果。

**关键要点**:

1. ✅ 配置 `optional_pre_call_checks=["prompt_caching"]`
2. ✅ 生产环境使用 Redis
3. ✅ 手动指定稳定的 `model_info.id`
4. ✅ 确保 prompt >= 1024 tokens
5. ✅ 合理设置 `cache_control` 位置和 TTL

**收益**:

- 💰 成本降低高达 90%
- ⚡ 响应速度提升
- 🎯 自动化智能路由
- 📊 易于监控和调试

---


## 如何验证 Redis 中的 cache key。

  Redis Cache Key 格式

  根据代码分析，Redis 中的 cache key 格式为：

  deployment:{SHA256_hash}:prompt_caching

  Key 的组成部分

  1. 前缀: deployment:
  2. Hash 值: 64 字符的 SHA256 hash
  3. 后缀: :prompt_caching

  Hash 计算规则

  Hash 是基于以下内容计算的：
  - 可缓存的 messages 前缀（只包含到最后一个带 cache_control 的 content block）
  - tools（如果有）

  关键代码在 litellm/router_utils/prompt_caching_cache.py:157：
  return f"deployment:{hashed_data}:prompt_caching"

  如何在 Redis 中查看这些 Keys

  方法 1: 使用 redis-cli

  # 连接到你的 Redis 服务器
  redis-cli -h your-redis-host -p 6379 -a your-password

  # 列出所有 prompt caching 相关的 keys
  KEYS deployment:*:prompt_caching

  # 查看某个 key 的值
  GET deployment:{hash}:prompt_caching

  # 查看 key 的 TTL（剩余过期时间）
  TTL deployment:{hash}:prompt_caching

  方法 2: 使用 Python 脚本

  import redis

  # 连接到你的 Redis
  r = redis.Redis(
      host='your-redis-host',
      port=6379,
      password='your-password',
      decode_responses=True
  )

  # 测试连接
  r.ping()

  # 列出所有 prompt caching keys
  keys = r.keys("deployment:*:prompt_caching")
  print(f"找到 {len(keys)} 个 keys")

  # 查看每个 key 的详细信息
  for key in keys:
      value = r.get(key)  # 存储的是 model_id
      ttl = r.ttl(key)    # TTL（秒）
      print(f"Key: {key}")
      print(f"Value (model_id): {value}")
      print(f"TTL: {ttl}s ({ttl//60}分钟)")
      print()

  方法 3: 使用 Redis Desktop Manager 等 GUI 工具

  1. 连接到你的 Redis 服务器
  2. 搜索 pattern: deployment:*:prompt_caching
  3. 查看匹配的 keys

  Key 的值是什么

  存储在 Redis 中的值是一个 JSON 对象：
  {"model_id": "your-deployment-id"}

  例如：
  {"model_id": "claude-sonnet-1"}

  Key 示例

  假设你有这样的 messages：

  messages = [
      {
          "role": "system",
          "content": [
              {"type": "text", "text": "You are a helpful assistant."},
              {
                  "type": "text",
                  "text": "Large context...",
                  "cache_control": {"type": "ephemeral"}
              }
          ]
      },
      {
          "role": "user",
          "content": "What is Python?"
      }
  ]

  生成的 cache key 可能是：
  deployment:a1b2c3d4e5f6789012345678901234567890123456789012345678901234:prompt_caching

  重要特性

  1. 相同的可缓存前缀 = 相同的 cache key
    - 即使 user message 不同，如果 system prompt 相同（且有 cache_control），cache key 也相同
  2. TTL = 300 秒（5 分钟）
    - 代码位置: litellm/router_utils/prompt_caching_cache.py:195
    - 超过 5 分钟后，key 会自动过期
  3. 只有可缓存的内容参与 hash 计算
    - cache_control 之后的内容不会影响 cache key

  快速验证命令

  如果你想快速查看当前 Redis 中有哪些 prompt caching keys：

  redis-cli -h your-host -p 6379 -a your-password KEYS "deployment:*:prompt_caching" | wc -l

  这会告诉你有多少个 prompt caching keys。


**维护者**: LiteLLM Team
**最后更新**: 2025-12-22
**版本**: 1.0.0
