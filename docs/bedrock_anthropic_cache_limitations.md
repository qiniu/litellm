# AWS Bedrock Anthropic 模型的 Prompt Caching 限制

## 问题描述

当通过 AWS Bedrock 使用 Anthropic Claude 模型并指定 `cache_control` 的 TTL 时,返回的 usage 对象中**不包含**按 TTL 分类的缓存详细信息(`ephemeral_5m_input_tokens` 和 `ephemeral_1h_input_tokens`)。

## 根本原因

### 1. Bedrock 不支持 TTL 参数

AWS Bedrock 的 Anthropic Claude 实现与 Anthropic 原生 API 存在差异:

- **Anthropic 原生 API**: 支持在 `cache_control` 中指定 `ttl` 参数(如 `"5m"` 或 `"1h"`)
- **AWS Bedrock**: **不支持** `ttl` 参数

**代码位置**: `litellm/llms/bedrock/messages/invoke_transformations/anthropic_claude3_transformation.py` 第 111-130 行

```python
def _remove_ttl_from_cache_control(self, anthropic_messages_request: Dict) -> None:
    """
    Remove `ttl` field from cache_control in messages.
    Bedrock doesn't support the ttl field in cache_control.
    """
    # ... 会移除请求中所有的 TTL 参数
```

### 2. Bedrock API 响应格式限制

Bedrock Converse API 返回的 usage 格式:

```json
{
  "usage": {
    "inputTokens": 2360,
    "outputTokens": 120,
    "totalTokens": 2480,
    "cacheReadInputTokens": 0,      // 从缓存读取的 tokens
    "cacheWriteInputTokens": 3184   // 写入缓存的 tokens
  }
}
```

**注意**: Bedrock **不返回**按 TTL 分类的详细信息,只提供总的缓存写入和读取 token 数量。

## 实际测试结果

### 请求示例
```bash
curl -X POST 'http://127.0.0.1:4000/chat/completions' \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer sk-xxx' \
  --data '{
    "model": "bedrock/anthropic.claude-sonnet-4-5",
    "messages": [
      {
        "role": "system",
        "content": [
          {
            "type": "text",
            "text": "Long context...",
            "cache_control": {"type": "ephemeral", "ttl": "1h"}
          }
        ]
      },
      {
        "role": "user",
        "content": "Your question"
      }
    ]
  }'
```

### 返回的 Usage (流式响应最后一个 chunk)
```json
{
  "usage": {
    "completion_tokens": 10,
    "prompt_tokens": 3196,
    "total_tokens": 3206,
    "completion_tokens_details": {
      "reasoning_tokens": 0
    },
    "prompt_tokens_details": {
      "cached_tokens": 0,
      "cache_creation_tokens": 3184
    },
    "cache_creation_input_tokens": 3184,
    "cache_read_input_tokens": 0
  }
}
```

**缺失的字段**:
- ❌ `cache_creation_token_details.ephemeral_5m_input_tokens`
- ❌ `cache_creation_token_details.ephemeral_1h_input_tokens`

## 对比: Anthropic 原生 API vs Bedrock

| 特性 | Anthropic 原生 API | AWS Bedrock |
|------|-------------------|-------------|
| 支持 TTL 参数 | ✅ 支持 `"5m"` 和 `"1h"` | ❌ 不支持(会被移除) |
| 返回 cache_creation_input_tokens | ✅ | ✅ |
| 返回 cache_read_input_tokens | ✅ | ✅ |
| 返回 ephemeral_5m_input_tokens | ✅ | ❌ |
| 返回 ephemeral_1h_input_tokens | ✅ | ❌ |

### Anthropic 原生 API 完整响应示例
```python
# 使用 model="anthropic/claude-3-7-sonnet-20250219"
response.usage = Usage(
    prompt_tokens=4720,
    completion_tokens=120,
    total_tokens=4840,
    cache_creation_input_tokens=2360,
    cache_read_input_tokens=0,
    prompt_tokens_details=PromptTokensDetailsWrapper(
        cached_tokens=0,
        cache_creation_tokens=2360,
        cache_creation_token_details=CacheCreationTokenDetails(
            ephemeral_5m_input_tokens=100,   # ✅ 5分钟缓存
            ephemeral_1h_input_tokens=2260   # ✅ 1小时缓存
        )
    )
)
```

### Bedrock 响应示例
```python
# 使用 model="bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0"
response.usage = Usage(
    prompt_tokens=3196,
    completion_tokens=10,
    total_tokens=3206,
    cache_creation_input_tokens=3184,
    cache_read_input_tokens=0,
    prompt_tokens_details=PromptTokensDetailsWrapper(
        cached_tokens=0,
        cache_creation_tokens=3184,
        cache_creation_token_details=None   # ❌ 没有 TTL 详细信息
    )
)
```

