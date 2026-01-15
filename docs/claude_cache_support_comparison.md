# Claude Prompt Caching 支持对比 - Anthropic vs Bedrock vs Vertex AI

## 概览

本文档对比了在不同平台上使用 Claude 模型时,Prompt Caching 功能(特别是 TTL 详细信息)的支持情况。

## 快速对比表

| 平台 | 模型前缀 | 支持 TTL 参数 | 返回 TTL 详细信息 | 推荐度 |
|------|---------|--------------|------------------|--------|
| **Anthropic 原生 API** | `anthropic/` | ✅ 完全支持 | ✅ 完全支持 | ⭐⭐⭐⭐⭐ |
| **GCP Vertex AI** | `vertex_ai/` | ✅ 完全支持 | ✅ 完全支持 | ⭐⭐⭐⭐⭐ |
| **AWS Bedrock** | `bedrock/` | ❌ 不支持(会被移除) | ❌ 不支持 | ⭐⭐ |

## 详细对比

### 1. Anthropic 原生 API ⭐⭐⭐⭐⭐

**模型示例**: `anthropic/claude-3-7-sonnet-20250219`

#### 支持的功能
- ✅ 支持 `cache_control` 参数
- ✅ 支持 TTL 参数 (`"5m"` 和 `"1h"`)
- ✅ 返回 `cache_creation_input_tokens`
- ✅ 返回 `cache_read_input_tokens`
- ✅ 返回 `ephemeral_5m_input_tokens`
- ✅ 返回 `ephemeral_1h_input_tokens`

#### 代码示例
```python
import litellm

response = litellm.completion(
    model="anthropic/claude-3-7-sonnet-20250219",
    messages=[
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "Long context..." * 200,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"}  # ✅ 支持 1小时 TTL
                }
            ]
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Your question",
                    "cache_control": {"type": "ephemeral", "ttl": "5m"}  # ✅ 支持 5分钟 TTL
                }
            ]
        }
    ]
)

# ✅ 完整的 TTL 详细信息
print(f"5分钟缓存: {response.usage.prompt_tokens_details.cache_creation_token_details.ephemeral_5m_input_tokens}")
print(f"1小时缓存: {response.usage.prompt_tokens_details.cache_creation_token_details.ephemeral_1h_input_tokens}")
```

#### 返回的 Usage 示例
```python
Usage(
    prompt_tokens=4720,
    completion_tokens=120,
    total_tokens=4840,
    cache_creation_input_tokens=2360,
    cache_read_input_tokens=0,
    prompt_tokens_details=PromptTokensDetailsWrapper(
        cached_tokens=0,
        cache_creation_tokens=2360,
        cache_creation_token_details=CacheCreationTokenDetails(
            ephemeral_5m_input_tokens=100,   # ✅ 可用
            ephemeral_1h_input_tokens=2260   # ✅ 可用
        )
    )
)
```

---

### 2. GCP Vertex AI ⭐⭐⭐⭐⭐

**模型示例**: `vertex_ai/claude-3-5-sonnet-v2@20241022`

#### 支持的功能
- ✅ 支持 `cache_control` 参数
- ✅ 支持 TTL 参数 (`"5m"` 和 `"1h"`)
- ✅ 返回 `cache_creation_input_tokens`
- ✅ 返回 `cache_read_input_tokens`
- ✅ 返回 `ephemeral_5m_input_tokens`
- ✅ 返回 `ephemeral_1h_input_tokens`

#### 实现细节
Vertex AI 的 Claude 实现继承自 `AnthropicConfig` 类,因此**完全支持** Anthropic 的所有功能,包括 TTL 详细信息。

**代码位置**: `litellm/llms/vertex_ai/vertex_ai_partner_models/anthropic/transformation.py`

```python
class VertexAIAnthropicConfig(AnthropicConfig):
    # 继承 Anthropic 的完整功能

    def transform_request(self, ...):
        # 调用父类方法,保留所有 Anthropic 功能
        data = super().transform_request(...)
        data.pop("model", None)  # 只移除 model 参数
        return data

    def transform_response(self, ...):
        # 调用父类方法,完整解析 usage
        response = super().transform_response(...)
        return response
```

