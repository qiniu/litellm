# Prompt Caching 模型特定路由配置指南

## 问题背景

LiteLLM Router 的 Prompt Caching 功能当前对**所有模型**生效,只要满足以下条件:
1. 启用 `optional_pre_call_checks=["prompt_caching"]`
2. Prompt >= 1024 tokens
3. Messages 中包含 `cache_control`

但是,**Prompt Caching 只对特定模型有实际效果**:
- ✅ **Anthropic Claude** (原生支持)
- ✅ **OpenAI** (部分模型支持)
- ❌ **Google Gemini** (不支持 prompt caching)
- ❌ **其他大部分模型** (不支持)

## 当前行为

### 现状分析

```python
# litellm/router_utils/pre_call_checks/prompt_caching_deployment_check.py

async def async_filter_deployments(self, model, healthy_deployments, messages, ...):
    # ⚠️ 问题: 只检查 token 数量,不检查模型是否支持 prompt caching
    if messages is not None and is_prompt_caching_valid_prompt(
        messages=messages,
        model=model,  # ← 这里的 model 是用户请求的 model_name (如 "my-model")
    ):
        # 对所有模型都进行缓存路由!
        model_id_dict = await prompt_cache.async_get_model_id(messages, tools)
        if model_id_dict is not None:
            return [deployment]  # 强制路由到缓存的 deployment

    return healthy_deployments
```

```python
# litellm/utils.py

def is_prompt_caching_valid_prompt(model, messages, tools, ...):
    # ⚠️ 只检查 token 数量,不检查 provider
    token_count = token_counter(messages, tools, model)
    return token_count >= MINIMUM_PROMPT_CACHE_TOKEN_COUNT  # 1024
```

### 问题示例

```python
router = Router(
    model_list=[
        # Claude deployments (支持 prompt caching)
        {
            "model_name": "my-model",
            "litellm_params": {
                "model": "anthropic/claude-3-7-sonnet-20250219",
                "api_key": "sk-ant-...",
            },
            "model_info": {"id": "claude-1"}
        },
        # Gemini deployments (不支持 prompt caching)
        {
            "model_name": "my-model",
            "litellm_params": {
                "model": "vertex_ai/gemini-1.5-pro",
                "vertex_project": "my-project",
            },
            "model_info": {"id": "gemini-1"}
        }
    ],
    optional_pre_call_checks=["prompt_caching"]
)

# 第一次请求 - 路由到 gemini-1
response1 = await router.acompletion(
    model="my-model",
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Large prompt..." * 500,
                 "cache_control": {"type": "ephemeral"}}  # Gemini 不支持!
            ]
        }
    ]
)
# ⚠️ 问题: cache_key → gemini-1 被保存

# 第二次请求 - 被强制路由到 gemini-1
response2 = await router.acompletion(
    model="my-model",
    messages=[...]  # 相同的可缓存内容
)
# ❌ 问题: 即使 gemini-1 不支持 prompt caching,仍然被强制路由
# ❌ 无法利用负载均衡
```

---

## 解决方案

### 方案 1: 使用不同的 model_name (推荐 ⭐)

**最简单、最可靠的方案**: 为不同的模型使用不同的 `model_name`

```python
router = Router(
    model_list=[
        # Claude deployments - 使用 "claude-model"
        {
            "model_name": "claude-model",  # ← 独立的 model_name
            "litellm_params": {
                "model": "anthropic/claude-3-7-sonnet-20250219",
                "api_key": "sk-ant-key-1",
            },
            "model_info": {"id": "claude-1"}
        },
        {
            "model_name": "claude-model",
            "litellm_params": {
                "model": "anthropic/claude-3-7-sonnet-20250219",
                "api_key": "sk-ant-key-2",
            },
            "model_info": {"id": "claude-2"}
        },

        # Gemini deployments - 使用 "gemini-model"
        {
            "model_name": "gemini-model",  # ← 不同的 model_name
            "litellm_params": {
                "model": "vertex_ai/gemini-1.5-pro",
                "vertex_project": "project-1",
            },
            "model_info": {"id": "gemini-1"}
        },
        {
            "model_name": "gemini-model",
            "litellm_params": {
                "model": "vertex_ai/gemini-1.5-pro",
                "vertex_project": "project-2",
            },
            "model_info": {"id": "gemini-2"}
        }
    ],
    optional_pre_call_checks=["prompt_caching"]
)

# 使用 Claude (会应用 prompt caching 路由)
claude_response = await router.acompletion(
    model="claude-model",  # ← Claude
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Large prompt..." * 500,
                 "cache_control": {"type": "ephemeral"}}
            ]
        }
    ]
)
# ✅ 后续请求会强制路由到相同的 Claude deployment

# 使用 Gemini (不会应用 prompt caching 路由)
gemini_response = await router.acompletion(
    model="gemini-model",  # ← Gemini
    messages=[
        {
            "role": "user",
            "content": "Same large prompt..." * 500  # 不添加 cache_control
        }
    ]
)
# ✅ 正常负载均衡,不受 prompt caching 影响
```

