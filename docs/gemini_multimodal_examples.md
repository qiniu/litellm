# Gemini 多模态使用示例

LiteLLM 提供的 OpenAI 兼容接口可以使用 Gemini 模型处理多模态内容(图片、音频、视频、文件)。本文档展示请求示例及其转换为 Gemini 原生协议的格式。

## 1. 图片处理

### 1.1 使用 HTTPS URL

**OpenAI 格式请求:**
```python
import litellm

response = litellm.completion(
    model="gemini/gemini-2.5-flash",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "这张图片里有什么?"
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://example.com/image.jpg",
                        "detail": "high"  # 可选: "low" 或 "high"
                    }
                }
            ]
        }
    ]
)
```

**转换为 Gemini 原生格式:**
```json
{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "这张图片里有什么?"
        },
        {
          "file_data": {
            "file_uri": "https://example.com/image.jpg",
            "mime_type": "image/jpeg"
          },
          "media_resolution": {
            "level": "MEDIA_RESOLUTION_HIGH"
          }
        }
      ]
    }
  ],
  "generationConfig": {}
}
```

### 1.2 使用 Base64 编码

**OpenAI 格式请求:**
```python
import litellm
import base64

# 读取本地图片并转换为 base64
with open("local_image.jpg", "rb") as image_file:
    base64_image = base64.b64encode(image_file.read()).decode('utf-8')

response = litellm.completion(
    model="gemini/gemini-2.5-flash",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "描述这张图片"
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{base64_image}"
                    }
                }
            ]
        }
    ]
)
```

**转换为 Gemini 原生格式:**
```json
{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "描述这张图片"
        },
        {
          "inline_data": {
            "mime_type": "image/jpeg",
            "data": "<base64_encoded_data>"
          }
        }
      ]
    }
  ]
}
```

### 1.3 使用 Google Cloud Storage (GCS) URI

**OpenAI 格式请求:**
```python
import litellm

response = litellm.completion(
    model="vertex_ai/gemini-2.5-flash",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "分析这张图片"
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "gs://my-bucket/images/photo.png",
                        "format": "image/png"  # 可选,明确指定格式
                    }
                }
            ]
        }
    ],
    vertex_project="your-project-id",
    vertex_location="us-central1"
)
```

**转换为 Gemini 原生格式:**
```json
{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "分析这张图片"
        },
        {
          "file_data": {
            "mime_type": "image/png",
            "file_uri": "gs://my-bucket/images/photo.png"
          }
        }
      ]
    }
  ]
}
```

## 2. 音频处理

### 2.1 使用 input_audio 字段

**OpenAI 格式请求:**
```python
import litellm
import base64

# 读取音频文件
with open("audio.mp3", "rb") as audio_file:
    audio_base64 = base64.b64encode(audio_file.read()).decode('utf-8')

response = litellm.completion(
    model="gemini/gemini-2.5-flash",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "转录这段音频"
                },
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": audio_base64,
                        "format": "mp3"  # 或 "wav", "flac" 等
                    }
                }
            ]
        }
    ]
)
```

**转换为 Gemini 原生格式:**
```json
{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "转录这段音频"
        },
        {
          "inline_data": {
            "mime_type": "audio/mp3",
            "data": "<base64_encoded_audio_data>"
          }
        }
      ]
    }
  ]
}
```

### 2.2 使用 file 字段 (GCS URI)

**OpenAI 格式请求:**
```python
import litellm

response = litellm.completion(
    model="vertex_ai/gemini-2.5-flash",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "总结这段音频的内容"
                },
                {
                    "type": "file",
                    "file": {
                        "file_id": "gs://my-bucket/audio/meeting.wav",
                        "format": "audio/wav"
                    }
                }
            ]
        }
    ],
    vertex_project="your-project-id",
    vertex_location="us-central1"
)
```

**转换为 Gemini 原生格式:**
```json
{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "总结这段音频的内容"
        },
        {
          "file_data": {
            "mime_type": "audio/wav",
            "file_uri": "gs://my-bucket/audio/meeting.wav"
          }
        }
      ]
    }
  ]
}
```

## 3. 视频处理

### 3.1 使用 GCS URI

**OpenAI 格式请求:**
```python
import litellm

response = litellm.completion(
    model="vertex_ai/gemini-2.5-pro",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "描述这个视频中发生了什么"
                },
                {
                    "type": "file",
                    "file": {
                        "file_id": "gs://my-bucket/videos/demo.mp4",
                        "format": "video/mp4"
                    }
                }
            ]
        }
    ],
    vertex_project="your-project-id",
    vertex_location="us-central1"
)
```

