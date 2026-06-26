# Anthropic Cache Control 实现文档

## 概述

本文档详细说明 LiteLLM 中 Anthropic Claude 模型使用 `cache_control: {"type": "ephemeral"}` 的完整处理逻辑。

**Prompt Caching** 是 Anthropic 提供的功能,允许缓存部分 prompt 内容以:
- 减少重复 token 计算
- 降低 API 调用成本(缓存读取约 90% 折扣)
- 提高响应速度

**支持的缓存类型**:
```json
{"type": "ephemeral"}                    // 默认 5 分钟缓存
{"type": "ephemeral", "ttl": "5m"}      // 5 分钟缓存
{"type": "ephemeral", "ttl": "1h"}      // 1 小时缓存
```

---

## 核心处理流程

### 1. 请求接收阶段

**文件**: `litellm/proxy/litellm_pre_call_utils.py:1341-1362`

当请求进入 LiteLLM Proxy 时,首先检查并转发 Anthropic 相关的 headers:

```python
def add_provider_specific_headers_to_request(data: dict, headers: dict):
    """
    提取并保存 Anthropic API 相关的 headers
    """
    anthropic_headers = {}

    # 检查请求头中的 Anthropic headers
    # ANTHROPIC_API_HEADERS = ["anthropic-version", "anthropic-beta"]
    for header in ANTHROPIC_API_HEADERS:
        if header in headers:
            anthropic_headers[header] = headers[header]

    if anthropic_headers:
        # 存储到 provider_specific_header,后续转发给 Anthropic API
        data["provider_specific_header"] = ProviderSpecificHeader(
            custom_llm_provider="anthropic,bedrock,vertex_ai",
            extra_headers=anthropic_headers
        )
```

**关键点**:
- 支持 `anthropic-beta: prompt-caching-2024-07-31` header
- 该 header 会被转发到底层 Anthropic API
- 支持跨提供商: Anthropic、Bedrock、Vertex AI

---

### 2. 消息转换阶段

**文件**: `litellm/llms/anthropic/chat/transformation.py`

#### 2.1 System Message 处理

**方法**: `AnthropicConfig.translate_system_message()` (第 774-825 行)

```python
def translate_system_message(
    self,
    messages: List[AllMessageValues]
) -> List[AnthropicSystemMessageContent]:
    """
    将 OpenAI 格式的 system message 转换为 Anthropic 格式
    并保留 cache_control 信息
    """
    anthropic_system_message_list = []

    for message in messages:
        if message["role"] == "system":
            # 格式 1: content 是字符串
            if isinstance(message["content"], str):
                anthropic_system_message_content = AnthropicSystemMessageContent(
                    type="text",
                    text=message["content"],
                )
                # 提取消息级别的 cache_control
                if "cache_control" in message:
                    anthropic_system_message_content["cache_control"] = \
                        message["cache_control"]

                anthropic_system_message_list.append(anthropic_system_message_content)

            # 格式 2: content 是 list (多个 content block)
            elif isinstance(message["content"], list):
                for _content in message["content"]:
                    anthropic_system_message_content = AnthropicSystemMessageContent(
                        type=_content.get("type"),
                        text=_content.get("text"),
                    )
                    # 每个 content block 都可以有自己的 cache_control
                    if "cache_control" in _content:
                        anthropic_system_message_content["cache_control"] = \
                            _content["cache_control"]

                    anthropic_system_message_list.append(anthropic_system_message_content)

    # 从原始消息列表中移除 system messages
    # 它们会被放到 Anthropic API 的 "system" 字段中
    return anthropic_system_message_list
```

**转换示例**:

```python
# OpenAI 格式 (输入)
{
    "role": "system",
    "content": [
        {
            "type": "text",
            "text": "You are a helpful assistant."
        },
        {
            "type": "text",
            "text": "Here is a large context document..." * 500,
            "cache_control": {"type": "ephemeral"}  # ← 缓存这部分
        }
    ]
}

# Anthropic 格式 (输出)
# 放在请求的 "system" 字段中
[
    {
        "type": "text",
        "text": "You are a helpful assistant."
    },
    {
        "type": "text",
        "text": "Here is a large context document...",
        "cache_control": {"type": "ephemeral"}
    }
]
```

