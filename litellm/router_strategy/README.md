# LiteLLM 路由策略文档

本文档详细介绍了 LiteLLM Router 中的所有路由策略实现。

## 目录

- [概述](#概述)
- [核心架构](#核心架构)
- [路由策略详解](#路由策略详解)
  - [1. 最少繁忙策略 (Least Busy)](#1-最少繁忙策略-least-busy)
  - [2. 最低成本策略 (Lowest Cost)](#2-最低成本策略-lowest-cost)
  - [3. 最低延迟策略 (Lowest Latency)](#3-最低延迟策略-lowest-latency)
  - [4. 最低 TPM/RPM 策略 (Lowest TPM/RPM)](#4-最低-tpmrpm-策略-lowest-tpmrpm)
  - [5. 最低 TPM/RPM 策略 V2](#5-最低-tpmrpm-策略-v2)
  - [6. 简单随机策略 (Simple Shuffle)](#6-简单随机策略-simple-shuffle)
  - [7. 基于标签的路由 (Tag-Based Routing)](#7-基于标签的路由-tag-based-routing)
  - [8. 预算限制器 (Budget Limiter)](#8-预算限制器-budget-limiter)
  - [9. 自动路由 (Auto Router)](#9-自动路由-auto-router)
- [使用示例](#使用示例)

---

## 概述

LiteLLM Router 支持多种路由策略，用于在多个部署（Deployment）之间智能分配请求。每种策略都有不同的优化目标：

- **性能优化**: 最低延迟、最少繁忙
- **成本优化**: 最低成本
- **限流管理**: 最低 TPM/RPM
- **灵活分配**: 简单随机、权重分配
- **高级路由**: 标签路由、预算限制、语义路由

---

## 核心架构

### 基础路由策略类 (BaseRoutingStrategy)

位置: `litellm/router_strategy/base_routing_strategy.py`

所有路由策略的基类，提供了以下核心功能：

**主要功能:**
1. **缓存管理**: 使用 DualCache (内存 + Redis) 进行双层缓存
2. **批量 Redis 写入**: 优化 Redis 写入性能，避免频繁的单次操作
3. **定期同步**: 周期性地将内存缓存与 Redis 同步，适用于多实例环境
4. **增量操作**: 提供批量增量操作接口

**关键方法:**
```python
async def _increment_value_in_current_window(key, value, ttl)
    # 在当前时间窗口内增量更新值

async def _sync_in_memory_spend_with_redis()
    # 同步内存缓存和 Redis 缓存

async def periodic_sync_in_memory_spend_with_redis(interval)
    # 周期性同步任务
```

---

## 路由策略详解

### 1. 最少繁忙策略 (Least Busy)

**文件**: `least_busy.py`

**策略描述**: 选择当前正在处理请求数最少的部署

**工作原理**:
1. **请求前**: 在 `log_pre_api_call` 中记录部署的请求计数 +1
2. **请求成功/失败后**: 在回调中将请求计数 -1
3. **选择策略**: 选择 `request_count` 最小的部署

**缓存结构**:
```python
{
    "{model_group}_request_count": {
        "deployment_id_1": 5,  # 当前正在处理 5 个请求
        "deployment_id_2": 2,  # 当前正在处理 2 个请求
    }
}
```

**适用场景**:
- 需要均衡负载
- 避免单个部署过载
- 实时流量分配

**优点**:
- 实时响应负载变化
- 避免请求堆积

**缺点**:
- 不考虑请求复杂度
- 不考虑响应时间

---

### 2. 最低成本策略 (Lowest Cost)

**文件**: `lowest_cost.py`

**策略描述**: 选择成本最低的可用部署

**工作原理**:
1. **记录使用量**: 记录每个部署在每分钟的 TPM 和 RPM
2. **计算成本**: 基于 `input_cost_per_token` 和 `output_cost_per_token`
3. **过滤部署**: 排除超过 TPM/RPM 限制的部署
4. **选择最低成本**: 从可用部署中选择成本最低的

**缓存结构**:
```python
{
    "{model_group}_map": {
        "deployment_id": {
            "2024-12-18-14-30": {
                "tpm": 1000,  # 该分钟已使用的 token 数
                "rpm": 5      # 该分钟的请求数
            }
        }
    }
}
```

**成本计算**:
```python
item_cost = input_cost_per_token + output_cost_per_token
```

**适用场景**:
- 成本敏感型应用
- 需要在多个定价模型间选择
- 预算控制

**配置示例**:
```yaml
model_list:
  - model_name: gpt-4
    litellm_params:
      model: openai/gpt-4
      input_cost_per_token: 0.00003
      output_cost_per_token: 0.00006
```

---

### 3. 最低延迟策略 (Lowest Latency)

**文件**: `lowest_latency.py`

**策略描述**: 选择平均响应延迟最低的部署

**工作原理**:
1. **记录延迟**: 记录每次请求的延迟时间
2. **区分流式/非流式**:
   - 非流式: 记录完整响应时间
   - 流式: 记录首个 token 响应时间 (TTFT)
3. **计算平均延迟**: 保留最近 N 次请求的延迟（可配置）
4. **选择策略**: 选择平均延迟最低的部署

**缓存结构**:
```python
{
    "{model_group}_map": {
        "deployment_id": {
            "latency": [0.5, 0.6, 0.4, ...],  # 最近 N 次的延迟
            "time_to_first_token": [0.1, 0.15, ...],  # 流式请求的 TTFT
            "2024-12-18-14-30": {
                "tpm": 1000,
                "rpm": 5
            }
        }
    }
}
```

**配置参数**:
```python
class RoutingArgs:
    ttl: float = 1 * 60 * 60  # 缓存 TTL: 1 小时
    lowest_latency_buffer: float = 0  # 延迟缓冲区
    max_latency_list_size: int = 10  # 保存的延迟记录数
```

**延迟计算**:
```python
# 非流式
final_value = response_time / completion_tokens

# 流式
time_to_first_token = first_token_time / completion_tokens
```

**适用场景**:
- 对响应时间敏感的应用
- 需要优化用户体验
- 实时交互场景

**特殊处理**:
- 超时错误: 给予 1000 秒的惩罚分数
- 缓冲区策略: 可以设置 `lowest_latency_buffer` 在延迟接近的部署中随机选择

---

### 4. 最低 TPM/RPM 策略 (Lowest TPM/RPM)

**文件**: `lowest_tpm_rpm.py`

**策略描述**: 选择当前 TPM (Tokens Per Minute) 或 RPM (Requests Per Minute) 使用率最低的部署

**工作原理**:
1. **记录使用量**: 按分钟记录每个部署的 TPM 和 RPM
2. **过滤部署**: 排除已超过配置的 TPM/RPM 限制的部署
3. **选择策略**: 选择 TPM 最低的可用部署

**缓存结构**:
```python
# TPM 缓存
"{model_group}:tpm:{HH-MM}": {
    "deployment_id_1": 5000,  # 该分钟已使用 5000 tokens
    "deployment_id_2": 3000
}

# RPM 缓存
"{model_group}:rpm:{HH-MM}": {
    "deployment_id_1": 10,  # 该分钟已处理 10 个请求
    "deployment_id_2": 5
}
```

**TTL 配置**:
```python
class RoutingArgs:
    ttl: int = 1 * 60  # 1 分钟过期
```

**适用场景**:
- 需要遵守速率限制
- 避免触发 API 限流
- 均衡分配请求

---

### 5. 最低 TPM/RPM 策略 V2

**文件**: `lowest_tpm_rpm_v2.py`

**策略描述**: V1 的改进版本，针对多实例环境优化

**主要改进**:
1. **继承 BaseRoutingStrategy**: 使用基类的批量 Redis 操作
2. **预调用检查**: 在请求前检查 RPM 限制，及时拒绝超限请求
3. **批量缓存读取**: 使用 `async_batch_get_cache` 一次性读取所有部署的 TPM/RPM
4. **优化的缓存键**: 缓存单个部署而非模型组

**缓存结构**:
```python
# 每个部署单独的键
"{deployment_id}:{model_name}:tpm:{HH-MM}": 5000
"{deployment_id}:{model_name}:rpm:{HH-MM}": 10
```

**预调用检查**:
```python
async def async_pre_call_check(deployment):
    # 检查本地缓存
    local_result = await cache.async_get_cache(rpm_key, local_only=True)
    if local_result >= deployment_rpm:
        raise RateLimitError(...)

    # 增量 RPM 并检查 Redis
    result = await cache.async_increment(rpm_key, 1)
    if result > deployment_rpm:
        raise RateLimitError(...)
```

**错误处理**:
- 超过 RPM 限制时抛出 `RateLimitError` (HTTP 429)
- 提供详细的限流信息和调试数据

**适用场景**:
- 多实例 LiteLLM 部署
- 严格的速率限制要求
- 需要精确的流量控制

---

### 6. 简单随机策略 (Simple Shuffle)

**文件**: `simple_shuffle.py`

**策略描述**: 随机选择一个健康的部署，支持权重配置

**工作原理**:
1. **检查权重**: 按顺序检查 `weight`、`rpm`、`tpm` 参数
2. **加权随机**: 如果存在权重，按权重进行随机选择
3. **纯随机**: 如果没有权重，完全随机选择

**权重计算**:
```python
# 权重归一化
weights = [m["litellm_params"].get(weight_by, 0) for m in deployments]
total_weight = sum(weights)
normalized_weights = [w / total_weight for w in weights]

# 加权随机选择
selected_index = random.choices(range(len(weights)), weights=normalized_weights)[0]
```

**配置示例**:
```yaml
model_list:
  - model_name: gpt-4
    litellm_params:
      model: openai/gpt-4
      weight: 3  # 或 rpm: 100, 或 tpm: 10000
  - model_name: gpt-4
    litellm_params:
      model: azure/gpt-4
      weight: 1
```

**适用场景**:
- 简单的负载均衡
- 按比例分配流量
- A/B 测试

---

### 7. 基于标签的路由 (Tag-Based Routing)

**文件**: `tag_based_routing.py`

**策略描述**: 基于请求标签路由到特定的部署

**工作原理**:
1. **提取请求标签**: 从 `request_kwargs.metadata.tags` 获取标签
2. **匹配部署标签**: 查找部署配置中的标签
3. **标签匹配规则**:
   - 如果请求中的任一标签存在于部署标签中，则匹配
   - 如果没有匹配的部署，查找带有 `default` 标签的部署
   - 如果没有 default 部署，返回所有健康部署

**匹配逻辑**:
```python
def is_valid_deployment_tag(deployment_tags, request_tags):
    return any(tag in deployment_tags for tag in request_tags)
```

**配置示例**:
```yaml
model_list:
  - model_name: gpt-4
    litellm_params:
      model: openai/gpt-4
      tags: ["premium", "default"]
  - model_name: gpt-4
    litellm_params:
      model: azure/gpt-4
      tags: ["standard", "default"]
  - model_name: gpt-3.5-turbo
    litellm_params:
      model: openai/gpt-3.5-turbo
      tags: ["free"]
```

**请求示例**:
```python
response = router.completion(
    model="gpt-4",
    messages=[...],
    metadata={"tags": ["premium"]}
)
```

**适用场景**:
- 多租户系统
- 不同服务等级 (SLA)
- 团队/项目隔离
- 地理位置路由

---

### 8. 预算限制器 (Budget Limiter)

**文件**: `budget_limiter.py`

**策略描述**: 基于预算限制过滤部署，支持三层预算控制

**三层预算控制**:

1. **Provider 级别预算**
   - 限制特定 LLM 提供商的支出
   - 例如: OpenAI 每天最多 $100

2. **Deployment 级别预算**
   - 限制特定部署的支出
   - 例如: gpt-4 部署每周最多 $500

3. **Tag 级别预算** (企业功能)
   - 限制特定标签的支出
   - 例如: "team-A" 标签每月最多 $1000

**工作原理**:
1. **初始化预算窗口**: 设置预算开始时间和 TTL
2. **记录支出**: 每次成功请求后增量更新支出
3. **过滤部署**: 在路由前过滤掉超出预算的部署
4. **周期性同步**: 在多实例环境中同步内存和 Redis

**缓存结构**:
```python
# Provider 预算
"provider_spend:{provider}:{duration}": 45.67  # 当前支出
"provider_budget_start_time:{provider}": 1702900000.0  # 窗口开始时间

# Deployment 预算
"deployment_spend:{model_id}:{duration}": 123.45
"deployment_budget_start_time:{model_id}": 1702900000.0

# Tag 预算
"tag_spend:{tag}:{duration}": 78.90
"tag_budget_start_time:{tag}": 1702900000.0
```

**配置示例**:
```yaml
router_settings:
  provider_budget_config:
    openai:
      budget_limit: 100
      time_period: 1d
    anthropic:
      budget_limit: 500
      time_period: 7d

model_list:
  - model_name: gpt-4
    litellm_params:
      model: openai/gpt-4
      max_budget: 50
      budget_duration: 1d
```

**预算窗口处理**:
```python
async def _increment_spend_for_key(budget_config, spend_key, start_time_key, cost):
    current_time = datetime.now(timezone.utc).timestamp()
    budget_start = await get_or_set_budget_start_time(start_time_key)

    if budget_start is None:
        # 新预算窗口
        await handle_new_budget_window(...)
    elif (current_time - budget_start) > ttl_seconds:
        # 预算窗口过期，重置
        await handle_new_budget_window(...)
    else:
        # 在当前窗口内增量
        await increment_spend_in_current_window(...)
```

**适用场景**:
- 成本控制
- 多租户预算管理
- 防止意外超支
- 企业级财务管理

**特性**:
- Prometheus 指标集成
- 详细的预算超支错误信息
- 支持多实例环境
- 批量 Redis 操作优化

---

### 9. 自动路由 (Auto Router)

**文件**: `auto_router/auto_router.py`

**策略描述**: 基于语义理解自动路由请求到最合适的模型

**工作原理**:
1. **加载语义路由配置**: 从 JSON 文件或配置字符串加载路由规则
2. **创建语义路由层**: 使用 `semantic-router` 库创建路由层
3. **请求前钩子**: 在路由前分析用户消息内容
4. **语义匹配**: 将消息与预定义的路由规则匹配
5. **模型选择**: 根据匹配结果选择最合适的模型

**配置结构**:
```json
{
  "routes": [
    {
      "name": "gpt-4",
      "description": "Complex reasoning and analysis tasks",
      "utterances": [
        "Analyze this complex problem",
        "Explain the philosophical implications",
        "Provide a detailed analysis"
      ],
      "score_threshold": 0.7
    },
    {
      "name": "gpt-3.5-turbo",
      "description": "Simple queries and general tasks",
      "utterances": [
        "What is the weather",
        "Simple question",
        "Quick answer needed"
      ],
      "score_threshold": 0.6
    }
  ]
}
```

**路由过程**:
```python
async def async_pre_routing_hook(model, request_kwargs, messages):
    # 1. 初始化语义路由层（如果未初始化）
    if self.routelayer is None:
        self.routelayer = SemanticRouter(
            routes=self.loaded_routes,
            encoder=LiteLLMRouterEncoder(
                litellm_router_instance=self.litellm_router_instance,
                model_name=self.embedding_model,
            ),
            auto_sync="local",
        )

    # 2. 获取用户消息
    user_message = messages[-1]["content"]

    # 3. 语义匹配
    route_choice = self.routelayer(text=user_message)

    # 4. 返回选择的模型
    model = route_choice.name or self.default_model
    return PreRoutingHookResponse(model=model, messages=messages)
```

**Encoder 实现**:
```python
class LiteLLMRouterEncoder:
    def __init__(self, litellm_router_instance, model_name):
        self.litellm_router_instance = litellm_router_instance
        self.model_name = model_name

    def __call__(self, texts):
        # 使用 LiteLLM Router 调用 embedding 模型
        response = self.litellm_router_instance.embedding(
            model=self.model_name,
            input=texts
        )
        return response.data
```

**使用示例**:
```python
from litellm import Router

router = Router(
    model_list=[
        {
            "model_name": "auto-router",
            "litellm_params": {
                "model": "auto-router",
            }
        },
        {
            "model_name": "gpt-4",
            "litellm_params": {"model": "openai/gpt-4"}
        },
        {
            "model_name": "gpt-3.5-turbo",
            "litellm_params": {"model": "openai/gpt-3.5-turbo"}
        }
    ],
    routing_strategy="auto-router",
    routing_strategy_args={
        "auto_router_config_path": "router_config.json",
        "default_model": "gpt-3.5-turbo",
        "embedding_model": "text-embedding-ada-002"
    }
)

# 请求会自动路由到合适的模型
response = router.completion(
    model="auto-router",
    messages=[{"role": "user", "content": "Analyze the philosophical implications of AI"}]
)
# 此请求会被路由到 gpt-4
```

**适用场景**:
- 智能模型选择
- 根据任务复杂度自动路由
- 成本与性能的自动平衡
- 动态模型切换

**优点**:
- 无需手动指定模型
- 自动优化成本和性能
- 支持自定义语义规则
- 灵活的路由配置

**依赖**:
- `semantic-router` 库
- Embedding 模型（用于语义匹配）

---

## 使用示例

### 示例 1: 使用最低延迟策略

```python
from litellm import Router

router = Router(
    model_list=[
        {
            "model_name": "gpt-4",
            "litellm_params": {
                "model": "openai/gpt-4",
                "api_key": "sk-...",
            },
            "tpm": 100000,
            "rpm": 1000,
        },
        {
            "model_name": "gpt-4",
            "litellm_params": {
                "model": "azure/gpt-4",
                "api_key": "...",
                "api_base": "https://...",
            },
            "tpm": 100000,
            "rpm": 1000,
        }
    ],
    routing_strategy="lowest-latency",
    routing_strategy_args={
        "ttl": 3600,  # 缓存 1 小时
        "lowest_latency_buffer": 0.1,  # 10% 延迟缓冲
        "max_latency_list_size": 20,  # 保留 20 次延迟记录
    },
    redis_host="localhost",
    redis_port=6379,
)

response = router.completion(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello"}],
)
```

### 示例 2: 使用预算限制

```python
from litellm import Router

router = Router(
    model_list=[
        {
            "model_name": "gpt-4",
            "litellm_params": {
                "model": "openai/gpt-4",
                "max_budget": 50,  # 每天最多 $50
                "budget_duration": "1d",
            },
        },
        {
            "model_name": "claude-3",
            "litellm_params": {
                "model": "anthropic/claude-3-opus",
                "max_budget": 100,
                "budget_duration": "1d",
            },
        }
    ],
    provider_budget_config={
        "openai": {
            "budget_limit": 100,
            "time_period": "1d"
        },
        "anthropic": {
            "budget_limit": 200,
            "time_period": "1d"
        }
    },
    redis_host="localhost",
    redis_port=6379,
)

response = router.completion(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello"}],
)
```

### 示例 3: 使用标签路由

```python
from litellm import Router

router = Router(
    model_list=[
        {
            "model_name": "gpt-4",
            "litellm_params": {
                "model": "openai/gpt-4",
                "tags": ["premium", "default"]
            },
        },
        {
            "model_name": "gpt-3.5-turbo",
            "litellm_params": {
                "model": "openai/gpt-3.5-turbo",
                "tags": ["standard", "default"]
            },
        },
        {
            "model_name": "gpt-3.5-turbo",
            "litellm_params": {
                "model": "azure/gpt-35-turbo",
                "tags": ["free"]
            },
        }
    ],
    enable_tag_filtering=True,
)

# Premium 用户的请求
response = router.completion(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello"}],
    metadata={"tags": ["premium"]},  # 会路由到 OpenAI GPT-4
)

# Free 用户的请求
response = router.completion(
    model="gpt-3.5-turbo",
    messages=[{"role": "user", "content": "Hello"}],
    metadata={"tags": ["free"]},  # 会路由到 Azure GPT-3.5
)
```

### 示例 4: 组合多种策略

```python
from litellm import Router

router = Router(
    model_list=[...],
    routing_strategy="lowest-latency",  # 主路由策略
    routing_strategy_args={
        "ttl": 3600,
    },
    provider_budget_config={  # 添加预算限制
        "openai": {
            "budget_limit": 100,
            "time_period": "1d"
        }
    },
    enable_tag_filtering=True,  # 启用标签过滤
)

# 这个请求会：
# 1. 先基于标签过滤部署
# 2. 再过滤掉超出预算的部署
# 3. 最后从剩余部署中选择延迟最低的
response = router.completion(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello"}],
    metadata={"tags": ["premium"]},
)
```

---

## 性能优化建议

### 1. 使用 Redis 缓存
对于多实例部署，强烈建议使用 Redis：
```python
router = Router(
    model_list=[...],
    redis_host="localhost",
    redis_port=6379,
)
```

### 2. 调整缓存 TTL
根据业务需求调整缓存过期时间：
```python
routing_strategy_args={
    "ttl": 3600,  # 延迟/成本数据缓存 1 小时
}
```

### 3. 批量操作
V2 策略使用批量 Redis 操作，性能更好：
- 使用 `lowest_tpm_rpm_v2` 而非 `lowest_tpm_rpm`
- 启用 `should_batch_redis_writes=True`

### 4. 合理设置限流参数
避免过于严格的 TPM/RPM 限制：
```python
{
    "tpm": 100000,  # 根据实际 API 限制设置
    "rpm": 1000,
}
```

---

## 监控和调试

### 1. 启用详细日志
```python
import litellm
litellm.set_verbose = True
```

### 2. Prometheus 指标
预算限制器会自动记录 Prometheus 指标：
- `litellm_provider_remaining_budget`
- 当前支出
- 预算限制

### 3. 错误信息
各策略会在错误时提供详细信息：
```python
try:
    response = router.completion(...)
except litellm.RateLimitError as e:
    # 包含当前 TPM/RPM、限制值、部署信息等
    print(e.message)
except ValueError as e:
    # 包含预算超支信息、部署状态等
    print(e)
```

---

## 总结

| 策略 | 优化目标 | 适用场景 | 缓存依赖 |
|------|---------|---------|---------|
| Least Busy | 负载均衡 | 实时流量分配 | 必需 (内存) |
| Lowest Cost | 成本 | 成本敏感应用 | 必需 |
| Lowest Latency | 响应时间 | 低延迟要求 | 必需 |
| Lowest TPM/RPM | 限流 | API 速率限制 | 必需 |
| Lowest TPM/RPM V2 | 限流 (多实例) | 严格速率控制 | 必需 (Redis) |
| Simple Shuffle | 负载均衡 | 简单场景 | 不需要 |
| Tag-Based | 隔离/分层 | 多租户系统 | 不需要 |
| Budget Limiter | 成本控制 | 预算管理 | 必需 (Redis) |
| Auto Router | 智能路由 | 自动模型选择 | 需要 Embedding |

选择合适的路由策略可以显著提升系统的性能、成本效益和可靠性。建议根据实际业务需求组合使用多种策略。
