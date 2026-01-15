# Anthropic Beta Header 实现文档

## 概述

本文档描述了 LiteLLM 在处理 chat/completions 请求时，如何在不同云服务提供商（GCP Vertex AI 和 AWS Bedrock）上处理 Claude 模型的 Anthropic `anthropic-beta` headers。

## 背景

Anthropic 使用 `anthropic-beta` header 来启用 beta 功能，例如：
- `computer-use-2024-10-22` / `computer-use-2025-01-24` - 计算机使用工具
- `prompt-caching-2024-07-31` - 提示词缓存
- `pdfs-2024-09-25` - PDF 文件支持
- `tool-search-tool-2025-10-19` - 工具搜索功能
- `token-efficient-tools-2025-02-19` - 高效 token 工具
- `output-128k-2025-02-19` - 扩展输出
- 等等...

不同的云服务提供商在其 API 中对这些 beta 功能的处理方式各不相同。

---

## GCP Vertex AI 实现

**文件位置**: `litellm/llms/vertex_ai/vertex_ai_partner_models/anthropic/transformation.py:74-88`

### 处理流程

1. **自动检测 Beta 功能**
   - 调用 `get_anthropic_beta_list()` 检测是否使用了特定功能：
     - 计算机工具 (`is_computer_tool_used()`)
     - 提示词缓存 (`is_cache_control_set()`)
     - 文件上传 (`is_file_id_used()`)
     - MCP 服务器 (`is_mcp_server_used()`)

2. **Tool Search 特殊处理**
   - 如果检测到 tool search 工具，添加 `"tool-search-tool-2025-10-19"` beta header
   - **Vertex AI 要求此 header** 才能使用 tool search 功能

3. **Beta 值合并**
   - 使用 set 去重
   - 将所有 beta 值添加到**请求体**的 `anthropic_beta` 字段（不是 HTTP header）

### 代码示例

```python
# litellm/llms/vertex_ai/vertex_ai_partner_models/anthropic/transformation.py:74-88
auto_betas = self.get_anthropic_beta_list(
    model=model,
    optional_params=optional_params,
    computer_tool_used=self.is_computer_tool_used(tools),
    prompt_caching_set=self.is_cache_control_set(messages),
    file_id_used=self.is_file_id_used(messages),
    mcp_server_used=self.is_mcp_server_used(optional_params.get("mcp_servers")),
)

beta_set = set(auto_betas)
if tool_search_used:
    beta_set.add("tool-search-tool-2025-10-19")  # Vertex 要求

if beta_set:
    data["anthropic_beta"] = list(beta_set)
```

### 关键特征

- **Beta 来源**: 仅自动检测（不提取用户 header）
- **提示词缓存**: ✅ 通过 `is_cache_control_set()` 检测
- **Tool Search**: ✅ 完全支持，需要特定 beta header
- **输出位置**: 请求体字段 `anthropic_beta`

---

## AWS Bedrock 实现

AWS Bedrock 有两种 API 路由，处理方式略有不同：

### 1. Converse API (`bedrock_converse`)

**文件位置**: `litellm/llms/bedrock/chat/converse_transformation.py:914-973`

#### 处理流程

1. **从用户 Headers 提取**
   - 调用 `get_anthropic_beta_from_headers(headers)` 从 HTTP headers 中提取 `anthropic-beta`
   - 支持逗号分隔的多个值

2. **计算机使用工具检测**
   - 如果检测到 computer use tools，自动添加 `"computer-use-2024-10-22"` beta
   - Computer use tools 被移到 `additionalModelRequestFields["tools"]`

3. **Tool Search 过滤**
   - **Converse API 不支持 tool search tools**
   - 过滤掉 `tool_search_tool_regex_20251119` 和 `tool_search_tool_bm25_20251119`

4. **去重并添加**
   - 保序去重
   - 添加到 `additionalModelRequestFields["anthropic_beta"]`

#### 代码示例

```python
# litellm/llms/bedrock/chat/converse_transformation.py:924-971
# 从用户 headers 提取
anthropic_beta_list = []
if headers:
    user_betas = get_anthropic_beta_from_headers(headers)
    anthropic_beta_list.extend(user_betas)

# 过滤 tool search tools（Converse 不支持）
for tool in original_tools:
    tool_type = tool.get("type", "")
    if tool_type in ("tool_search_tool_regex_20251119", "tool_search_tool_bm25_20251119"):
        continue  # 跳过

# Computer use 检测
if computer_use_tools:
    anthropic_beta_list.append("computer-use-2024-10-22")
    additional_request_params["tools"] = transformed_computer_tools

# 去重并添加
if anthropic_beta_list:
    unique_betas = []
    seen = set()
    for beta in anthropic_beta_list:
        if beta not in seen:
            unique_betas.append(beta)
            seen.add(beta)
    additional_request_params["anthropic_beta"] = unique_betas
```