**优点**:
- ✅ 简单直接,不需要修改代码
- ✅ 清晰分离不同模型
- ✅ 易于配置和维护

**缺点**:
- ⚠️ 需要客户端知道使用哪个 model_name
- ⚠️ 无法统一使用一个 model_name

---

### 方案 2: 按 Provider 过滤 (需要代码修改)

修改 `PromptCachingDeploymentCheck` 来检查实际的 provider。

#### 2.1 修改 `is_prompt_caching_valid_prompt`

```python
# litellm/utils.py

# 支持 prompt caching 的 providers
PROMPT_CACHING_SUPPORTED_PROVIDERS = {
    "anthropic",
    "bedrock",  # Bedrock Anthropic models
    "vertex_ai",  # Vertex AI Anthropic models (claude)
    "openai",  # OpenAI (部分模型)
}

# 支持 prompt caching 的模型前缀
PROMPT_CACHING_SUPPORTED_MODEL_PREFIXES = [
    "claude",
    "anthropic.claude",  # Bedrock
    "gpt-4o",  # OpenAI
    "gpt-4o-mini",
]

def is_prompt_caching_valid_prompt(
    model: str,
    messages: Optional[List[AllMessageValues]],
    tools: Optional[List[ChatCompletionToolParam]] = None,
    custom_llm_provider: Optional[str] = None,
) -> bool:
    """
    Returns true if the prompt is valid for prompt caching.

    Checks:
    1. Token count >= 1024
    2. Provider supports prompt caching
    3. Model supports prompt caching
    """
    try:
        if messages is None and tools is None:
            return False

        # 检查 token 数量
        if custom_llm_provider is not None and not model.startswith(
            custom_llm_provider
        ):
            model = custom_llm_provider + "/" + model

        token_count = token_counter(
            messages=messages,
            tools=tools,
            model=model,
            use_default_image_token_count=True,
        )

        if token_count < MINIMUM_PROMPT_CACHE_TOKEN_COUNT:
            return False

        # ← 新增: 检查 provider 是否支持 prompt caching
        from litellm import get_llm_provider

        _, provider, _, _ = get_llm_provider(
            model=model,
            custom_llm_provider=custom_llm_provider
        )

        # 检查 provider
        if provider not in PROMPT_CACHING_SUPPORTED_PROVIDERS:
            verbose_logger.debug(
                f"Provider {provider} does not support prompt caching"
            )
            return False

        # 检查模型名称 (特殊情况)
        model_lower = model.lower()

        # Vertex AI: 只有 Claude 模型支持,Gemini 不支持
        if provider == "vertex_ai":
            if not any(prefix in model_lower for prefix in ["claude", "anthropic"]):
                verbose_logger.debug(
                    f"Vertex AI model {model} does not support prompt caching "
                    "(only Claude models supported)"
                )
                return False

        # Bedrock: 只有 Anthropic Claude 模型支持
        if provider == "bedrock":
            if not any(prefix in model_lower for prefix in ["claude", "anthropic"]):
                verbose_logger.debug(
                    f"Bedrock model {model} does not support prompt caching "
                    "(only Anthropic Claude models supported)"
                )
                return False

        return True

    except Exception as e:
        verbose_logger.error(f"Error in is_prompt_caching_valid_prompt: {e}")
        return False
```

#### 2.2 修改 `async_filter_deployments`