## 解决方案和建议

### 1. 如果需要 TTL 详细信息,使用 Anthropic 原生 API

```python
import litellm

response = litellm.completion(
    model="anthropic/claude-3-7-sonnet-20250219",  # 使用原生 API
    messages=[
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "Long context...",
                    "cache_control": {"type": "ephemeral", "ttl": "1h"}
                }
            ]
        },
        {"role": "user", "content": "Your question"}
    ]
)

# 可以访问详细的 TTL 信息
if response.usage.prompt_tokens_details.cache_creation_token_details:
    details = response.usage.prompt_tokens_details.cache_creation_token_details
    print(f"5分钟缓存: {details.ephemeral_5m_input_tokens}")
    print(f"1小时缓存: {details.ephemeral_1h_input_tokens}")
```

### 2. 如果必须使用 Bedrock,只能获取总量

```python
import litellm

response = litellm.completion(
    model="bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0",
    messages=[
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "Long context...",
                    "cache_control": {"type": "ephemeral", "ttl": "1h"}  # TTL 会被忽略
                }
            ]
        },
        {"role": "user", "content": "Your question"}
    ]
)

# 只能获取总的缓存创建 tokens
print(f"缓存创建总量: {response.usage.cache_creation_input_tokens}")
print(f"缓存读取总量: {response.usage.cache_read_input_tokens}")

# ❌ 无法区分 5 分钟和 1 小时缓存
```

### 3. 在 LiteLLM Proxy 中的处理

当通过 LiteLLM Proxy 使用 Bedrock 时:

```bash
# proxy 配置
model_list:
  - model_name: claude-sonnet
    litellm_params:
      model: bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0
```

客户端收到的响应中:
- ✅ `cache_creation_input_tokens` - 可用
- ✅ `cache_read_input_tokens` - 可用
- ❌ `cache_creation_token_details` - 不可用

## 技术实现细节

### 代码位置

1. **TTL 移除逻辑**:
   - 文件: `litellm/llms/bedrock/messages/invoke_transformations/anthropic_claude3_transformation.py`
   - 方法: `_remove_ttl_from_cache_control()` (第 111-130 行)

2. **Usage 解析逻辑**:
   - 文件: `litellm/llms/bedrock/chat/converse_transformation.py`
   - 方法: `_transform_usage()` (第 1227-1252 行)

3. **Bedrock Usage 类型定义**:
   - 文件: `litellm/types/llms/bedrock.py`
   - 类型: `ConverseTokenUsageBlock` (第 121-128 行)

### Bedrock 的限制原因

AWS Bedrock 使用统一的 Converse API 来支持多个模型提供商(Anthropic、Meta、Cohere 等)。为了保持 API 的一致性,Bedrock 没有实现 Anthropic 特有的 TTL 功能,而是使用默认的缓存策略。

## 常见问题 FAQ

### Q1: 为什么我设置了 TTL 但没有返回详细信息?

**A**: 如果你使用的是 Bedrock 模型(模型名包含 `bedrock/`),TTL 参数会被自动移除,且响应中不会包含 TTL 详细信息。这是 AWS Bedrock 的限制,不是 LiteLLM 的问题。

### Q2: 如何知道我的请求使用了哪种缓存?

**A**:
- **Anthropic 原生 API**: 检查 `response.usage.prompt_tokens_details.cache_creation_token_details`
- **Bedrock**: 无法区分,只能看到 `cache_creation_input_tokens` 总量

### Q3: Bedrock 的缓存 TTL 是多少?

**A**: Bedrock 使用默认的缓存 TTL,具体时长由 AWS 内部决定,通常为 5 分钟。无法通过 API 参数控制。

### Q4: 能否让 LiteLLM 模拟这些字段?

**A**: 理论上可以将所有 `cache_creation_input_tokens` 归类到 `ephemeral_1h_input_tokens` 或 `ephemeral_5m_input_tokens`,但这会造成数据不准确。目前 LiteLLM 选择忠实反映底层 API 的实际能力。

## 相关链接

- [Anthropic Prompt Caching 文档](https://docs.anthropic.com/claude/docs/prompt-caching)
- [AWS Bedrock Converse API 文档](https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference.html)
- [LiteLLM Anthropic 原生 API 使用指南](./cache_ttl_usage_guide.md)

## 更新日志

- **2025-12-30**: 初始文档,说明 Bedrock 不支持 TTL 详细信息的限制
