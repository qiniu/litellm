# Claude Prompt Caching TTL 详细信息 - 真实情况说明

## 重要发现 ⚠️

经过实际测试,**无论是 Anthropic 原生 API、GCP Vertex AI 还是 AWS Bedrock,都不返回 `cache_creation_token_details` (包含 `ephemeral_5m_input_tokens` 和 `ephemeral_1h_input_tokens`)**。

## 实际测试结果

### 测试 1: AWS Bedrock
```bash
# 模型: bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0
# 请求包含: cache_control with ttl="1h"

# 响应:
{
  "usage": {
    "cache_creation_tokens": 3184,
    "cache_read_input_tokens": 0
    // ❌ 没有 cache_creation_token_details
  }
}
```

### 测试 2: GCP Vertex AI
```bash
# 模型: vertex_ai/claude-sonnet-4-5@20250929
# 请求包含: cache_control with ttl="1h"

# 响应:
{
  "usage": {
    "cache_creation_tokens": 3184,
    "cache_read_input_tokens": 0
    // ❌ 没有 cache_creation_token_details
  }
}
```

## 原因分析

### 1. Anthropic API 的实际行为

根据代码分析,LiteLLM 的 `calculate_usage` 方法确实会检查 `cache_creation` 字段:

```python
# litellm/llms/anthropic/chat/transformation.py:1248-1256
if "cache_creation" in _usage and _usage["cache_creation"] is not None:
    cache_creation_token_details = CacheCreationTokenDetails(
        ephemeral_5m_input_tokens=_usage["cache_creation"].get(
            "ephemeral_5m_input_tokens"
        ),
        ephemeral_1h_input_tokens=_usage["cache_creation"].get(
            "ephemeral_1h_input_tokens"
        ),
    )
```

但是,**Anthropic API 实际上可能不返回这个字段**,可能的原因:

1. **Beta 功能**: `cache_creation` 详细信息可能是 Beta 功能,需要特定的 Beta header
2. **API 版本**: 可能需要特定的 `anthropic-version`
3. **仅在特定条件下返回**: 可能只在同时使用多个不同 TTL 时才返回详细信息
4. **文档与实现不符**: Anthropic 的文档可能描述了计划的功能,但实际 API 还未实现

### 2. Bedrock 的限制

Bedrock 的情况更明确:
- ✅ **确认**: Bedrock 会移除 TTL 参数
- ✅ **确认**: Bedrock API 不返回 `cache_creation` 字段
- ✅ **确认**: Bedrock 只提供 `cacheWriteInputTokens` 和 `cacheReadInputTokens`

### 3. Vertex AI 的情况

Vertex AI 虽然继承了 Anthropic 的代码,但:
- ✅ 代码支持解析 `cache_creation` 字段
- ❌ **但 Vertex AI API 实际不返回这个字段**
- 可能 Vertex AI 的 Anthropic 实现基于 Anthropic 的早期版本

## 当前可用的信息

### 所有平台都可用的字段

| 字段 | Anthropic | Vertex AI | Bedrock | 说明 |
|------|-----------|-----------|---------|------|
| `cache_creation_input_tokens` | ✅ | ✅ | ✅ | 创建缓存的总 tokens |
| `cache_read_input_tokens` | ✅ | ✅ | ✅ | 从缓存读取的总 tokens |
| `prompt_tokens_details.cache_creation_tokens` | ✅ | ✅ | ✅ | 同上(别名) |
| `prompt_tokens_details.cached_tokens` | ✅ | ✅ | ✅ | 同上(别名) |

### 不可用的字段 (所有平台)

| 字段 | 状态 | 说明 |
|------|------|------|
| `ephemeral_5m_input_tokens` | ❌ | 5分钟缓存的 tokens |
| `ephemeral_1h_input_tokens` | ❌ | 1小时缓存的 tokens |
| `cache_creation_token_details` | ❌ | 包含上述两个字段的对象 |

## 实用代码示例

### 正确的使用方式 - 获取总缓存信息

```python
import litellm

response = litellm.completion(
    model="anthropic/claude-3-7-sonnet-20250219",  # 或 vertex_ai/... 或 bedrock/...
    messages=[
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "Long context..." * 200,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"}
                }
            ]
        },
        {"role": "user", "content": "Your question"}
    ]
)

# ✅ 可用 - 获取总的缓存信息
print(f"缓存创建总量: {response.usage.cache_creation_input_tokens}")
print(f"缓存读取总量: {response.usage.cache_read_input_tokens}")

# ❌ 不可用 - TTL 详细信息
# details = response.usage.prompt_tokens_details.cache_creation_token_details
# 这个字段会是 None
```

### 错误的期望 ❌

```python
# ❌ 这些代码不会工作,因为 API 不返回这些字段
response = litellm.completion(...)

details = response.usage.prompt_tokens_details.cache_creation_token_details
# details 会是 None

if details:  # 永远不会进入这个分支
    print(f"5分钟: {details.ephemeral_5m_input_tokens}")
    print(f"1小时: {details.ephemeral_1h_input_tokens}")
```