```python
# litellm/router_utils/pre_call_checks/prompt_caching_deployment_check.py

async def async_filter_deployments(
    self,
    model: str,
    healthy_deployments: List,
    messages: Optional[List[AllMessageValues]],
    request_kwargs: Optional[dict] = None,
    parent_otel_span: Optional[Span] = None,
) -> List[dict]:
    if messages is None:
        return healthy_deployments

    # 过滤出支持 prompt caching 的 deployments
    prompt_caching_deployments = []
    for deployment in healthy_deployments:
        deployment_model = deployment["litellm_params"]["model"]
        custom_llm_provider = deployment["litellm_params"].get("custom_llm_provider")

        # 检查这个 deployment 是否支持 prompt caching
        if is_prompt_caching_valid_prompt(
            model=deployment_model,
            messages=messages,
            custom_llm_provider=custom_llm_provider
        ):
            prompt_caching_deployments.append(deployment)

    # 如果没有支持的 deployments,返回所有
    if not prompt_caching_deployments:
        verbose_logger.debug(
            f"No deployments support prompt caching for model {model}"
        )
        return healthy_deployments

    # 在支持的 deployments 中查找缓存
    prompt_cache = PromptCachingCache(cache=self.cache)
    model_id_dict = await prompt_cache.async_get_model_id(
        messages=cast(List[AllMessageValues], messages),
        tools=None,
    )

    if model_id_dict is not None:
        model_id = model_id_dict["model_id"]
        # 只在支持 prompt caching 的 deployments 中查找
        for deployment in prompt_caching_deployments:
            if deployment["model_info"]["id"] == model_id:
                verbose_logger.debug(
                    f"Found cached deployment {model_id} for prompt caching"
                )
                return [deployment]

    # 如果没有缓存命中,只返回支持 prompt caching 的 deployments
    return prompt_caching_deployments
```

**优点**:
- ✅ 自动识别支持 prompt caching 的模型
- ✅ 可以混用不同的模型在同一个 model_name
- ✅ 更智能的路由逻辑

**缺点**:
- ⚠️ 需要修改 LiteLLM 核心代码
- ⚠️ 需要维护 provider 和模型的支持列表
- ⚠️ 更复杂的逻辑

---

### 方案 3: 使用 Model Aliases (折中方案)

使用 Router 的 `model_group_alias` 功能:

```python
router = Router(
    model_list=[
        # Claude deployments
        {
            "model_name": "claude-internal",
            "litellm_params": {
                "model": "anthropic/claude-3-7-sonnet-20250219",
                "api_key": "sk-ant-...",
            },
            "model_info": {"id": "claude-1"}
        },
        # Gemini deployments
        {
            "model_name": "gemini-internal",
            "litellm_params": {
                "model": "vertex_ai/gemini-1.5-pro",
                "vertex_project": "my-project",
            },
            "model_info": {"id": "gemini-1"}
        }
    ],

    # 创建统一的 alias
    model_group_alias={
        "my-model": [
            "claude-internal",
            "gemini-internal"
        ]
    },

    optional_pre_call_checks=["prompt_caching"]
)

# 客户端仍然使用统一的 "my-model"
response = await router.acompletion(
    model="my-model",
    messages=[...]
)
```

**问题**: Alias 会展开为所有底层 model,仍然会混合 Claude 和 Gemini

---

### 方案 4: 自定义 Pre-call Check (高级)

创建一个自定义的 pre-call check 来替代默认的 prompt caching:

