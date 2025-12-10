# 多项目 Gemini Context Caching 支持

## 问题场景

当您的项目配置了多个 Vertex AI 项目时，需要确保缓存正确隔离：

```yaml
model_list:
  - model_name: gemini-2.0-flash-bz
    litellm_params:
      model: vertex_ai/gemini-2.0-flash-001
      vertex_project: "gemini-qn-bz"
      vertex_location: "global"

  - model_name: gemini-2.0-flash-prod
    litellm_params:
      model: vertex_ai/gemini-2.0-flash-001
      vertex_project: "gemini-prod"
      vertex_location: "global"

  - model_name: gemini-2.0-flash-dev
    litellm_params:
      model: vertex_ai/gemini-2.0-flash-001
      vertex_project: "gemini-dev"
      vertex_location: "us-central1"
```

**关键问题**：相同的内容在不同项目中会创建独立的缓存，它们不应该互相影响。

## 解决方案

### 缓存键作用域

我们的实现使用 **项目 + 区域** 作为缓存键的作用域：

```python
# 缓存键格式
scoped_key = f"{cache_key}:{vertex_project}:{vertex_location}:{hash}"

# 示例
"content-hash:gemini-qn-bz:global:7c0ff9df"
"content-hash:gemini-prod:global:225155362"
"content-hash:gemini-dev:us-central1:8a3fe421"
```

### 工作原理

```
相同内容 + 不同项目 = 独立缓存

content-hash-abc → gemini-qn-bz    → cache-id-1 ✓
content-hash-abc → gemini-prod     → cache-id-2 ✓
content-hash-abc → gemini-dev      → cache-id-3 ✓
```

## 实际使用

### 场景 1: 基本使用（自动处理）

```python
from litellm import completion

messages = [
    {
        "role": "system",
        "content": [{
            "type": "text",
            "text": "长文档内容...",
            "cache_control": {"type": "ephemeral", "ttl": "3600s"}
        }]
    },
    {"role": "user", "content": "问题"}
]

# 使用 project-1
response1 = completion(model="gemini-2.0-flash-bz", messages=messages)
# 本地缓存键: content-hash:gemini-qn-bz:global:xxx

# 使用 project-2（相同内容）
response2 = completion(model="gemini-2.0-flash-prod", messages=messages)
# 本地缓存键: content-hash:gemini-prod:global:yyy

# ✓ 两个项目各自维护独立的缓存
# ✓ 不会互相干扰
```

### 场景 2: 手动管理（高级）

```python
from litellm.llms.vertex_ai.context_caching.local_cache_manager import get_cache_manager

cache_manager = get_cache_manager()

# 为 project-1 设置缓存
cache_manager.set_cache(
    cache_key="content-hash-abc",
    cache_id="projects/gemini-qn-bz/locations/global/cachedContents/123",
    ttl_seconds=3600,
    vertex_project="gemini-qn-bz",
    vertex_location="global",
    custom_llm_provider="vertex_ai"
)

# 为 project-2 设置缓存（相同内容）
cache_manager.set_cache(
    cache_key="content-hash-abc",  # 相同的内容哈希
    cache_id="projects/gemini-prod/locations/global/cachedContents/456",
    ttl_seconds=3600,
    vertex_project="gemini-prod",  # 不同的项目
    vertex_location="global",
    custom_llm_provider="vertex_ai"
)

# 获取 project-1 的缓存
cache1 = cache_manager.get_cache(
    cache_key="content-hash-abc",
    vertex_project="gemini-qn-bz",
    vertex_location="global",
    custom_llm_provider="vertex_ai"
)
# 返回: projects/gemini-qn-bz/locations/global/cachedContents/123

# 获取 project-2 的缓存
cache2 = cache_manager.get_cache(
    cache_key="content-hash-abc",
    vertex_project="gemini-prod",
    vertex_location="global",
    custom_llm_provider="vertex_ai"
)
# 返回: projects/gemini-prod/locations/global/cachedContents/456
```

## 作用域维度

### 1. 项目隔离

```python
# 同一内容，不同项目
cache_key = "doc-hash-123"

# Project A
cache_manager.set_cache(
    cache_key, "cache-a", 3600,
    vertex_project="project-a",
    vertex_location="global"
)

# Project B
cache_manager.set_cache(
    cache_key, "cache-b", 3600,
    vertex_project="project-b",
    vertex_location="global"
)

# ✓ 两个独立的缓存
```

### 2. 区域隔离

