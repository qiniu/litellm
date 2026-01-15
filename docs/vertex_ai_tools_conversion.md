# Vertex AI Tools/Function Calling 响应转换文档

本文档描述 LiteLLM 如何将 Vertex AI 返回的 function calling 响应转换为 OpenAI 协议格式的 `tool_calls`。

## 概述

当调用 `chat/completions` 接口时，如果使用 Vertex AI 作为渠道（包括 Gemini 和 Claude 模型），LiteLLM 会将提供商返回的响应转换为统一的 OpenAI 格式。本文档重点关注 **响应转换**，即如何将 Vertex AI 的 function calling 响应转换为 OpenAI 的 `tool_calls` 格式。

## 主要代码位置

### 1. Gemini 模型响应转换

#### 文件: `litellm/llms/vertex_ai/gemini/vertex_and_google_ai_studio_gemini.py`

**核心方法调用链**:
```
transform_response (行 1910)
  └─> _transform_google_generate_content_to_openai_model_response (行 1952)
      └─> _process_candidates (行 1739)
          └─> _transform_parts (行 1278)  # 核心转换逻辑
```

**关键函数: `_transform_parts()` (行 1278-1332)**

这是 Gemini 响应转换的核心函数，负责将 Vertex AI Gemini 的 `functionCall` 转换为 OpenAI 的 `tool_calls`。

```python
def _transform_parts(
    parts: List[HttpxPartType],
    cumulative_tool_call_idx: int,
    is_function_call: Optional[bool],
) -> Tuple[
    Optional[ChatCompletionToolCallFunctionChunk],
    Optional[List[ChatCompletionToolCallChunk]],
    int,
]:
    """
    遍历 Gemini 响应中的 parts，提取 functionCall 并转换为 OpenAI 格式
    """
    for part in parts:
        if "functionCall" in part:
            # 1. 提取函数名和参数
            _function_chunk = {
                "name": part["functionCall"]["name"],
                # 关键：将 args 对象转为 JSON 字符串
                "arguments": json.dumps(
                    part["functionCall"]["args"], ensure_ascii=False
                ),
            }

            # 2. 构建 OpenAI 格式的 tool_call
            _tool_response_chunk = {
                "id": f"call_{uuid.uuid4().hex[:28]}",  # 生成唯一 ID
                "type": "function",
                "function": _function_chunk,
                "index": cumulative_tool_call_idx,
            }

            # 3. 处理 thought signature（如果存在）
            thought_signature = part.get("thoughtSignature")
            if thought_signature:
                _tool_response_chunk["provider_specific_fields"] = {
                    "thought_signature": thought_signature
                }
                # 将 signature 编码到 ID 中
                _tool_response_chunk["id"] = _encode_tool_call_id_with_signature(
                    _tool_response_chunk["id"], thought_signature
                )
```

### 2. Claude 模型响应转换

#### 文件路径:
- `litellm/llms/vertex_ai/vertex_ai_partner_models/anthropic/transformation.py`
- `litellm/llms/anthropic/chat/transformation.py`

**核心方法调用链**:
```
VertexAIAnthropicConfig.transform_response (vertex_ai/.../transformation.py:92)
  └─> AnthropicConfig.transform_response (anthropic/.../transformation.py:1432)
      └─> transform_parsed_response (行 1293)
          └─> extract_response_content (行 1118)
              └─> convert_tool_use_to_openai_format (行 140)  # 核心转换逻辑
```

**关键函数: `convert_tool_use_to_openai_format()` (行 140-167)**

这是 Claude 响应转换的核心函数，负责将 Anthropic 的 `tool_use` 转换为 OpenAI 的 `tool_calls`。

```python
@staticmethod
def convert_tool_use_to_openai_format(
    anthropic_tool_content: Dict[str, Any],
    index: int,
) -> ChatCompletionToolCallChunk:
    """
    将 Anthropic tool_use 格式转换为 OpenAI ChatCompletionToolCallChunk 格式

    Args:
        anthropic_tool_content: Anthropic 格式
            {"type": "tool_use", "id": "...", "name": "...", "input": {...}}
        index: tool call 的索引

    Returns:
        OpenAI 格式的 ChatCompletionToolCallChunk
    """
    tool_call = ChatCompletionToolCallChunk(
        id=anthropic_tool_content["id"],  # 直接使用 Anthropic 的 ID
        type="function",
        function=ChatCompletionToolCallFunctionChunk(
            name=anthropic_tool_content["name"],
            # 关键：将 input 对象转为 JSON 字符串
            arguments=json.dumps(anthropic_tool_content["input"]),
        ),
        index=index,
    )

    # 如果有 caller 信息（程序化工具调用）
    if "caller" in anthropic_tool_content:
        tool_call["caller"] = anthropic_tool_content["caller"]

    return tool_call
```

## 格式转换详解

### Gemini 模型转换