#### 代码示例
```python
import litellm

response = litellm.completion(
    model="vertex_ai/claude-3-5-sonnet-v2@20241022",
    messages=[
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "Long context..." * 200,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"}  # ✅ 支持
                }
            ]
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Your question",
                    "cache_control": {"type": "ephemeral", "ttl": "5m"}  # ✅ 支持
                }
            ]
        }
    ]
)

# ✅ 完整的 TTL 详细信息
details = response.usage.prompt_tokens_details.cache_creation_token_details
print(f"5分钟缓存: {details.ephemeral_5m_input_tokens}")
print(f"1小时缓存: {details.ephemeral_1h_input_tokens}")
```

#### 返回的 Usage 示例
```python
Usage(
    prompt_tokens=4720,
    completion_tokens=120,
    total_tokens=4840,
    cache_creation_input_tokens=2360,
    cache_read_input_tokens=0,
    prompt_tokens_details=PromptTokensDetailsWrapper(
        cached_tokens=0,
        cache_creation_tokens=2360,
        cache_creation_token_details=CacheCreationTokenDetails(
            ephemeral_5m_input_tokens=100,   # ✅ 可用
            ephemeral_1h_input_tokens=2260   # ✅ 可用
        )
    )
)
```

#### 与 Anthropic 原生 API 的区别
唯一的区别是 API endpoint 和认证方式:
- **认证**: 使用 GCP 服务账号而非 Anthropic API Key
- **Endpoint**: GCP Vertex AI endpoint
- **功能**: 与 Anthropic 原生 API **完全相同**

---

### 3. AWS Bedrock ⭐⭐

**模型示例**: `bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0`

#### 支持的功能
- ✅ 支持 `cache_control` 参数
- ❌ **不支持** TTL 参数(请求时会被自动移除)
- ✅ 返回 `cache_creation_input_tokens`
- ✅ 返回 `cache_read_input_tokens`
- ❌ **不返回** `ephemeral_5m_input_tokens`
- ❌ **不返回** `ephemeral_1h_input_tokens`

#### 限制原因

1. **TTL 会被移除** (`anthropic_claude3_transformation.py:111-130`)
   ```python
   def _remove_ttl_from_cache_control(self, anthropic_messages_request: Dict) -> None:
       """
       Remove `ttl` field from cache_control in messages.
       Bedrock doesn't support the ttl field in cache_control.
       """
       # 会移除所有 cache_control.ttl 字段
   ```

2. **Bedrock API 响应格式限制**
   ```python
   class ConverseTokenUsageBlock(TypedDict):
       inputTokens: int
       outputTokens: int
       totalTokens: int
       cacheReadInputTokens: int      # ✅ 有
       cacheWriteInputTokens: int     # ✅ 有
       # ❌ 没有 ephemeral_5m_input_tokens
       # ❌ 没有 ephemeral_1h_input_tokens
   ```

#### 代码示例
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
                    "text": "Long context..." * 200,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"}  # ⚠️ TTL 会被忽略
                }
            ]
        },
        {
            "role": "user",
            "content": "Your question"
        }
    ]
)

# ❌ 无法获取 TTL 详细信息
print(f"缓存创建总量: {response.usage.cache_creation_input_tokens}")  # ✅ 可用
print(f"缓存读取总量: {response.usage.cache_read_input_tokens}")      # ✅ 可用

# ❌ cache_creation_token_details 为 None
details = response.usage.prompt_tokens_details.cache_creation_token_details
print(f"TTL 详细信息: {details}")  # None
```

#### 实际测试结果
```bash
# 请求
curl -X POST 'http://127.0.0.1:4000/chat/completions' \
  -H 'Authorization: Bearer sk-xxx' \
  --data '{
    "model": "bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0",
    "messages": [...],
    "cache_control": {"type": "ephemeral", "ttl": "1h"}
  }'