```python
# 同一项目，不同区域
cache_key = "doc-hash-123"

# Global region
cache_manager.set_cache(
    cache_key, "cache-global", 3600,
    vertex_project="my-project",
    vertex_location="global"
)

# US-Central1 region
cache_manager.set_cache(
    cache_key, "cache-us", 3600,
    vertex_project="my-project",
    vertex_location="us-central1"
)

# ✓ 两个独立的缓存
```

### 3. 提供商隔离

```python
cache_key = "doc-hash-123"

# Google AI Studio (Gemini)
cache_manager.set_cache(
    cache_key, "gemini-cache", 3600,
    custom_llm_provider="gemini"
)

# Vertex AI
cache_manager.set_cache(
    cache_key, "vertex-cache", 3600,
    vertex_project="my-project",
    vertex_location="global",
    custom_llm_provider="vertex_ai"
)

# ✓ 两个独立的缓存
```

## 测试验证

### 运行测试

```bash
cd /Users/lizhen/go/src/github.com/litellm

# 运行多项目测试
python3 test_cache_scoping_standalone.py
```

### 预期输出

```
🚀 Multi-Project Cache Scoping Tests
================================================================================

Test 1: Basic Project/Location Scoping
✓ Test passed! 2 independent cache entries created

Scoped keys:
  - content-hash-123:project-1:global:7c0ff9df2f051a2d
  - content-hash-123:project-2:global:225155362746ff4a

Test 2: Your Actual Multi-Project Configuration
✓ gemini-qn-bz (global): projects/gemini-qn-bz/.../123
✓ gemini-prod (global): projects/gemini-prod/.../456
✓ gemini-dev (us-central1): projects/gemini-dev/.../789

🎉 ALL TESTS PASSED!
```

## 性能影响

### 内存占用

```python
# 每个缓存条目
base_entry = ~150 bytes

# 多项目场景（相同内容，3个项目）
3_projects × 150_bytes = 450 bytes

# 100 个不同内容 × 3 个项目
100 × 3 × 150 = 45KB  # 可忽略不计
```

### 查找性能

```python
# 哈希查找：O(1)
# 即使有多个项目，查找速度不受影响

# 单项目：O(1)
# 多项目：O(1)  # 相同！
```

## 实际案例

### 案例 1: 多环境部署

```python
# 开发环境
dev_response = completion(
    model="gemini-2.0-flash-dev",
    messages=messages_with_cache
)
# 缓存在: gemini-dev project

# 生产环境
prod_response = completion(
    model="gemini-2.0-flash-prod",
    messages=messages_with_cache
)
# 缓存在: gemini-prod project

# ✓ 两个环境完全独立
# ✓ 互不影响
```

### 案例 2: 多租户应用

```python
# 租户 A
tenant_a_response = completion(
    model="gemini-tenant-a",  # 映射到 project-a
    messages=messages
)

# 租户 B
tenant_b_response = completion(
    model="gemini-tenant-b",  # 映射到 project-b
    messages=messages
)

# ✓ 每个租户的缓存隔离
# ✓ 数据安全
```

### 案例 3: 地理分布

```python
# 亚洲用户
asia_response = completion(
    model="gemini-asia",  # project-asia, asia-northeast1
    messages=messages
)

# 欧洲用户
eu_response = completion(
    model="gemini-eu",  # project-eu, europe-west1
    messages=messages
)

# ✓ 每个区域独立缓存
# ✓ 降低跨区域延迟
```

## 监控和调试

### 查看所有缓存

```python
from litellm.llms.vertex_ai.context_caching.local_cache_manager import get_cache_manager

cache_manager = get_cache_manager()
stats = cache_manager.get_stats()

print(f"总缓存条目: {stats['total_entries']}")
print(f"有效条目: {stats['valid_entries']}")

# 查看作用域键
for key in stats['cache_keys']:
    print(f"  {key}")
    # 输出示例:
    # content-hash:gemini-qn-bz:global:7c0ff9df
    # content-hash:gemini-prod:global:225155362
```

### 按项目统计

```python
def get_project_stats(manager):
    """统计每个项目的缓存数量"""
    stats = manager.get_stats()
    project_counts = {}

    for key in stats['cache_keys']:
        if ':' in key:
            parts = key.split(':')
            if len(parts) >= 3:
                project = parts[1]
                project_counts[project] = project_counts.get(project, 0) + 1

    return project_counts

# 使用
counts = get_project_stats(cache_manager)
for project, count in counts.items():
    print(f"{project}: {count} caches")

# 输出:
# gemini-qn-bz: 15 caches
# gemini-prod: 23 caches
# gemini-dev: 8 caches
```

### 清理特定项目的缓存