**转换为 Gemini 原生格式:**
```json
{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "描述这个视频中发生了什么"
        },
        {
          "file_data": {
            "mime_type": "video/mp4",
            "file_uri": "gs://my-bucket/videos/demo.mp4"
          }
        }
      ]
    }
  ]
}
```

### 3.2 使用 Base64 编码 (较小视频)

**OpenAI 格式请求:**
```python
import litellm
import base64

with open("short_video.mp4", "rb") as video_file:
    video_base64 = base64.b64encode(video_file.read()).decode('utf-8')

response = litellm.completion(
    model="gemini/gemini-2.5-pro",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "分析这段视频"
                },
                {
                    "type": "file",
                    "file": {
                        "file_data": f"data:video/mp4;base64,{video_base64}",
                        "format": "video/mp4"
                    }
                }
            ]
        }
    ]
)
```

**转换为 Gemini 原生格式:**
```json
{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "分析这段视频"
        },
        {
          "inline_data": {
            "mime_type": "video/mp4",
            "data": "<base64_encoded_video_data>"
          }
        }
      ]
    }
  ]
}
```

## 4. 文档/文件处理

### 4.1 PDF 文件

**OpenAI 格式请求:**
```python
import litellm

response = litellm.completion(
    model="vertex_ai/gemini-2.5-pro",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "总结这份 PDF 文档的主要内容"
                },
                {
                    "type": "file",
                    "file": {
                        "file_id": "gs://my-bucket/documents/report.pdf",
                        "format": "application/pdf"
                    }
                }
            ]
        }
    ],
    vertex_project="your-project-id",
    vertex_location="us-central1"
)
```

**转换为 Gemini 原生格式:**
```json
{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "总结这份 PDF 文档的主要内容"
        },
        {
          "file_data": {
            "mime_type": "application/pdf",
            "file_uri": "gs://my-bucket/documents/report.pdf"
          }
        }
      ]
    }
  ]
}
```

### 4.2 文本文件 (TXT, CSV 等)

**OpenAI 格式请求:**
```python
import litellm

response = litellm.completion(
    model="vertex_ai/gemini-2.5-flash",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "分析这个 CSV 文件中的数据趋势"
                },
                {
                    "type": "file",
                    "file": {
                        "file_id": "gs://my-bucket/data/sales.csv",
                        "format": "text/csv"
                    }
                }
            ]
        }
    ],
    vertex_project="your-project-id",
    vertex_location="us-central1"
)
```

**转换为 Gemini 原生格式:**
```json
{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "分析这个 CSV 文件中的数据趋势"
        },
        {
          "file_data": {
            "mime_type": "text/csv",
            "file_uri": "gs://my-bucket/data/sales.csv"
          }
        }
      ]
    }
  ]
}
```

## 5. 混合多模态内容

### 5.1 图片 + 音频 + 文本

**OpenAI 格式请求:**
```python
import litellm

response = litellm.completion(
    model="vertex_ai/gemini-2.5-pro",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "比较这张图片和这段音频的情感基调是否一致"
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "gs://my-bucket/image.jpg"
                    }
                },
                {
                    "type": "file",
                    "file": {
                        "file_id": "gs://my-bucket/audio.mp3",
                        "format": "audio/mp3"
                    }
                }
            ]
        }
    ],
    vertex_project="your-project-id",
    vertex_location="us-central1"
)
```

**转换为 Gemini 原生格式:**
```json
{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "比较这张图片和这段音频的情感基调是否一致"
        },
        {
          "file_data": {
            "mime_type": "image/jpeg",
            "file_uri": "gs://my-bucket/image.jpg"
          }
        },
        {
          "file_data": {
            "mime_type": "audio/mp3",
            "file_uri": "gs://my-bucket/audio.mp3"
          }
        }
      ]
    }
  ]
}
```

### 5.2 多轮对话 + 多模态

**OpenAI 格式请求:**
```python
import litellm

response = litellm.completion(
    model="gemini/gemini-2.5-pro",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "这张图片里是什么动物?"
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://example.com/cat.jpg"
                    }
                }
            ]
        },
        {
            "role": "assistant",
            "content": "这是一只猫。"
        },
        {
            "role": "user",
            "content": "这只猫看起来几岁了?"
        }
    ]
)
```

**转换为 Gemini 原生格式:**
```json
{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "这张图片里是什么动物?"
        },
        {
          "file_data": {
            "mime_type": "image/jpeg",
            "file_uri": "https://example.com/cat.jpg"
          }
        }
      ]
    },
    {
      "role": "model",
      "parts": [
        {
          "text": "这是一只猫。"
        }
      ]
    },
    {
      "role": "user",
      "parts": [
        {
          "text": "这只猫看起来几岁了?"
        }
      ]
    }
  ]
}
```

