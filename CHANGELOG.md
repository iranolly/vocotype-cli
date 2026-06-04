# VocoType 更新日志

## [2026-06-05] FunASR HTTP 端点 — Hermes 语音识别模型共享

### 概述

本次更新为 VocoType 添加了本地 HTTP 端点，使 Hermes Agent 的语音识别（STT）可以通过 HTTP 直接复用 VocoType 已加载的 FunASR 模型，无需重复加载。同时支持替换词典、专有名词热词偏置和可选的 AI 修正。

### 问题背景

- Hermes 每次语音识别都启动独立 Python 进程重新加载 ASR/VAD/PUNC 三个 ONNX 模型（~8 秒），总耗时 ~14 秒
- 同一份模型在 VocoType 和 Hermes 中重复加载，浪费 GPU 显存
- Hermes 转录结果缺少 VocoType 的替换词典和专有名词修正

### 变更文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `app/funasr_http.py` | **新建** | 可嵌入的 HTTP 服务组件，零依赖（仅 Python stdlib） |
| `funasr_http_server.py` | **新建** | 独立 HTTP 服务入口，VocoType 未运行时自动后备 |
| `hermes_funasr_stt.py` | **新建** | Hermes STT command provider，轻量 HTTP 客户端 |
| `app/transcribe.py` | 修改 | TranscriptionWorker 初始化后自动启动 HTTP 端点 |
| `app/config.py` | 修改 | DEFAULT_CONFIG 增加 `asr.http_server` 配置段 |
| `main.py` | 修改 | 改用 `set_post_processor()` 注入，清理重复代码 |
| `config.json.example` | 修改 | 添加 `http_server` 配置示例 |
| `config.example.json` | 删除 | 统一为 `config.json.example` |

### 架构

```
┌──────────────────────────────────────┐
│           VocoType 进程               │
│  TranscriptionWorker                 │
│   ├─ FunASRServer (ASR/VAD/PUNC 常驻) │
│   └─ FunASRHttpThread :8765          │
│         GET  /health                 │
│         POST /transcribe             │
│   F9 → 本地调用 → <0.5s              │
└──────────────┬───────────────────────┘
               │ HTTP localhost:8765
┌──────────────▼───────────────────────┐
│        Hermes Desktop                │
│  transcription_tools.py              │
│   └─ hermes_funasr_stt.py            │
│      (HTTP 客户端，~0.6s)             │
│  语音消息 → 转录 ~0.6s (提速 23x)     │
└──────────────────────────────────────┘
```

### 新增配置

```json
// config.json → asr.http_server
{
  "asr": {
    "http_server": {
      "enabled": true,        // 是否启动 HTTP 端点
      "host": "127.0.0.1",   // 监听地址
      "port": 8765,           // 监听端口
      "ai_correction": false  // HTTP 转录是否启用 AI 修正
    }
  }
}
```

### HTTP API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查，返回 `post_processor`、`ai_correction` 等状态 |
| `POST` | `/transcribe` | 转录，body: `{"audio_path": "...", "language": "zh"}` |

### 后处理行为

| `ai_correction` | 替换词典 | 专有名词热词 | 拼音修正 | DeepSeek AI |
|:-:|:-:|:-:|:-:|:-:|
| `false` | ✅ | ✅ | ✅ | ❌ |
| `true` | ✅ | ✅ | ✅ | ✅ |

### 性能

| 指标 | 改造前 | 改造后 |
|------|--------|--------|
| Hermes 语音识别 | ~14s/次 | ~0.6s/次 |
| 模型加载次数 | 每次 | 仅首次 |
| GPU 显存占用 | 2x (两份模型) | 1x (共享) |

### 其他修复

- ONNX ContextualParaformer 空热词崩溃：传入占位热词 "的" 规避
- `replacement_dict.process()` 大小写敏感问题已知（后续改进）