#### 关键特征

- **Beta 来源**: 用户 HTTP headers + 自动检测（仅 computer use）
- **提示词缓存**: ❌ 不检测
- **Tool Search**: ❌ 不支持，工具被过滤
- **Computer Use**: ✅ 检测，添加 `computer-use-2024-10-22`
- **输出位置**: 嵌套字段 `additionalModelRequestFields["anthropic_beta"]`

---

### 2. Invoke API (`bedrock_invoke`)

**文件位置**: `litellm/llms/bedrock/chat/invoke_transformations/anthropic_claude3_transformation.py:65-123`

#### 处理流程

1. **从用户 Headers 提取**
   - 使用 `get_anthropic_beta_from_headers(headers)` 提取

2. **自动检测 Beta 功能**
   - 调用 `get_anthropic_beta_list()` 检测：
     - 计算机工具
     - 文件上传
     - MCP 服务器
   - **注意**: 不检测提示词缓存（传入 `prompt_caching_set=False`）

3. **Tool Search 特殊处理**
   - 如果在特定条件下使用 tool search，移除默认 beta header
   - 对于 Opus 4 模型，添加 `"tool-search-tool-2025-10-19"`
   - 支持 `tool_search_tool_regex`（标准化格式）
   - **不支持** BM25 变体

4. **合并和去重**
   - 合并用户 headers 和自动检测的 betas
   - 添加到请求体字段 `anthropic_beta`

#### 代码示例

```python
# litellm/llms/bedrock/chat/invoke_transformations/anthropic_claude3_transformation.py:101-121
beta_set = set(get_anthropic_beta_from_headers(headers))
auto_betas = self.get_anthropic_beta_list(
    model=model,
    optional_params=optional_params,
    computer_tool_used=self.is_computer_tool_used(tools),
    prompt_caching_set=False,  # Invoke 不检测缓存
    file_id_used=self.is_file_id_used(messages),
    mcp_server_used=self.is_mcp_server_used(optional_params.get("mcp_servers")),
)
beta_set.update(auto_betas)

# Tool search 处理
if tool_search_used and not (programmatic_tool_calling_used or input_examples_used):
    beta_set.discard(ANTHROPIC_TOOL_SEARCH_BETA_HEADER)
    if "opus-4" in model.lower() or "opus_4" in model.lower():
        beta_set.add("tool-search-tool-2025-10-19")

if beta_set:
    _anthropic_request["anthropic_beta"] = list(beta_set)
```

#### Tool Search 标准化

Invoke API 对 tool search tools 进行标准化：

```python
# 转换 tool_search_tool_regex_20251119 -> tool_search_tool_regex
if tool_type == "tool_search_tool_regex_20251119":
    normalized_tool = tool.copy()
    normalized_tool["type"] = "tool_search_tool_regex"
    normalized_tool["name"] = normalized_tool.get("name", "tool_search_tool_regex")
    normalized_tools.append(normalized_tool)
    continue

# BM25 变体被跳过（不支持）
if tool_type == "tool_search_tool_bm25_20251119":
    continue
```

#### 关键特征

- **Beta 来源**: 用户 HTTP headers + 自动检测
- **提示词缓存**: ❌ 显式不检测（`prompt_caching_set=False`）
- **Tool Search**: ✅ 有条件支持，Opus 4 特殊逻辑
- **Computer Use**: ✅ 检测
- **文件上传**: ✅ 检测
- **MCP Server**: ✅ 检测
- **输出位置**: 请求体字段 `anthropic_beta`

---

## 功能对比矩阵