#### 2.2 User/Assistant Message 处理

**方法**: `anthropic_messages_pt()` (在 `litellm/litellm_core_utils/prompt_templates/factory.py`)

User 和 Assistant 消息通过专门的 prompt template 函数处理:

```python
# OpenAI 格式 (输入)
{
    "role": "user",
    "content": "What are the key terms?",
    "cache_control": {"type": "ephemeral"}
}

# Anthropic 格式 (输出)
{
    "role": "user",
    "content": [
        {
            "type": "text",
            "text": "What are the key terms?",
            "cache_control": {"type": "ephemeral"}  # ← 移到 content block 内
        }
    ]
}
```

**关键规则**:
1. **只有最后一个 content block 可以有 `cache_control`** (Anthropic API 限制)
2. 如果 `content` 是字符串,会自动转换为 list 格式
3. `cache_control` 始终附加到 content block 级别

---

#### 2.3 Tools 处理

**方法**: `AnthropicConfig._map_tool_helper()` (第 197-372 行)

```python
def _map_tool_helper(
    self,
    tool: ChatCompletionToolParam
) -> Tuple[Optional[AllAnthropicToolsValues], Optional[AnthropicMcpServerTool]]:
    """
    将 OpenAI 格式的 tool 转换为 Anthropic 格式
    """
    # 创建 Anthropic tool
    anthropic_tool = AnthropicMessagesTool(
        name=tool["function"]["name"],
        description=tool["function"].get("description"),
        input_schema=...,  # 转换 parameters 为 input_schema
    )

    # 检查 tool 级别的 cache_control (两个位置)
    _cache_control = tool.get("cache_control", None)
    _cache_control_function = tool.get("function", {}).get("cache_control", None)

    # 优先使用 tool 级别,其次是 function 级别
    if _cache_control is not None:
        anthropic_tool["cache_control"] = _cache_control
    elif _cache_control_function is not None:
        anthropic_tool["cache_control"] = _cache_control_function

    return anthropic_tool, mcp_server
```

**Tool 缓存示例**:

```python
# 方式 1: 在 function 内定义 cache_control
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"}
                }
            },
            "cache_control": {"type": "ephemeral"}  # ← 推荐位置
        }
    }
]

# 方式 2: 在 tool 顶层定义 cache_control
tools = [
    {
        "type": "function",
        "cache_control": {"type": "ephemeral"},  # ← 也支持
        "function": {
            "name": "get_weather",
            ...
        }
    }
]
```

**特殊工具类型**:
- `tool_search_tool_regex_20251119`: 不支持 cache_control
- `tool_search_tool_bm25_20251119`: 不支持 cache_control
- Computer tools (`computer_20241022`, `computer_20250124`): 不支持 cache_control

---

### 3. Headers 自动注入

**文件**: `litellm/llms/anthropic/chat/transformation.py:159-163`

```python
def get_cache_control_headers(self) -> dict:
    """
    返回使用 prompt caching 需要的 headers
    """
    return {
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "prompt-caching-2024-07-31",
    }
```

**自动注入逻辑**:

1. LiteLLM 检测请求中是否包含 `cache_control`
2. 如果用户没有在 `extra_headers` 中提供必要的 headers,自动添加
3. 用户显式传递的 headers 优先级更高

```python
# 用户不需要手动添加 headers (LiteLLM 会自动处理)
response = await litellm.acompletion(
    model="anthropic/claude-3-7-sonnet-20250219",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Hello",
                    "cache_control": {"type": "ephemeral"}
                }
            ]
        }
    ]
    # extra_headers 自动添加:
    # {
    #     "anthropic-version": "2023-06-01",
    #     "anthropic-beta": "prompt-caching-2024-07-31"
    # }
)
```

---

### 4. AnthropicCacheControlHook - 动态注入

**文件**: `litellm/integrations/anthropic_cache_control_hook.py`

这是一个高级功能,允许通过配置动态注入 `cache_control`,而不需要修改原始消息。

#### 4.1 使用方式

```python
response = await litellm.acompletion(
    model="anthropic/claude-3-7-sonnet-20250219",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is AI?"}
    ],
    cache_control_injection_points=[
        {
            "location": "message",
            "index": -1,  # 最后一条消息
            "control": {"type": "ephemeral"}
        }
    ]
)
```