**Vertex AI Gemini 原始响应**:
```json
{
  "candidates": [{
    "content": {
      "parts": [{
        "functionCall": {
          "name": "get_weather",
          "args": {
            "location": "Boston",
            "unit": "celsius"
          }
        }
      }]
    }
  }]
}
```

**转换为 OpenAI 格式**:
```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": null,
      "tool_calls": [{
        "id": "call_a1b2c3d4e5f6g7h8i9j0k1l2m3n4",
        "type": "function",
        "function": {
          "name": "get_weather",
          "arguments": "{\"location\": \"Boston\", \"unit\": \"celsius\"}"
        },
        "index": 0
      }]
    }
  }]
}
```

**关键转换点**:
1. `candidates[0].content.parts[]` → `choices[0].message.tool_calls[]`
2. `functionCall.name` → `tool_calls[].function.name`
3. `functionCall.args` (对象) → `tool_calls[].function.arguments` (JSON 字符串)
4. 生成唯一的 `tool_calls[].id` (格式: `call_` + 28位十六进制)

### Claude 模型转换

**Vertex AI Claude 原始响应**:
```json
{
  "content": [{
    "type": "tool_use",
    "id": "toolu_01A2B3C4D5E6F7G8H9I0J1K2",
    "name": "get_weather",
    "input": {
      "location": "Boston",
      "unit": "celsius"
    }
  }]
}
```

**转换为 OpenAI 格式**:
```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": null,
      "tool_calls": [{
        "id": "toolu_01A2B3C4D5E6F7G8H9I0J1K2",
        "type": "function",
        "function": {
          "name": "get_weather",
          "arguments": "{\"location\": \"Boston\", \"unit\": \"celsius\"}"
        },
        "index": 0
      }]
    }
  }]
}
```

**关键转换点**:
1. `content[]` 中的 `tool_use` 类型 → `choices[0].message.tool_calls[]`
2. `tool_use.id` → `tool_calls[].id` (直接复用)
3. `tool_use.name` → `tool_calls[].function.name`
4. `tool_use.input` (对象) → `tool_calls[].function.arguments` (JSON 字符串)

## 转换差异对比

| 特性 | Gemini 原始格式 | Claude 原始格式 | OpenAI 统一格式 |
|------|----------------|----------------|----------------|
| 数据位置 | `candidates[].content.parts[]` | `content[]` | `choices[].message.tool_calls[]` |
| 类型标识 | `functionCall` 字段存在 | `type: "tool_use"` | `type: "function"` |
| 函数名 | `functionCall.name` | `tool_use.name` | `function.name` |
| 参数格式 | `functionCall.args` (对象) | `tool_use.input` (对象) | `function.arguments` (JSON 字符串) |
| ID 生成 | 自动生成 UUID | 使用提供商的 ID | 保持统一格式 |
| 索引 | 累积计数 | 基于 content 索引 | `index` 字段 |

## 完整转换流程

### Gemini 转换流程

```
1. HTTP 响应接收
   └─> vertex_and_google_ai_studio_gemini.py::transform_response()

2. 解析 JSON 为 GenerateContentResponseBody
   └─> _transform_google_generate_content_to_openai_model_response()

3. 处理 candidates
   └─> _process_candidates()
       ├─> 提取元数据（grounding, safety, citations）
       ├─> 提取文本内容
       └─> 转换 parts 为 tool_calls
           └─> _transform_parts()
               ├─> 遍历每个 part
               ├─> 检测 functionCall
               ├─> 提取 name 和 args
               ├─> json.dumps(args) → arguments
               ├─> 生成唯一 ID
               └─> 构建 ChatCompletionToolCallChunk

4. 组装最终响应
   └─> ModelResponse.choices[].message.tool_calls
```

### Claude 转换流程

```
1. HTTP 响应接收
   └─> VertexAIAnthropicConfig.transform_response()
       └─> 调用父类 AnthropicConfig.transform_response()

2. 解析 JSON 响应
   └─> transform_parsed_response()

3. 提取响应内容
   └─> extract_response_content()
       ├─> 遍历 content[]
       ├─> 检测 type == "tool_use"
       └─> 转换为 OpenAI 格式
           └─> convert_tool_use_to_openai_format()
               ├─> 使用原始 ID
               ├─> 提取 name 和 input
               ├─> json.dumps(input) → arguments
               └─> 构建 ChatCompletionToolCallChunk

4. 组装最终响应
   └─> ModelResponse.choices[].message.tool_calls
```

## 特殊处理

### 1. Thought Signature (Gemini)

Gemini 支持 `thoughtSignature` 字段，用于多轮推理：

```python
if thought_signature:
    _tool_response_chunk["provider_specific_fields"] = {
        "thought_signature": thought_signature
    }
    # 编码到 ID 中以保持兼容性
    _tool_response_chunk["id"] = _encode_tool_call_id_with_signature(
        _tool_response_chunk["id"], thought_signature
    )
```

### 2. Caller 信息 (Claude)

Claude 支持 `caller` 字段，用于程序化工具调用：