## 6. 支持的文件类型

根据代码分析,Gemini 1.5+ 支持以下文件类型:

### 图片格式
- `image/png`
- `image/jpeg`
- `image/jpg`
- `image/webp`
- `image/heic`
- `image/heif`

### 音频格式
- `audio/wav`
- `audio/mp3`
- `audio/aiff`
- `audio/aac`
- `audio/ogg`
- `audio/flac`

### 视频格式
- `video/mp4`
- `video/mpeg`
- `video/mov`
- `video/avi`
- `video/x-flv`
- `video/mpg`
- `video/webm`
- `video/wmv`
- `video/3gpp`

### 文档格式
- `application/pdf`
- `text/plain`
- `text/csv`
- `text/html`
- `application/xml`
- `text/xml`

## 7. 重要注意事项

### 7.1 Google AI Studio vs Vertex AI 的区别

**Google AI Studio (gemini/):**
- 不支持 HTTP/HTTPS URL,会自动转换为 base64
- 适合小文件和快速测试
- 使用 `model="gemini/gemini-2.5-flash"`

**Vertex AI (vertex_ai/):**
- 支持 GCS URI (`gs://`)
- 支持 HTTP/HTTPS URL (Gemini 3.0+)
- 适合生产环境和大文件
- 需要配置 `vertex_project` 和 `vertex_location`
- 使用 `model="vertex_ai/gemini-2.5-flash"`

### 7.2 图片分辨率控制 (Gemini 3.0+)

使用 `detail` 参数控制图片分辨率:
- `"detail": "low"` → `MEDIA_RESOLUTION_LOW` (更快,更便宜)
- `"detail": "high"` → `MEDIA_RESOLUTION_HIGH` (更准确,更昂贵)

### 7.3 文件大小限制

- Base64 编码适合小于 20MB 的文件
- 大文件建议上传到 GCS 并使用 `gs://` URI
- 视频文件建议使用 GCS URI

### 7.4 MIME 类型自动检测

如果不指定 `format`,LiteLLM 会根据文件扩展名自动检测 MIME 类型:
```python
# 自动检测
{"type": "file", "file": {"file_id": "gs://bucket/file.mp4"}}

# 明确指定 (推荐)
{"type": "file", "file": {"file_id": "gs://bucket/file.mp4", "format": "video/mp4"}}
```

## 8. 完整示例:图片分析应用

```python
import litellm
import os

# 配置 API 密钥
os.environ["GEMINI_API_KEY"] = "your-api-key"

def analyze_image(image_url: str, question: str):
    """分析图片并回答问题"""
    response = litellm.completion(
        model="gemini/gemini-2.5-flash",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_url,
                            "detail": "high"
                        }
                    }
                ]
            }
        ],
        temperature=0.3
    )
    return response.choices[0].message.content

# 使用示例
result = analyze_image(
    image_url="https://example.com/product.jpg",
    question="这个产品有什么特点?请详细描述。"
)
print(result)
```

## 9. 完整示例:视频内容分析

```python
import litellm
import os

# 配置 Vertex AI
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/path/to/service-account.json"

def analyze_video(video_uri: str):
    """分析 GCS 上的视频内容"""
    response = litellm.completion(
        model="vertex_ai/gemini-2.5-pro",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "请详细描述这个视频的内容,包括:\n1. 主要场景\n2. 出现的人物或物体\n3. 关键动作或事件\n4. 整体时长和节奏"
                    },
                    {
                        "type": "file",
                        "file": {
                            "file_id": video_uri,
                            "format": "video/mp4"
                        }
                    }
                ]
            }
        ],
        vertex_project="your-project-id",
        vertex_location="us-central1",
        max_tokens=2048
    )
    return response.choices[0].message.content

# 使用示例
result = analyze_video("gs://my-bucket/videos/presentation.mp4")
print(result)
```

## 总结

LiteLLM 提供了与 OpenAI 兼容的接口来访问 Gemini 的多模态能力,主要特点:

1. **统一接口**: 使用 OpenAI 的消息格式
2. **自动转换**: 自动转换为 Gemini 原生格式
3. **灵活的文件来源**: 支持 URL、Base64、GCS URI
4. **多模态支持**: 图片、音频、视频、文档
5. **provider 差异处理**: 自动处理 Google AI Studio 和 Vertex AI 的差异

转换逻辑的核心代码位于:
- `litellm/llms/vertex_ai/gemini/transformation.py` - 通用转换逻辑
- `litellm/llms/gemini/chat/transformation.py` - Google AI Studio 特定逻辑