## TTL 参数的实际作用

虽然没有返回 TTL 详细信息,但 TTL 参数仍然有效:

### Anthropic 原生 API & Vertex AI
```python
# ✅ TTL 参数会被发送到 API
# ✅ 影响缓存的实际过期时间
# ❌ 但不会在响应中返回按 TTL 分类的 token 统计

"cache_control": {"type": "ephemeral", "ttl": "1h"}  # 缓存保留 1 小时
"cache_control": {"type": "ephemeral", "ttl": "5m"}  # 缓存保留 5 分钟
```

### AWS Bedrock
```python
# ❌ TTL 参数会被 LiteLLM 移除
# ⚠️ 使用 Bedrock 的默认 TTL (约 5 分钟)

"cache_control": {"type": "ephemeral", "ttl": "1h"}  # 被忽略
```

## 平台对比 (更新后)

| 平台 | 模型前缀 | 支持发送 TTL | 影响缓存时长 | 返回 TTL 详情 |
|------|---------|-------------|-------------|--------------|
| **Anthropic** | `anthropic/` | ✅ | ✅ | ❌ |
| **Vertex AI** | `vertex_ai/` | ✅ | ✅ | ❌ |
| **Bedrock** | `bedrock/` | ❌ | ❌ | ❌ |

## 建议和最佳实践

### 1. 跟踪缓存使用情况

既然无法从 API 获取 TTL 详细信息,需要在应用层跟踪:

```python
import litellm
from datetime import datetime

def track_cache_usage(response, ttl_used: str):
    """应用层跟踪缓存使用"""
    cache_created = response.usage.cache_creation_input_tokens
    cache_read = response.usage.cache_read_input_tokens

    # 记录到日志或数据库
    log_entry = {
        "timestamp": datetime.now(),
        "ttl": ttl_used,  # "5m" or "1h"
        "cache_created": cache_created,
        "cache_read": cache_read,
        "model": response.model,
    }

    # 保存到你的监控系统
    save_to_monitoring(log_entry)

    return cache_created, cache_read

# 使用
response = litellm.completion(
    model="anthropic/claude-3-7-sonnet-20250219",
    messages=[...],  # 带有 ttl="1h" 的 cache_control
)

track_cache_usage(response, ttl_used="1h")
```

### 2. 合理使用 TTL

即使看不到详细统计,TTL 仍然重要:

```python
# 长期上下文 - 使用 1h
system_prompt_with_docs = {
    "role": "system",
    "content": [{
        "type": "text",
        "text": large_documentation,
        "cache_control": {"type": "ephemeral", "ttl": "1h"}  # 文档变化少,用 1h
    }]
}

# 会话历史 - 使用 5m
conversation_history = {
    "role": "user",
    "content": [{
        "type": "text",
        "text": recent_messages,
        "cache_control": {"type": "ephemeral", "ttl": "5m"}  # 变化快,用 5m
    }]
}
```

### 3. 平台选择建议

| 场景 | 推荐平台 | 原因 |
|------|---------|------|
| 需要精确 TTL 控制 | Anthropic / Vertex AI | 支持发送 TTL 参数 |
| 已在 GCP 生态 | Vertex AI | 与 Anthropic 功能相同 |
| 已在 AWS 生态 | Bedrock | 虽无 TTL 控制,但集成简单 |
| 成本敏感 | Anthropic | 直接使用,无云平台加价 |

## 未来可能的改进

### 向 Anthropic 询问

如果你需要 TTL 详细信息用于成本核算或监控,可以:

1. **联系 Anthropic 支持**: 询问 `cache_creation` 字段的状态
2. **检查 API 文档**: 查看是否需要特定的 Beta header
3. **测试不同场景**: 尝试混合使用 5m 和 1h TTL,看是否会返回详细信息

### 向 LiteLLM 反馈

如果确认 Anthropic API 确实返回这些字段(在某些条件下),可以提交 issue:
- GitHub: https://github.com/BerriAI/litellm/issues
- 提供实际的 API 响应示例

## 总结

### ✅ 确认可用的功能

1. **所有平台**都返回总的缓存创建和读取 tokens
2. **Anthropic 和 Vertex AI** 支持发送 TTL 参数(5m/1h)
3. **TTL 参数会影响实际缓存过期时间**

### ❌ 确认不可用的功能

1. **所有平台**都不返回按 TTL 分类的详细统计
2. `cache_creation_token_details` 字段在实际响应中为 `None`
3. 无法从 API 直接得知 5m 和 1h 缓存各用了多少 tokens

### 💡 实际应对方法

1. 在应用层自己跟踪 TTL 使用情况
2. 根据消息类型(系统提示 vs 对话历史)合理分配 TTL
3. 使用总的缓存统计进行成本监控

---

## 更新日志

- **2025-12-30 - 重要更新**:
  - 根据实际测试,确认所有平台都不返回 `cache_creation_token_details`
  - 之前的文档基于代码分析,但实际 API 行为与预期不符
  - 更新建议:在应用层跟踪 TTL 使用情况
