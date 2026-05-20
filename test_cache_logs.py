#!/usr/bin/env python3
"""
测试 Vertex AI Context Caching 日志是否打印
"""
import os
import logging

# 设置日志级别为 DEBUG
os.environ["LITELLM_LOG"] = "DEBUG"

import litellm
from litellm._logging import verbose_proxy_logger

# 确保 verbose_proxy_logger 设置为 DEBUG
verbose_proxy_logger.setLevel(logging.DEBUG)

# 设置你的 Vertex AI 凭证
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/app/gemini-bz1.json"  # 替换为你的凭证文件路径

print("开始测试 Context Caching 日志...")
print(f"verbose_proxy_logger 当前级别: {verbose_proxy_logger.level}")

# 测试请求
response = litellm.completion(
    model="vertex_ai/gemini-2.0-flash-exp",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "这是一段需要缓存的长文本内容。" * 100,  # 重复100次确保足够长
                    "cache_control": {
                        "type": "ephemeral",
                        "ttl": "3600s"
                    }
                }
            ]
        },
        {
            "role": "user",
            "content": "请总结上面的内容"
        }
    ],
    vertex_project="your-project-id",  # 替换为你的项目ID
    vertex_location="us-central1",  # 替换为你的区域
)

print(f"\n响应: {response.choices[0].message.content}")
print("\n如果上面有中文的 'Vertex AI 上下文缓存' 开头的日志，说明日志系统工作正常")