| 特性 | GCP Vertex AI | AWS Bedrock Converse | AWS Bedrock Invoke |
|------|---------------|---------------------|-------------------|
| **Beta 来源** | 仅自动检测 | 用户 headers + 自动检测 | 用户 headers + 自动检测 |
| **提示词缓存检测** | ✅ 是 | ❌ 否 | ❌ 否（显式 `False`） |
| **Tool Search 支持** | ✅ 是（必需 beta） | ❌ 否（过滤掉） | ✅ 是（有条件） |
| **Tool Search Beta** | `tool-search-tool-2025-10-19` | N/A | `tool-search-tool-2025-10-19`（Opus 4） |
| **Computer Use 检测** | ✅ 是 | ✅ 是（`computer-use-2024-10-22`） | ✅ 是 |
| **文件上传检测** | ✅ 是 | ❌ 否 | ✅ 是 |
| **MCP Server 检测** | ✅ 是 | ❌ 否 | ✅ 是 |
| **Beta 字段位置** | 请求体 `anthropic_beta` | `additionalModelRequestFields["anthropic_beta"]` | 请求体 `anthropic_beta` |
| **去重方法** | Set 去重 | 保序去重 | Set 去重 |
| **用户 Header 支持** | ❌ 否 | ✅ 是 | ✅ 是 |

---

## 通用工具函数

所有实现都使用了共享的工具函数来提取 beta headers：

**文件位置**: `litellm/llms/bedrock/common_utils.py:690-709`

```python
def get_anthropic_beta_from_headers(headers: dict) -> List[str]:
    """
    从 HTTP headers 提取 anthropic-beta 值并转换为列表。
    支持从用户 headers 中提取逗号分隔的值。

    由 converse 和 invoke 转换共同使用，以一致地处理
    应传递给 AWS Bedrock 的 anthropic-beta headers。

    Args:
        headers (dict): 请求 headers 字典

    Returns:
        List[str]: anthropic beta 功能字符串列表，如果没有 header 则返回空列表
    """
    anthropic_beta_header = headers.get("anthropic-beta")
    if not anthropic_beta_header:
        return []

    # 拆分逗号分隔的值并去除空格
    return [beta.strip() for beta in anthropic_beta_header.split(",")]
```

### 使用示例

```python
# 用户传递 header
headers = {
    "anthropic-beta": "computer-use-2024-10-22,pdfs-2024-09-25"
}

# 提取为列表
betas = get_anthropic_beta_from_headers(headers)
# 结果: ["computer-use-2024-10-22", "pdfs-2024-09-25"]
```

---

## 核心要点

1. **提供商差异**: 每个提供商对 beta 功能有不同的能力和要求

2. **Vertex AI 特点**:
   - 仅使用自动检测
   - tool search 需要 `tool-search-tool-2025-10-19`
   - 检测提示词缓存

3. **Bedrock Converse 特点**:
   - 接受用户 headers 但自动检测有限
   - 不支持 tool search
   - 输出到嵌套的 `additionalModelRequestFields`

4. **Bedrock Invoke 特点**:
   - 最全面：用户 headers + 广泛的自动检测
   - 有条件的 tool search 支持（特别是 Opus 4）
   - 标准化 tool search 工具类型
   - 不检测提示词缓存

5. **用户体验**: 用户可以传递 `anthropic-beta` headers，这将：
   - 被 Vertex AI 忽略（仅使用自动检测）
   - 在 Bedrock 上与自动检测的功能合并
   - 根据提供商/路由输出到适当的请求位置

---

## 相关文件

- GCP Vertex AI: `litellm/llms/vertex_ai/vertex_ai_partner_models/anthropic/transformation.py`
- AWS Bedrock Converse: `litellm/llms/bedrock/chat/converse_transformation.py`
- AWS Bedrock Invoke: `litellm/llms/bedrock/chat/invoke_transformations/anthropic_claude3_transformation.py`
- 通用工具: `litellm/llms/bedrock/common_utils.py`
- 基础 Anthropic 配置: `litellm/llms/anthropic/chat/transformation.py`

---

## 实现说明

### 添加新的 Beta 功能

当 Anthropic 发布新的 beta 功能时：

1. **Vertex AI**: 如果需要自动检测，更新基础 Anthropic 配置中的 `get_anthropic_beta_list()`
2. **Bedrock Converse**: 更新 computer use 检测或添加新的工具类型处理
3. **Bedrock Invoke**: 更新 `get_anthropic_beta_list()` 调用并添加任何特殊的标准化逻辑
4. 在所有三个路由上测试以确保正确处理

### 测试注意事项

测试 beta 功能支持时：

- 验证 beta headers 是否正确从用户输入中提取
- 测试每种功能类型的自动检测
- 检查输出位置是否符合提供商预期
- 验证去重是否正确工作
- 特别在 Invoke API 的 Opus 4 模型上测试 tool search