```python
if "caller" in anthropic_tool_content:
    tool_call["caller"] = anthropic_tool_content["caller"]
```

### 3. 多个 Tool Calls

**Gemini**:
- 每个 `functionCall` part 独立转换
- 使用 `cumulative_tool_call_idx` 跟踪索引

**Claude**:
- 遍历 `content[]` 数组
- 每个 `tool_use` 项按顺序转换
- 使用 `enumerate(idx)` 作为索引

### 4. JSON 序列化

两种模型都使用 `json.dumps()` 将对象转换为字符串：

```python
# Gemini
arguments=json.dumps(part["functionCall"]["args"], ensure_ascii=False)

# Claude
arguments=json.dumps(anthropic_tool_content["input"])
```

**注意**: Gemini 使用 `ensure_ascii=False` 以支持 Unicode 字符。

## 使用示例

### 调用示例

```python
import litellm

# 使用 Vertex AI Gemini
response = litellm.completion(
    model="vertex_ai/gemini-1.5-pro",
    messages=[{"role": "user", "content": "What's the weather in Boston?"}],
    tools=[{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
                },
                "required": ["location"]
            }
        }
    }]
)

# 使用 Vertex AI Claude
response = litellm.completion(
    model="vertex_ai/claude-3-5-sonnet@20241022",
    messages=[{"role": "user", "content": "What's the weather in Boston?"}],
    tools=[...]  # 同上
)

# 统一的访问方式
if response.choices[0].message.tool_calls:
    for tool_call in response.choices[0].message.tool_calls:
        print(f"Tool ID: {tool_call.id}")
        print(f"Function: {tool_call.function.name}")
        print(f"Arguments: {tool_call.function.arguments}")
        # Arguments 是 JSON 字符串，需要解析
        import json
        args = json.loads(tool_call.function.arguments)
        print(f"Parsed args: {args}")
```

### 响应处理

```python
# 检查是否有 tool calls
if response.choices[0].message.tool_calls:
    # 提取第一个 tool call
    tool_call = response.choices[0].message.tool_calls[0]

    # 解析参数
    import json
    function_args = json.loads(tool_call.function.arguments)

    # 执行函数
    if tool_call.function.name == "get_weather":
        result = get_weather(**function_args)

        # 继续对话
        messages = [
            {"role": "user", "content": "What's the weather in Boston?"},
            response.choices[0].message,  # 包含 tool_calls
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(result)
            }
        ]

        final_response = litellm.completion(
            model="vertex_ai/gemini-1.5-pro",
            messages=messages,
            tools=[...]
        )
```

## 相关文件

### Gemini 转换
- `litellm/llms/vertex_ai/gemini/vertex_and_google_ai_studio_gemini.py` (行 1278, 1739, 1910, 1952)
- `litellm/types/llms/vertex_ai.py` - Vertex AI 类型定义

### Claude 转换
- `litellm/llms/vertex_ai/vertex_ai_partner_models/anthropic/transformation.py` (行 92)
- `litellm/llms/anthropic/chat/transformation.py` (行 140, 1118, 1293, 1432)
- `litellm/types/llms/anthropic.py` - Anthropic 类型定义

### 通用类型
- `litellm/types/llms/openai.py` - OpenAI 类型定义
- `litellm/types/utils.py` - 通用工具类型

## 调试技巧

### 查看原始响应

```python
response = litellm.completion(...)

# 访问隐藏的原始响应
if hasattr(response, '_hidden_params'):
    original_response = response._hidden_params.get('original_response')
    print("Original response:", original_response)
```

### 启用详细日志

```python
import litellm
litellm.set_verbose = True

response = litellm.completion(...)
# 会输出详细的转换日志
```

## 注意事项

1. **参数格式**:
   - Vertex AI 原始格式使用对象
   - OpenAI 格式使用 JSON 字符串
   - 必须使用 `json.loads()` 解析参数

2. **ID 格式差异**:
   - Gemini: 自动生成 `call_` + 28位十六进制
   - Claude: 使用原始 `toolu_` 前缀的 ID
   - 在多轮对话中使用相同的 ID

3. **索引管理**:
   - Gemini: 使用累积索引（跨多个 parts）
   - Claude: 基于 content 数组索引
   - 都从 0 开始

4. **错误处理**:
   - 转换失败会抛出 `VertexAIError` 或 `AnthropicError`
   - 需要适当的异常捕获

5. **性能考虑**:
   - JSON 序列化/反序列化有性能开销
   - 大型参数对象会影响响应时间

## 参考资源

- [OpenAI Function Calling 文档](https://platform.openai.com/docs/guides/function-calling)
- [Vertex AI Gemini Function Calling](https://cloud.google.com/vertex-ai/docs/generative-ai/multimodal/function-calling)
- [Anthropic Claude Tool Use](https://docs.anthropic.com/claude/docs/tool-use)
- [LiteLLM 官方文档](https://docs.litellm.ai/)