#### 4.2 注入点配置

```python
# 按索引注入
{
    "location": "message",
    "index": 0,  # 第一条消息 (支持负数索引)
    "control": {"type": "ephemeral"}
}

# 按角色注入 (应用到所有匹配的消息)
{
    "location": "message",
    "role": "system",  # 所有 system 消息
    "control": {"type": "ephemeral", "ttl": "1h"}
}
```

#### 4.3 实现逻辑

```python
class AnthropicCacheControlHook(CustomPromptManagement):
    def get_chat_completion_prompt(self, model, messages, non_default_params, ...):
        # 从请求参数中提取注入点配置
        injection_points = non_default_params.pop(
            "cache_control_injection_points", []
        )

        if not injection_points:
            return model, messages, non_default_params

        # 深拷贝消息,避免修改原始数据
        processed_messages = copy.deepcopy(messages)

        for point in injection_points:
            if point.get("location") == "message":
                processed_messages = self._process_message_injection(
                    point=point,
                    messages=processed_messages
                )

        return model, processed_messages, non_default_params

    @staticmethod
    def _safe_insert_cache_control_in_message(message, control):
        """
        安全地将 cache_control 插入消息中
        """
        message_content = message.get("content", None)

        # 情况 1: content 是字符串
        if isinstance(message_content, str):
            message["cache_control"] = control

        # 情况 2: content 是 list - 只在最后一个 block 添加
        elif isinstance(message_content, list):
            if len(message_content) > 0 and isinstance(message_content[-1], dict):
                message_content[-1]["cache_control"] = control

        return message
```

**使用场景**:
- 在 proxy 层面统一管理缓存策略
- 不需要修改客户端代码
- 适用于团队级或 API key 级的缓存配置

---

### 5. Response 处理

**文件**: `litellm/llms/anthropic/chat/transformation.py:1117-1212`

#### 5.1 Usage 计算

```python
def calculate_usage(
    self,
    usage_object: dict,
    reasoning_content: Optional[str],
    completion_response: Optional[dict] = None
) -> Usage:
    """
    处理 Anthropic 返回的 usage 信息,包含缓存相关的 token 统计
    """
    # 基础 tokens
    prompt_tokens = usage_object.get("input_tokens", 0) or 0
    completion_tokens = usage_object.get("output_tokens", 0) or 0

    # Prompt Caching 相关 tokens
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0
    cache_creation_token_details = None

    # 缓存创建 tokens (写入缓存)
    if "cache_creation_input_tokens" in usage_object:
        cache_creation_input_tokens = usage_object["cache_creation_input_tokens"]
        prompt_tokens += cache_creation_input_tokens

    # 缓存读取 tokens (从缓存读取)
    if "cache_read_input_tokens" in usage_object:
        cache_read_input_tokens = usage_object["cache_read_input_tokens"]
        prompt_tokens += cache_read_input_tokens

    # 详细的缓存创建信息 (按 TTL 分类)
    if "cache_creation" in usage_object:
        cache_creation_token_details = CacheCreationTokenDetails(
            ephemeral_5m_input_tokens=usage_object["cache_creation"].get(
                "ephemeral_5m_input_tokens"
            ),
            ephemeral_1h_input_tokens=usage_object["cache_creation"].get(
                "ephemeral_1h_input_tokens"
            ),
        )

    # 构建 prompt tokens 详情
    prompt_tokens_details = PromptTokensDetailsWrapper(
        cached_tokens=cache_read_input_tokens,
        cache_creation_tokens=cache_creation_input_tokens,
        cache_creation_token_details=cache_creation_token_details,
    )

    # 返回完整的 usage 信息
    return Usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        prompt_tokens_details=prompt_tokens_details,
        cache_creation_input_tokens=cache_creation_input_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
    )
```

#### 5.2 Response 字段说明

