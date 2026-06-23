# CleverClip

AI 驱动的视频自动化剪辑工具。通过 LLM 视觉理解视频内容，根据自然语言描述自动规划并执行剪辑。支持从多个视频源中自动选取素材进行混剪。

## 工作流程

```
source_clips/ 下全部 .mp4
        │
        ▼  逐个固定频率抽帧
  视频帧图片
        │
        ▼  LLM 视觉识别内容
  每帧内容描述
        │
        ▼  构建统一时间轴
  时间轴
        │
        ▼  用户自然语言剪辑需求
  剪辑方案
        │
        ▼  LLM 规划片段（跨视频混剪）
  片段列表
        │
        ▼  ffmpeg 裁切 + 拼装
  最终视频
```

## 环境要求

- Python 3.10+
- ffmpeg / ffprobe（系统已安装）
- macOS: brew install ffmpeg

## 安装

```bash
pip install -r requirements.txt
```

## 配置

编辑 `config.yaml`，填入你的 LLM API 信息（支持 OpenAI 兼容接口）：

```yaml
llm:
  vision:
    base_url: "http://127.0.0.1:5000/v1"
    api_key: "api-key-here"
    model: "gemma-4-12B"
    max_tokens: 2048
    batch_size: 5       # 每批发送的图片帧数
    concurrency: 5      # 同时并发发送的批次数
  text:
    base_url: "http://127.0.0.1:5000/v1"
    api_key: "api-key-here"
    model: "gemma-4-12B"
    max_tokens: 8192
```

- `batch_size` 越大，API 调用次数越少，但单请求体积越大
- `concurrency` 越大，并发请求越多，总识别时间越短，但需考虑 API 的并发/速率限制

## 准备视频源

将所有需要处理的 `.mp4` 视频文件放入 `source_clips/` 目录：

```
source_clips/
├── video1.mp4
├── video2.mp4
└── video3.mp4
```

程序会自动扫描该目录下所有 `.mp4` 文件作为输入源。

## 使用

### 仅分析视频（生成时间轴，不剪辑）

```bash
python main.py analyze
```

分析完成后，每个源视频会在 `temp/` 下生成独立的时间轴文件：
```
temp/
├── timeline_video1.json
├── timeline_video2.json
└── timeline_video3.json
```

### 自动剪辑

```bash
# 根据需求自动剪辑（自动扫描 source_clips/ 下所有 .mp4）
python main.py edit "提取所有有人在户外活动的片段"

# 指定输出文件名
python main.py edit "保留精彩的运动画面" -o highlights.mp4

# 复用已有时间轴目录（跳过重复分析）
python main.py edit "不同需求" -t temp/
```

### 剪辑需求示例

- `"提取所有接吻的片段"`
- `"保留户外风景部分，去掉室内场景"`
- `"精彩集锦，只保留人物冲镜头笑的片段"`
- `"提取所有有运动画面的片段"`
- `"把 viedo1-5按顺序拼接起来成一个视频"`

## 项目结构

```
CleverClip/
├── config.yaml              # 配置文件（LLM API、抽帧、输出参数）
├── main.py                  # CLI 入口
├── requirements.txt
├── src/
│   ├── config.py            # 配置管理 + 源视频扫描
│   ├── frame_extractor.py   # ffmpeg 抽帧（每帧标注来源视频）
│   ├── llm_client.py        # LLM 视觉识别 + 剪辑规划（跨视频）
│   ├── timeline_builder.py  # 时间轴构建与展示（含来源信息）
│   ├── video_editor.py      # ffmpeg 裁切与拼接（支持多源混剪）
│   └── utils.py             # 工具函数
├── source_clips/            # 源视频输入目录（放入 .mp4 文件）
├── temp/                    # 临时文件（抽帧图片、中间产物）
└── output/                  # 最终输出视频
```

## 抽帧间隔调整

在 `config.yaml` 中修改 `frame_extraction.interval`：

- `1.0`：每秒一帧（默认，适合短视频）
- `10.0`：每10秒一帧（适合长视频vlog等，节省 API 调用）
- `0.5`：每半秒一帧（高精度，适合快速切换画面的视频）

## 注意事项

1. **API 费用**：视觉识别会发送图片到 LLM，注意 API 调用成本。本例中采用在4060ti主机上私有部署的gemma-4-12B
2. **临时空间**：抽帧会生成大量图片，确保磁盘空间充足
3. **视频编码**：输出使用 H.264 + AAC，兼容性较好
