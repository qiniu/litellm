#!/usr/bin/env python3
"""
测试本地缓存的后台自动清理功能
"""
import time
import logging
import os

# 设置日志级别
os.environ["LITELLM_LOG"] = "DEBUG"

from litellm._logging import verbose_proxy_logger
from litellm.llms.vertex_ai.context_caching.local_cache_manager import (
    get_cache_manager,
)

# 设置日志级别为 DEBUG
verbose_proxy_logger.setLevel(logging.DEBUG)

# 添加控制台 handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
verbose_proxy_logger.addHandler(console_handler)

print("=" * 80)
print("测试本地缓存后台自动清理功能")
print("=" * 80)

# 获取缓存管理器（带有 10 秒清理间隔用于快速测试）
cache_manager = get_cache_manager()

# 显示初始统计
print("\n📊 初始缓存统计:")
stats = cache_manager.get_stats()
for key, value in stats.items():
    print(f"  {key}: {value}")

# 添加一些测试缓存（TTL=15秒）
print("\n➕ 添加 5 个测试缓存 (TTL=15秒)...")
for i in range(5):
    cache_manager.set_cache(
        cache_key=f"test_cache_{i}",
        cache_id=f"projects/test/locations/global/cachedContents/cache_{i}",
        ttl_seconds=15,
        vertex_project="test-project",
        vertex_location="global",
        custom_llm_provider="vertex_ai"
    )
    print(f"  ✓ 添加缓存 test_cache_{i}")

# 显示添加后统计
print("\n📊 添加后缓存统计:")
stats = cache_manager.get_stats()
for key, value in stats.items():
    print(f"  {key}: {value}")

# 测试查询缓存
print("\n🔍 测试查询缓存 test_cache_0:")
result = cache_manager.get_cache(
    cache_key="test_cache_0",
    vertex_project="test-project",
    vertex_location="global",
    custom_llm_provider="vertex_ai"
)
print(f"  查询结果: {result}")

# 等待 20 秒让缓存过期
print("\n⏳ 等待 20 秒让缓存过期...")
for i in range(20, 0, -1):
    print(f"  倒计时: {i} 秒", end='\r')
    time.sleep(1)
print("\n")

# 再次查询（应该触发惰性删除）
print("🔍 再次查询过期的缓存 test_cache_0:")
result = cache_manager.get_cache(
    cache_key="test_cache_0",
    vertex_project="test-project",
    vertex_location="global",
    custom_llm_provider="vertex_ai"
)
print(f"  查询结果: {result} (应该是 None)")

# 显示统计（应该看到一个被惰性删除）
print("\n📊 惰性删除后统计:")
stats = cache_manager.get_stats()
for key, value in stats.items():
    print(f"  {key}: {value}")

# 测试后台清理功能
# 注意：默认清理间隔是 300 秒（5分钟）
# 这里我们手动触发清理来演示
print("\n🧹 手动触发过期缓存清理:")
removed = cache_manager.cleanup_expired()
print(f"  删除了 {removed} 个过期缓存项")

# 最终统计
print("\n📊 清理后最终统计:")
stats = cache_manager.get_stats()
for key, value in stats.items():
    print(f"  {key}: {value}")

# 测试 shutdown
print("\n🛑 测试优雅关闭:")
cache_manager.shutdown()

print("\n" + "=" * 80)
print("✅ 测试完成！")
print("=" * 80)
print("\n说明：")
print("1. 后台清理线程在创建 cache_manager 时自动启动")
print("2. 默认每 5 分钟（300秒）清理一次过期缓存")
print("3. 当查询缓存时，如果过期会被惰性删除（Lazy Deletion）")
print("4. 后台线程是守护线程，不会阻止程序退出")
print("5. 可以调用 shutdown() 优雅关闭后台线程")