```python
response = await litellm.acompletion(...)

# 访问 usage 信息
print(response.usage.prompt_tokens)                    # 总 prompt tokens
print(response.usage.completion_tokens)                # 输出 tokens
print(response.usage.cache_creation_input_tokens)      # 写入缓存的 tokens
print(response.usage.cache_read_input_tokens)          # 从缓存读取的 tokens

# 详细信息
print(response.usage.prompt_tokens_details.cached_tokens)          # 缓存命中的 tokens
print(response.usage.prompt_tokens_details.cache_creation_tokens)  # 缓存创建的 tokens
```

**Anthropic 返回的 usage 示例**:

```json
{
  "usage": {
    "input_tokens": 2095,
    "cache_creation_input_tokens": 2051,  // 首次请求: 创建缓存
    "cache_read_input_tokens": 0,
    "output_tokens": 283
  }
}

// 第二次请求 (相同 prompt)
{
  "usage": {
    "input_tokens": 2095,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 2051,      // 命中缓存!
    "output_tokens": 296
  }
}
```

---

## 完整使用示例

### 示例 1: System Message Caching

```python
import litellm

response = await litellm.acompletion(
    model="anthropic/claude-3-7-sonnet-20250219",
    messages=[
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "You are an AI assistant tasked with analyzing legal documents.",
                },
                {
                    "type": "text",
                    "text": (
                        "Here is the full text of a complex legal agreement:\n\n"
                        + "Article 1: This agreement covers..." * 500  # 大量文本
                    ),
                    "cache_control": {"type": "ephemeral"}  # ← 缓存长文本
                }
            ],
        },
        {
            "role": "user",
            "content": "What are the key terms and conditions?"
        }
    ],
    # 可选: 显式传递 headers (LiteLLM 也会自动添加)
    extra_headers={
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "prompt-caching-2024-07-31",
    }
)

# 检查缓存使用情况
print(f"Prompt tokens: {response.usage.prompt_tokens}")
print(f"Cache creation tokens: {response.usage.cache_creation_input_tokens}")
print(f"Cache read tokens: {response.usage.cache_read_input_tokens}")

# 首次调用输出:
# Prompt tokens: 5000
# Cache creation tokens: 4800
# Cache read tokens: 0

# 第二次调用 (5分钟内):
# Prompt tokens: 5000
# Cache creation tokens: 0
# Cache read tokens: 4800  ← 成本大幅降低!
```

---

### 示例 2: Multi-turn Conversation Caching

```python
# 多轮对话场景,逐步缓存上下文
messages = [
    # 缓存 system prompt
    {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": "You are a helpful assistant." * 100,
                "cache_control": {"type": "ephemeral", "ttl": "1h"}  # 1小时缓存
            }
        ]
    },
    # 第一轮对话
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "What is machine learning?",
                "cache_control": {"type": "ephemeral"}  # 缓存用户问题
            }
        ]
    },
    {
        "role": "assistant",
        "content": "Machine learning is a subset of AI..."
    },
    # 第二轮对话 (继续缓存)
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "Can you give me some examples?",
                "cache_control": {"type": "ephemeral"}  # 缓存后续对话
            }
        ]
    }
]

response = await litellm.acompletion(
    model="anthropic/claude-3-7-sonnet-20250219",
    messages=messages
)
```

**缓存层级策略**:
1. System prompt: 1小时缓存 (最不常变化)
2. 历史对话: 5分钟缓存 (可能继续对话)
3. 最新消息: 5分钟缓存 (为后续 turn 准备)

---

### 示例 3: Tools with Caching

```python
response = await litellm.acompletion(
    model="anthropic/claude-3-7-sonnet-20250219",
    messages=[
        {
            "role": "user",
            "content": "What's the weather like in Boston today?"
        }
    ],
    tools=[
        {
            "type": "function",
            "function": {
                "name": "get_current_weather",
                "description": "Get the current weather in a given location",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "The city and state, e.g. San Francisco, CA",
                        },
                        "unit": {
                            "type": "string",
                            "enum": ["celsius", "fahrenheit"],
                        },
                    },
                    "required": ["location"],
                },
                "cache_control": {"type": "ephemeral"}  # ← 缓存 tool 定义
            },
        }
    ],
)

# 如果有多个工具,只需在最后一个工具上添加 cache_control
# (根据 Anthropic API 规范)
```

**适用场景**:
- 复杂的工具定义 (长 schema)
- 多个工具需要重复使用
- Tool description 包含大量示例