# 响应 (流式最后一个 chunk)
{
  "usage": {
    "cache_creation_tokens": 3184,    # ✅ 可用
    "cache_read_input_tokens": 0,     # ✅ 可用
    # ❌ 没有 cache_creation_token_details
  }
}
```

#### 返回的 Usage 示例
```python
Usage(
    prompt_tokens=3196,
    completion_tokens=10,
    total_tokens=3206,
    cache_creation_input_tokens=3184,
    cache_read_input_tokens=0,
    prompt_tokens_details=PromptTokensDetailsWrapper(
        cached_tokens=0,
        cache_creation_tokens=3184,
        cache_creation_token_details=None   # ❌ 不可用
    )
)
```

---

## 推荐使用场景

### 使用 Anthropic 原生 API 的场景
- ✅ 需要精确的 TTL 控制和详细的缓存统计
- ✅ 需要区分 5 分钟和 1 小时缓存的使用情况
- ✅ 使用 Anthropic 的最新功能和 Beta 特性
- ✅ 不依赖特定云平台

### 使用 GCP Vertex AI 的场景
- ✅ 需要精确的 TTL 控制和详细的缓存统计
- ✅ 已经使用 GCP 基础设施
- ✅ 需要 GCP 的企业级安全和合规性
- ✅ 需要与其他 GCP 服务集成
- ✅ 享受与 Anthropic 原生 API **完全相同的功能**

### 使用 AWS Bedrock 的场景
- ⚠️ 已经深度绑定 AWS 生态
- ⚠️ 不需要 TTL 详细信息,只需要总的缓存统计
- ⚠️ 可以接受默认的缓存 TTL(约 5 分钟)
- ❌ **不推荐**用于需要精确缓存控制的场景

---

## 迁移建议

### 从 Bedrock 迁移到 Anthropic 或 Vertex AI

如果你当前使用 Bedrock 但需要 TTL 详细信息,可以考虑迁移:

#### 方案 1: 迁移到 Anthropic 原生 API
```python
# 从
model="bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0"
api_key="AWS credentials"

# 改为
model="anthropic/claude-3-7-sonnet-20250219"
api_key=os.getenv("ANTHROPIC_API_KEY")
```

#### 方案 2: 迁移到 Vertex AI
```python
# 从
model="bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0"
api_key="AWS credentials"

# 改为
model="vertex_ai/claude-3-5-sonnet-v2@20241022"
# 使用 GCP 服务账号认证
```

### 代码兼容性
Anthropic 原生 API 和 Vertex AI 的代码**完全兼容**,只需要更改:
1. 模型名称前缀
2. 认证方式

其他代码(messages 格式、cache_control 参数、usage 访问等)保持不变。

---

## 常见问题 FAQ

### Q1: 为什么 Bedrock 不支持 TTL?
**A**: AWS Bedrock 使用统一的 Converse API 来支持多个模型提供商。为了保持 API 的一致性,Bedrock 没有实现 Anthropic 特有的 TTL 功能。

### Q2: Vertex AI 和 Anthropic 原生 API 有什么区别?
**A**: 在 Prompt Caching 功能上,**没有区别**。Vertex AI 完全继承了 Anthropic 的实现。唯一的区别是:
- 认证方式(GCP vs Anthropic API Key)
- API endpoint
- 定价可能不同

### Q3: 如何选择平台?
**A**:
- **需要 TTL 详细信息**: Anthropic 原生 API 或 Vertex AI
- **已在 GCP**: Vertex AI(功能与 Anthropic 相同)
- **已在 AWS 但需要 TTL**: 考虑使用 Anthropic 原生 API
- **只需要总缓存统计**: 可以使用 Bedrock

### Q4: LiteLLM 会模拟 Bedrock 的 TTL 详细信息吗?
**A**: 不会。LiteLLM 选择忠实反映底层 API 的实际能力,而不是创造虚假数据。这确保了数据的准确性和可靠性。

---

## 技术实现位置

### Anthropic 原生 API
- **配置**: `litellm/llms/anthropic/chat/transformation.py` - `AnthropicConfig`
- **Usage 解析**: `calculate_usage()` 方法 (第 1193-1291 行)

### Vertex AI
- **配置**: `litellm/llms/vertex_ai/vertex_ai_partner_models/anthropic/transformation.py`
- **继承**: `VertexAIAnthropicConfig(AnthropicConfig)` - 完全继承 Anthropic 实现

### Bedrock
- **配置**: `litellm/llms/bedrock/messages/invoke_transformations/anthropic_claude3_transformation.py`
- **TTL 移除**: `_remove_ttl_from_cache_control()` (第 111-130 行)
- **Usage 解析**: `litellm/llms/bedrock/chat/converse_transformation.py` - `_transform_usage()` (第 1227-1252 行)

---

## 相关文档

- [Anthropic Prompt Caching 文档](https://docs.anthropic.com/claude/docs/prompt-caching)
- [AWS Bedrock 限制说明](./bedrock_anthropic_cache_limitations.md)
- [LiteLLM Cache TTL 使用指南](./cache_ttl_usage_guide.md)

---

## 更新日志

- **2025-12-30**: 初始文档,对比三个平台的 Prompt Caching 支持情况