```python
def clear_project_caches(project_name: str):
    """清理特定项目的所有缓存"""
    cache_manager = get_cache_manager()
    stats = cache_manager.get_stats()

    cleared = 0
    for key in stats['cache_keys']:
        if f":{project_name}:" in key:
            # 提取原始 cache_key
            base_key = key.split(':')[0]
            # 失效缓存（需要知道 location）
            # 这个例子假设 global location
            cache_manager.invalidate_cache(
                cache_key=base_key,
                vertex_project=project_name,
                vertex_location="global",
                custom_llm_provider="vertex_ai"
            )
            cleared += 1

    return cleared

# 使用
cleared = clear_project_caches("gemini-dev")
print(f"清理了 {cleared} 个 gemini-dev 的缓存")
```

## 常见问题

### Q1: 如果忘记传递 vertex_project 会怎样？

**A**: 会回退到无作用域的缓存键，可能导致不同项目共享缓存（不推荐）。

```python
# ❌ 错误：没有传递项目信息
cache_manager.set_cache("key", "id", 3600)

# ✅ 正确：传递完整作用域
cache_manager.set_cache(
    "key", "id", 3600,
    vertex_project="my-project",
    vertex_location="global",
    custom_llm_provider="vertex_ai"
)
```

### Q2: 能否跨项目共享缓存？

**A**: 不能，也不应该。Google Vertex AI 的缓存是项目级别的，无法跨项目共享。

### Q3: 如何迁移项目？

**A**: 缓存会自动失效，新项目会创建新缓存。

```python
# 旧配置
# model: vertex_ai/gemini-2.0-flash-001
# vertex_project: "old-project"

# 新配置
# vertex_project: "new-project"

# 结果：
# - old-project 的本地缓存会自动过期
# - new-project 会创建新的缓存
# - 无需手动迁移
```

### Q4: 多个区域会增加成本吗？

**A**: 是的，同样的内容在不同区域需要分别缓存，会占用各自的配额。建议：

```python
# 策略 1: 按区域路由用户
if user_region == "asia":
    model = "gemini-asia"  # asia-northeast1
elif user_region == "europe":
    model = "gemini-eu"    # europe-west1

# 策略 2: 使用 global 区域
# 所有项目使用 global location 可以减少缓存副本
```

## 最佳实践

### 1. 清晰的命名约定

```yaml
model_list:
  # 格式: {service}-{project}-{region}
  - model_name: gemini-bz-global
    litellm_params:
      vertex_project: "gemini-qn-bz"
      vertex_location: "global"

  - model_name: gemini-prod-global
    litellm_params:
      vertex_project: "gemini-prod"
      vertex_location: "global"
```

### 2. 统一的 Location 策略

```yaml
# ✅ 推荐：统一使用 global
# - 减少缓存副本
# - 简化管理
model_list:
  - model_name: gemini-bz
    litellm_params:
      vertex_project: "gemini-qn-bz"
      vertex_location: "global"  # 统一

  - model_name: gemini-prod
    litellm_params:
      vertex_project: "gemini-prod"
      vertex_location: "global"  # 统一
```

### 3. 监控缓存使用

```python
# 定期监控
import schedule

def monitor_cache():
    stats = get_cache_manager().get_stats()
    print(f"[{time.now()}] 缓存统计: {stats['valid_entries']} 个有效条目")

    # 按项目统计
    counts = get_project_stats(get_cache_manager())
    for proj, count in counts.items():
        print(f"  {proj}: {count}")

# 每小时检查一次
schedule.every(1).hours.do(monitor_cache)
```

### 4. 测试新项目

```python
# 添加新项目前先测试
def test_new_project(project_name, location):
    """测试新项目的缓存是否正常工作"""
    cache_manager = get_cache_manager()

    test_key = f"test-{time.time()}"
    test_id = f"projects/{project_name}/locations/{location}/cachedContents/test"

    # 设置
    cache_manager.set_cache(
        test_key, test_id, 60,
        vertex_project=project_name,
        vertex_location=location,
        custom_llm_provider="vertex_ai"
    )

    # 获取
    retrieved = cache_manager.get_cache(
        test_key,
        vertex_project=project_name,
        vertex_location=location,
        custom_llm_provider="vertex_ai"
    )

    assert retrieved == test_id, "缓存测试失败"
    print(f"✓ {project_name} ({location}) 缓存测试通过")

# 使用
test_new_project("gemini-new-proj", "global")
```

## 总结

✅ **自动隔离**：项目 + 区域自动作为作用域
✅ **零配置**：无需额外设置
✅ **性能优化**：哈希查找，O(1) 复杂度
✅ **内存友好**：多项目场景增加的内存可忽略
✅ **易于调试**：清晰的作用域键格式
✅ **生产就绪**：经过完整测试验证

您的多项目配置已完全支持！🎉