---

### 示例 4: 使用 Cache Control Hook

```python
# 在 proxy 层面统一配置缓存策略
response = await litellm.acompletion(
    model="anthropic/claude-3-7-sonnet-20250219",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is AI?"}
    ],
    # 动态注入 cache_control
    cache_control_injection_points=[
        # 缓存所有 system 消息
        {
            "location": "message",
            "role": "system",
            "control": {"type": "ephemeral", "ttl": "1h"}
        },
        # 缓存最后一条消息
        {
            "location": "message",
            "index": -1,
            "control": {"type": "ephemeral"}
        }
    ]
)
```

**优势**:
- 客户端代码无需修改
- 集中管理缓存策略
- 适合团队级配置

---

### 示例 5: Streaming with Cache Control

```python
response = await litellm.acompletion(
    model="anthropic/claude-3-7-sonnet-20250219",
    messages=[
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "Large context..." * 400,
                    "cache_control": {"type": "ephemeral"}
                }
            ]
        },
        {
            "role": "user",
            "content": "Summarize the key points"
        }
    ],
    stream=True,
    stream_options={"include_usage": True}  # ← 重要: 获取 usage 信息
)

async for chunk in response:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")

    # 最后一个 chunk 包含 usage 信息
    if hasattr(chunk, "usage") and chunk.usage:
        print(f"\n\nUsage:")
        print(f"  Cache read: {chunk.usage.cache_read_input_tokens}")
        print(f"  Cache creation: {chunk.usage.cache_creation_input_tokens}")
        print(f"  Total prompt: {chunk.usage.prompt_tokens}")
```

**注意事项**:
- 必须设置 `stream_options={"include_usage": True}`
- Usage 信息在最后一个 chunk 中返回
- 与非 streaming 模式的 usage 格式相同

---

## 关键实现细节

### 1. 最小 Token 要求

**文件**: `litellm/utils.py`

```python
MINIMUM_PROMPT_CACHE_TOKEN_COUNT = 1024

def is_prompt_caching_valid_prompt(
    model: str,
    messages: Optional[List[AllMessageValues]],
    tools: Optional[List[ChatCompletionToolParam]] = None,
    custom_llm_provider: Optional[str] = None,
) -> bool:
    """
    检查 prompt 是否满足缓存的最小 token 要求
    """
    try:
        if messages is None and tools is None:
            return False

        # 计算总 token 数
        token_count = token_counter(
            messages=messages,
            tools=tools,
            model=model,
            use_default_image_token_count=True,
        )

        # Anthropic 要求至少 1024 tokens 才会启用缓存
        return token_count >= MINIMUM_PROMPT_CACHE_TOKEN_COUNT
    except Exception as e:
        verbose_logger.error(f"Error in is_prompt_caching_valid_prompt: {e}")
        return False
```

**重要**:
- Prompt 必须 >= 1024 tokens
- 低于此阈值的请求不会创建缓存
- LiteLLM 会自动计算 token 数进行验证

---

### 2. Cache Control 位置限制

根据 Anthropic API 规范:

**规则**: 在一条消息中,只有**最后一个 content block** 可以有 `cache_control`

```python
# ✅ 正确
{
    "role": "user",
    "content": [
        {"type": "text", "text": "Part 1"},
        {"type": "text", "text": "Part 2"},
        {"type": "text", "text": "Part 3", "cache_control": {"type": "ephemeral"}}
    ]
}

# ❌ 错误 - cache_control 在中间的 block
{
    "role": "user",
    "content": [
        {"type": "text", "text": "Part 1", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "Part 2"}
    ]
}
```

**多条消息缓存**:

```python
# ✅ 可以在多条消息的最后 block 添加 cache_control
messages = [
    {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": "System prompt...",
                "cache_control": {"type": "ephemeral"}  # ← OK
            }
        ]
    },
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "User message...",
                "cache_control": {"type": "ephemeral"}  # ← OK
            }
        ]
    }
]
```

---

### 3. 跨提供商支持