```python
# custom_prompt_caching_check.py

from litellm.integrations.custom_logger import CustomLogger
from litellm.router_utils.prompt_caching_cache import PromptCachingCache

class ModelSpecificPromptCachingCheck(CustomLogger):
    def __init__(self, cache, supported_providers=None):
        self.cache = cache
        self.supported_providers = supported_providers or {"anthropic", "bedrock"}

    async def async_filter_deployments(
        self,
        model: str,
        healthy_deployments: List,
        messages: Optional[List[AllMessageValues]],
        request_kwargs: Optional[dict] = None,
        parent_otel_span: Optional[Span] = None,
    ) -> List[dict]:
        # 过滤出支持 prompt caching 的 deployments
        supported_deployments = []

        for deployment in healthy_deployments:
            deployment_model = deployment["litellm_params"]["model"]

            # 提取 provider
            from litellm import get_llm_provider
            _, provider, _, _ = get_llm_provider(model=deployment_model)

            # 只处理支持的 providers
            if provider in self.supported_providers:
                # 额外检查: Vertex AI 只支持 Claude
                if provider == "vertex_ai":
                    if "claude" not in deployment_model.lower():
                        continue

                supported_deployments.append(deployment)

        # 如果没有支持的 deployments,返回所有
        if not supported_deployments:
            return healthy_deployments

        # 在支持的 deployments 中应用 prompt caching 逻辑
        if messages is not None:
            from litellm.utils import is_prompt_caching_valid_prompt

            # 使用第一个支持的 deployment 的 model 来检查
            if is_prompt_caching_valid_prompt(
                model=supported_deployments[0]["litellm_params"]["model"],
                messages=messages
            ):
                prompt_cache = PromptCachingCache(cache=self.cache)
                model_id_dict = await prompt_cache.async_get_model_id(messages, None)

                if model_id_dict is not None:
                    model_id = model_id_dict["model_id"]
                    for deployment in supported_deployments:
                        if deployment["model_info"]["id"] == model_id:
                            return [deployment]

        return supported_deployments

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        # 保存 model_id (只针对支持的 providers)
        standard_logging_object = kwargs.get("standard_logging_object")
        if standard_logging_object is None:
            return

        model = standard_logging_object["model"]
        messages = standard_logging_object["messages"]
        model_id = standard_logging_object["model_id"]

        # 检查 provider
        from litellm import get_llm_provider
        _, provider, _, _ = get_llm_provider(model=model)

        if provider not in self.supported_providers:
            return  # 不保存

        # Vertex AI: 只处理 Claude
        if provider == "vertex_ai" and "claude" not in model.lower():
            return

        # 保存 cache
        from litellm.utils import is_prompt_caching_valid_prompt
        if is_prompt_caching_valid_prompt(model=model, messages=messages):
            cache = PromptCachingCache(cache=self.cache)
            await cache.async_add_model_id(
                model_id=model_id,
                messages=messages,
                tools=None
            )

# 使用自定义 check
from litellm import Router

router = Router(
    model_list=[...],
    # 不使用内置的 prompt_caching
)

# 手动添加自定义 check
custom_check = ModelSpecificPromptCachingCheck(
    cache=router.cache,
    supported_providers={"anthropic", "bedrock"}  # 只对 Anthropic 生效
)

import litellm
litellm.callbacks.append(custom_check)
```

---

## 推荐方案对比

| 方案 | 难度 | 灵活性 | 维护成本 | 推荐度 |
|------|------|--------|----------|--------|
| **方案 1: 不同 model_name** | ⭐ 简单 | ⭐⭐ 中等 | ⭐ 低 | ⭐⭐⭐⭐⭐ **强烈推荐** |
| 方案 2: 修改核心代码 | ⭐⭐⭐ 复杂 | ⭐⭐⭐⭐⭐ 高 | ⭐⭐⭐ 高 | ⭐⭐⭐ 可选 |
| 方案 3: Model Aliases | ⭐⭐ 中等 | ⭐⭐ 中等 | ⭐⭐ 中等 | ⭐⭐ 不推荐 |
| 方案 4: 自定义 Check | ⭐⭐⭐⭐ 很复杂 | ⭐⭐⭐⭐⭐ 最高 | ⭐⭐⭐⭐ 很高 | ⭐⭐ 高级用户 |

---

## 最佳实践配置示例

### 生产环境推荐配置

```yaml
# config.yaml

model_list:
  # ========== Claude Models (支持 Prompt Caching) ==========
  - model_name: claude-sonnet  # ← 独立的 model_name
    litellm_params:
      model: anthropic/claude-3-7-sonnet-20250219
      api_key: os.environ/ANTHROPIC_API_KEY_1
    model_info:
      id: anthropic-direct-1
      base_model: anthropic/claude-3-7-sonnet-20250219

  - model_name: claude-sonnet
    litellm_params:
      model: anthropic/claude-3-7-sonnet-20250219
      api_key: os.environ/ANTHROPIC_API_KEY_2
    model_info:
      id: anthropic-direct-2

  - model_name: claude-sonnet
    litellm_params:
      model: bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0
      aws_access_key_id: os.environ/AWS_ACCESS_KEY_A
      aws_secret_access_key: os.environ/AWS_SECRET_ACCESS_KEY_A
      aws_region_name: us-east-1
    model_info:
      id: bedrock-us-east-1-a

  - model_name: claude-sonnet
    litellm_params:
      model: bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0
      aws_access_key_id: os.environ/AWS_ACCESS_KEY_B
      aws_secret_access_key: os.environ/AWS_SECRET_ACCESS_KEY_B
      aws_region_name: us-west-2
    model_info:
      id: bedrock-us-west-2-b

  # ========== Gemini Models (不支持 Prompt Caching) ==========
  - model_name: gemini-pro  # ← 不同的 model_name
    litellm_params:
      model: vertex_ai/gemini-1.5-pro
      vertex_project: os.environ/VERTEX_PROJECT_1
      vertex_location: us-central1
    model_info:
      id: gemini-us-central1-1

  - model_name: gemini-pro
    litellm_params:
      model: vertex_ai/gemini-1.5-pro
      vertex_project: os.environ/VERTEX_PROJECT_2
      vertex_location: us-west1
    model_info:
      id: gemini-us-west1-2

  # ========== GPT Models (OpenAI Prompt Caching) ==========
  - model_name: gpt-4o
    litellm_params:
      model: gpt-4o
      api_key: os.environ/OPENAI_API_KEY_1
    model_info:
      id: openai-gpt4o-1

  - model_name: gpt-4o
    litellm_params:
      model: gpt-4o
      api_key: os.environ/OPENAI_API_KEY_2
    model_info:
      id: openai-gpt4o-2

router_settings:
  routing_strategy: simple-shuffle

environment_variables:
  REDIS_HOST: localhost
  REDIS_PORT: "6379"
```