| 提供商 | 支持 cache_control | 支持 anthropic-beta header | 说明 |
|--------|-------------------|---------------------------|------|
| **Anthropic** | ✅ | ✅ | 原生支持,完整功能 |
| **Bedrock** | ✅ | ❌ | Anthropic on AWS,不支持 beta header |
| **Vertex AI** | ✅ | ❌ | Anthropic on GCP,不支持 beta header |

**Bedrock/Vertex AI 使用示例**:

```python
# Bedrock
response = await litellm.acompletion(
    model="bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Hello",
                    "cache_control": {"type": "ephemeral"}  # ← 支持
                }
            ]
        }
    ]
    # 注意: 不要添加 anthropic-beta header (会导致错误)
)

# Vertex AI
response = await litellm.acompletion(
    model="vertex_ai/claude-3-5-sonnet-v2@20241022",
    messages=[...],  # 相同格式
)
```

**实现细节**:

```python
# litellm/llms/anthropic/chat/transformation.py
def update_headers_with_optional_anthropic_beta(
    self, headers: dict, optional_params: dict
) -> dict:
    # 检测 cache_control 并添加 beta header
    # 但在 Bedrock/Vertex AI 调用时会被过滤
    ...
```

---

### 4. TTL (Time-To-Live) 支持

```python
# 5 分钟缓存 (默认)
{"type": "ephemeral"}
{"type": "ephemeral", "ttl": "5m"}

# 1 小时缓存
{"type": "ephemeral", "ttl": "1h"}
```

**Usage 返回的详细信息**:

```python
response.usage.prompt_tokens_details.cache_creation_token_details
# CacheCreationTokenDetails(
#     ephemeral_5m_input_tokens=100,   # 5分钟缓存创建的 tokens
#     ephemeral_1h_input_tokens=500,   # 1小时缓存创建的 tokens
# )
```

**选择策略**:
- **5m**: 短期对话,快速迭代
- **1h**: 长期上下文,如文档分析、代码审查

---

### 5. 成本分析

**Anthropic Prompt Caching 定价** (以 Claude 3.5 Sonnet 为例):

| Token 类型 | 成本 (per 1M tokens) | 相对成本 |
|-----------|---------------------|---------|
| 普通 Input | $3.00 | 100% |
| Cache Write (创建) | $3.75 | 125% |
| Cache Read (读取) | $0.30 | 10% |
| Output | $15.00 | - |

**成本计算示例**:

```python
# 场景: 5000 tokens 的 prompt,重复使用 10 次

# 不使用缓存:
cost_no_cache = 5000 * 10 * $3.00 / 1_000_000 = $0.15

# 使用缓存:
cost_with_cache = (
    5000 * $3.75 / 1_000_000 +      # 首次写入: $0.01875
    5000 * 9 * $0.30 / 1_000_000    # 9次读取: $0.0135
) = $0.03225

# 节省: $0.15 - $0.03225 = $0.11775 (78.5% 成本降低)
```

**最佳实践**:
- 适用于重复使用的长 prompt (>2048 tokens)
- 不适用于单次使用的短 prompt
- 考虑缓存失效的风险 (TTL 过期)

---

## 测试文件

相关测试文件位置:

1. **基础功能测试**: `tests/local_testing/test_anthropic_prompt_caching.py`
   - System message caching
   - Multi-turn conversation
   - Tools with cache control
   - Streaming support

2. **Cache control hook 测试**: `tests/test_litellm/integrations/test_anthropic_cache_control_hook.py`
   - Dynamic injection
   - Index-based targeting
   - Role-based targeting

3. **Router 集成测试**: `tests/router_unit_tests/test_router_prompt_caching.py`
   - Prompt caching with router
   - Model selection for cached prompts

---

## 常见问题 (FAQ)

### Q1: 为什么我的缓存没有生效?

**A**: 检查以下几点:
1. Prompt 是否 >= 1024 tokens
2. `cache_control` 位置是否正确 (最后一个 content block)
3. Headers 是否正确 (`anthropic-beta: prompt-caching-2024-07-31`)
4. 两次请求的 prompt 是否完全一致

### Q2: 如何验证缓存是否命中?

**A**: 检查 response.usage:
```python
if response.usage.cache_read_input_tokens > 0:
    print("✅ Cache hit!")
else:
    print("❌ Cache miss - creating new cache")
```

### Q3: 可以在 Bedrock 上使用吗?

**A**: 可以!但不要添加 `anthropic-beta` header:
```python
# ✅ 正确
response = await litellm.acompletion(
    model="bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
    messages=[...],  # 包含 cache_control
)

# ❌ 错误 - 不要添加 beta header
response = await litellm.acompletion(
    model="bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
    messages=[...],
    extra_headers={"anthropic-beta": "..."}  # ← 会失败
)
```

### Q4: 多个用户的缓存会互相影响吗?

**A**: 不会。缓存是基于:
- API key
- 完整的 prompt 内容 (包括所有标记为 cache_control 的内容)
- Model version

不同用户(不同 API key)的缓存是隔离的。

### Q5: 缓存过期后会怎样?

**A**:
- 请求仍然成功
- 会创建新的缓存 (`cache_creation_input_tokens > 0`)
- 没有额外的错误或警告

### Q6: 可以缓存图片吗?

**A**: 可以!图片被视为普通 content block:
```python
{
    "role": "user",
    "content": [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": "..."
            }
        },
        {
            "type": "text",
            "text": "Describe this image",
            "cache_control": {"type": "ephemeral"}  # ← 缓存包括图片
        }
    ]
}
```

---

## 最佳实践

### 1. 分层缓存策略

```python
messages = [
    # 第 1 层: 系统指令 (1小时缓存)
    {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": "You are an expert...",
                "cache_control": {"type": "ephemeral", "ttl": "1h"}
            }
        ]
    },
    # 第 2 层: 长期上下文 (1小时缓存)
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "Here is a large document...",
                "cache_control": {"type": "ephemeral", "ttl": "1h"}
            }
        ]
    },
    # 第 3 层: 对话历史 (5分钟缓存)
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
                "cache_control": {"type": "ephemeral"}  # 5分钟
            }
        ]
    }
]
```

### 2. 监控缓存效率

```python
def calculate_cache_efficiency(response):
    """计算缓存命中率和成本节省"""
    usage = response.usage

    total_input = usage.prompt_tokens
    cache_hit = usage.cache_read_input_tokens
    cache_miss = usage.cache_creation_input_tokens

    hit_rate = cache_hit / total_input if total_input > 0 else 0

    # 成本节省估算 (Claude 3.5 Sonnet 定价)
    normal_cost = total_input * 3.00 / 1_000_000
    actual_cost = (
        cache_hit * 0.30 / 1_000_000 +
        cache_miss * 3.75 / 1_000_000 +
        (total_input - cache_hit - cache_miss) * 3.00 / 1_000_000
    )
    savings = normal_cost - actual_cost

    return {
        "hit_rate": f"{hit_rate:.1%}",
        "cost_savings": f"${savings:.4f}",
        "cache_hit_tokens": cache_hit,
        "cache_miss_tokens": cache_miss
    }

# 使用
response = await litellm.acompletion(...)
stats = calculate_cache_efficiency(response)
print(f"Cache hit rate: {stats['hit_rate']}")
print(f"Cost savings: {stats['cost_savings']}")
```

### 3. Proxy 级别统一配置

在 LiteLLM Proxy 中为团队配置默认缓存策略:

```yaml
# litellm_config.yaml
model_list:
  - model_name: claude-sonnet
    litellm_params:
      model: anthropic/claude-3-7-sonnet-20250219
      api_key: os.environ/ANTHROPIC_API_KEY

      # 团队级缓存配置
      metadata:
        cache_control_injection_points:
          - location: message
            role: system
            control:
              type: ephemeral
              ttl: 1h
          - location: message
            index: -1
            control:
              type: ephemeral
```

---

## 相关资源

- **Anthropic 官方文档**: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- **LiteLLM 缓存文档**: `docs/my-website/docs/completion/prompt_caching.md`
- **测试文件**: `tests/local_testing/test_anthropic_prompt_caching.py`
- **实现代码**: `litellm/llms/anthropic/chat/transformation.py`

---

## 更新历史

- **2025-01-22**: 初始版本,覆盖 Anthropic cache_control 完整实现
- 基于 LiteLLM commit: `52090c3f3`

---

**维护者**: LiteLLM Team
**最后更新**: 2025-01-22