```python
# 加载配置
router = Router(
    config_file_path="config.yaml",
    optional_pre_call_checks=["prompt_caching"]
)

# 使用 Claude (会应用 prompt caching)
claude_response = await router.acompletion(
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
        {"role": "user", "content": "Question?"}
    ]
)

# 使用 Gemini (正常负载均衡,不受 prompt caching 影响)
gemini_response = await router.acompletion(
    model="gemini-pro",
    messages=[
        {"role": "user", "content": "Large context..." * 500}
        # 不添加 cache_control
    ]
)

# 使用 GPT-4o (会应用 prompt caching)
gpt_response = await router.acompletion(
    model="gpt-4o",
    messages=[...]
)
```

---

## 验证配置

```python
# 验证脚本
async def verify_prompt_caching_config():
    """验证 prompt caching 配置是否正确"""

    # 准备测试 messages
    large_prompt_messages = [
        {
            "role": "system",
            "content": [
                {"type": "text", "text": "Test prompt..." * 500,
                 "cache_control": {"type": "ephemeral"}}
            ]
        },
        {"role": "user", "content": "Question 1?"}
    ]

    print("=== Testing Claude (should use prompt caching) ===")

    # 第一次请求
    response1 = await router.acompletion(
        model="claude-sonnet",
        messages=large_prompt_messages
    )
    deployment1 = response1._hidden_params.get("model_id")
    print(f"1st request → Deployment: {deployment1}")

    await asyncio.sleep(2)

    # 第二次请求
    response2 = await router.acompletion(
        model="claude-sonnet",
        messages=large_prompt_messages
    )
    deployment2 = response2._hidden_params.get("model_id")
    print(f"2nd request → Deployment: {deployment2}")

    if deployment1 == deployment2:
        print("✅ Claude: Prompt caching working (same deployment)")
    else:
        print("❌ Claude: Prompt caching NOT working (different deployments)")

    print("\n=== Testing Gemini (should NOT use prompt caching) ===")

    # Gemini 第一次请求
    gemini1 = await router.acompletion(
        model="gemini-pro",
        messages=[{"role": "user", "content": "Test..." * 500}]
    )
    gemini_deployment1 = gemini1._hidden_params.get("model_id")
    print(f"1st request → Deployment: {gemini_deployment1}")

    # Gemini 第二次请求
    gemini2 = await router.acompletion(
        model="gemini-pro",
        messages=[{"role": "user", "content": "Test..." * 500}]
    )
    gemini_deployment2 = gemini2._hidden_params.get("model_id")
    print(f"2nd request → Deployment: {gemini_deployment2}")

    # Gemini 应该随机选择 deployment
    print("✅ Gemini: Normal load balancing (as expected)")

# 运行验证
await verify_prompt_caching_config()
```

---

## 总结

### 核心要点

1. **当前行为**: Prompt Caching 路由对所有模型生效(只要 token >= 1024)
2. **问题**: Gemini 不支持 prompt caching,但仍然被强制路由
3. **推荐方案**: 使用不同的 `model_name` 区分支持和不支持的模型
4. **高级方案**: 修改代码添加 provider 检查

### 配置清单

- ✅ 为 Claude 使用独立的 `model_name` (如 `claude-sonnet`)
- ✅ 为 Gemini 使用独立的 `model_name` (如 `gemini-pro`)
- ✅ 只在 Claude 的请求中添加 `cache_control`
- ✅ 配置 Redis 以支持跨进程缓存
- ✅ 手动指定稳定的 `model_info.id`

---

**维护者**: LiteLLM Team
**最后更新**: 2025-12-22
