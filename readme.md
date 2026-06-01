# VocoType-CLI

**语音输入工具 — 按住说话，松开打字**

基于 [233stone/vocotype-cli](https://github.com/233stone/vocotype-cli) 的增强 Fork，增加替换词典、专有名词热词注入、AI 文本修正三大后处理模块。

---

## 目录

- [功能概览](#功能概览)
- [Fork 修改说明](#fork-修改说明)
- [快速开始](#快速开始)
- [配置文件详解](#配置文件详解)
- [后处理管道](#后处理管道)
- [使用示例](#使用示例)
- [常见问题](#常见问题)

---

## 功能概览

| 功能 | 说明 |
|------|------|
| **Push-to-Talk** | 按住 F9 录音，松开自动转录并输入到当前光标位置 |
| **快速模式 (F9)** | 按住 F9 → 录音 → 松开 → 本地 ASR 离线转录 → 输入 |
| **AI 润色模式 (Shift+F9)** | 同上，额外调用 AI 修正文本（去口语化、修正同音字） |
| **系统托盘** | 后台常驻，任务栏图标，右键退出 |
| **替换词典** | 自动替换 ASR 常见错误词（如"人工智障"→"人工智能"） |
| **专有名词热词** | 注入 FunASR 模型级偏置 + 拼音后处理纠错（同音/近音自动修正） |
| **异步转录** | 录音和转录分离，松开立即开始下一次录音 |
| **启动保护** | 启动后 2 秒内忽略按键，防止幽灵事件误触 |
| **运行时热加载** | 修改 replacement_dict.json 或 proper_nouns.json 下次录音立即生效 |
| **GPU 加速** | 可选 CUDA 加速，RTX 2060 推理从 ~400ms 降至 ~50ms |
| **火山引擎后端** | 可选云端流式识别（配置 volcengine 相关参数即可切换） |

---

## Fork 修改说明

本 Fork 相对上游仓库做了以下修改：

### 键盘交互重构
- **Toggle → Push-to-Talk**：上游使用 `keyboard` 库的 F2 开关键，本 Fork 改用 `pynput` 实现按住说话、松开打字，更符合语音输入直觉
- **Shift+F9 长句模式**：按住 F9 为快速模式（纯本地转录），同时按住 Shift+F9 进入 AI 润色模式（额外调用 DeepSeek 修正文本）
- **启动保护期**：启动后 2 秒内忽略 F9 按键，彻底解决 `pynput` 全局钩子首次注册时的幽灵事件问题

### ASR 模型升级
- **ONNX → PyTorch ContextualParaformer**：上游使用 ONNX 格式的 Paraformer，本 Fork 改用 PyTorch 版 ContextualParaformer，**支持 hotword 偏置参数**，专有名词识别准确率大幅提升
- **hotword 格式自动转换**：配置文件中用逗号分隔热词，代码自动转换为 FunASR 需要的空格分隔格式

### 后处理管道（新增模块）
新增三层后处理流水线，按序处理 ASR 输出：

```
ASR 输出 → 替换词典 → 专有名词拼音修正 → AI 修正（仅长句模式）→ 去句末标点 → 输入光标
```

1. **替换词典** (app/replacement_dict.py) — 精确字符串替换，修复已知 ASR 常见错误
2. **专有名词拼音修正** (app/proper_nouns.py) — 滑动窗口拼音匹配 + Levenshtein 英文纠错，自动修正同音字
3. **AI 文本修正** (app/ai_corrector.py) — 调用 OpenAI 兼容 API（默认 DeepSeek），去口语化、顺句、补标点

### 系统托盘
- 新增 `pystray` 系统托盘图标，后台静默运行
- 提供 `start_vocotype.bat`（无窗口）和 `start_vocotype_debug.bat`（调试窗口）两种启动方式

### 配置系统增强
- 新增 `replacement_dict`、`proper_nouns`、`ai_correction` 三段配置块
- 支持 `config.json` 覆盖默认配置（合并式加载，只覆盖指定字段）
- 日志目录支持相对路径（基于项目根目录）

### 其他改进
- `funasr_server.py` 移除所有 ONNX 加载代码，仅保留 PyTorch AutoModel 路径
- `transcribe.py` 增加异步转录工作线程、转录计数器、会话大小限制
- 删除 `脚本.md`（已过时的安装说明）
- GPU 设备通过 `config.json` 的 `asr.device` 或环境变量 `FUNASR_DEVICE` 配置

---

## 快速开始

### 前置要求

- Windows 10 / 11
- Python 3.10+
- 麦克风可用

### 安装

```bash
# 1. 克隆仓库
git clone https://github.com/iranolly/vocotype-cli.git
cd vocotype-cli

# 2. 创建虚拟环境（推荐使用 uv）
uv venv --python 3.12
# 或: python -m venv .venv

# 3. 激活虚拟环境
.venv\Scripts\activate

# 4. 安装依赖
uv pip install -r requirements.txt
# 或: pip install -r requirements.txt

# 5. 首次启动（下载 ASR 模型，约 1.4GB）
python main.py --once
# 首次启动会自动下载 FunASR ContextualParaformer + VAD + 标点模型
# 请保持网络畅通，下载完成后自动转录一次并退出
```

### 启动

**静默托盘模式（推荐）：**
```bash
start_vocotype.bat
```
或直接双击 `start_vocotype.bat`

**调试模式（有控制台日志）：**
```bash
start_vocotype_debug.bat
```
或：
```bash
python main.py --config config.json
```

### 使用

| 操作 | 效果 |
|------|------|
| 按住 **F9** | 开始录音（蓝色托盘图标常亮） |
| 松开 **F9** | 停止录音 → 本地转录 → 文本自动输入 |
| 按住 **Shift+F9** | 开始录音（AI 润色模式） |
| 松开 **Shift+F9** | 停止录音 → 本地转录 → AI 修正 → 文本输入 |
| 托盘右键 → **退出** | 退出程序 |

---

## 配置文件详解

配置文件 `config.json` 位于项目根目录，所有字段均有默认值，只需覆盖需要修改的部分即可。

### 完整配置模板

```json
{
  "hotkeys": {
    "quick": "f9",
    "long": "shift+f9"
  },
  "audio": {
    "sample_rate": 16000,
    "block_ms": 20,
    "device": null,
    "max_session_bytes": 20971520
  },
  "vad": {
    "start_threshold": 0.02,
    "stop_threshold": 0.01,
    "min_speech_ms": 300,
    "min_silence_ms": 200,
    "pad_ms": 200
  },
  "backend": "funasr",
  "asr": {
    "use_vad": false,
    "use_punc": true,
    "language": "zh",
    "hotword": "",
    "batch_size_s": 60.0,
    "device": ""
  },
  "volcengine": {
    "app_key": "",
    "access_key": "",
    "resource_id": "volc.bigasr.sauc.duration",
    "url": "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel",
    "model_name": "bigmodel",
    "chunk_ms": 100,
    "enable_punc": true,
    "enable_itn": true
  },
  "output": {
    "dedupe": true,
    "max_history": 5,
    "min_chars": 1,
    "method": "auto",
    "append_newline": false
  },
  "logging": {
    "dir": "logs",
    "level": "INFO"
  },
  "replacement_dict": {
    "enabled": true,
    "path": "config/replacement_dict.json"
  },
  "proper_nouns": {
    "enabled": true,
    "path": "config/proper_nouns.json"
  },
  "ai_correction": {
    "enabled": false,
    "provider": "remote",
    "endpoint": "https://api.deepseek.com/v1/chat/completions",
    "api_key": "",
    "model": "deepseek-chat",
    "timeout_ms": 15000,
    "min_chars": 8,
    "max_tokens": 256,
    "temperature": 0.0,
    "top_p": 0.9,
    "system_prompt": ""
  }
}
```

### 字段说明

#### hotkeys
- `quick` — 快速模式快捷键（默认 F9）
- `long` — AI 润色模式快捷键（默认 Shift+F9）

#### audio
- `sample_rate` — 采样率（FunASR 固定 16000）
- `block_ms` — 音频块大小（毫秒）
- `device` — 输入设备索引（null = 系统默认）
- `max_session_bytes` — 单次录音上限（默认 20MB，约 20 分钟）

#### vad
语音活动检测参数（仅当 `asr.use_vad: true` 时生效）

#### asr
- `use_vad` — 是否启用 VAD（静音检测分段）
- `use_punc` — 是否启用标点恢复
- `language` — 语言
- `hotword` — 额外热词（逗号分隔）
- `batch_size_s` — FunASR 批处理时长
- `device` — 推理设备，空=CPU，`"cuda:0"`=GPU

#### replacement_dict
- `enabled` — 是否启用替换词典
- `path` — 替换词典 JSON 文件路径（相对于项目根目录）

#### proper_nouns
- `enabled` — 是否启用专有名词热词注入
- `path` — 专有名词列表 JSON 文件路径

#### ai_correction
- `enabled` — 是否启用 AI 修正（仅在 Shift+F9 长句模式时生效）
- `endpoint` — OpenAI 兼容 API 端点（默认 DeepSeek）
- `api_key` — **在此填入你的 API Key**
- `model` — 模型名称（如 `deepseek-chat`、`gpt-4o-mini`）
- `timeout_ms` — API 超时（毫秒）
- `min_chars` — 最短触发长度（少于该长度的文本不调用 AI）
- `max_tokens` — 每次 API 调用最大输出 token
- `temperature` — 生成温度（推荐 0.0 保持一致性）
- `system_prompt` — 自定义系统提示词（为空则使用内置默认提示）

#### logging
- `dir` — 日志目录
- `level` — 日志级别（DEBUG / INFO / WARNING / ERROR）

---

## 后处理管道

整个处理流程如下：

```
录音 → ASR 转录 → 替换词典 → 拼音修正 → AI 修正(仅Shift+F9) → 去句末标点 → 文本输入
```

### 1. 替换词典

文件：`config/replacement_dict.json`

精确字符串替换，按词长降序匹配（长词优先），适用于修复 ASR 的已知系统性错误。

```json
{
  "人工智障": "人工智能",
  "神经网落": "神经网络",
  "机器学西": "机器学习",
  "vocal type": "vocotype"
}
```

### 2. 专有名词热词 & 拼音修正

文件：`config/proper_nouns.json`

**双层兜底机制：**

- **第一层（模型级）**：专有名词以 hotword 参数注入 FunASR ContextualParaformer，在解码阶段提高这些词的识别权重
- **第二层（后处理）**：对 ASR 输出做滑动窗口拼音匹配，自动修正同音/近音字。中文采用三阶段匹配：
  1. 带声调拼音精确匹配
  2. 无音调拼音精确匹配
  3. 逐字音近匹配（允许 1 个异音字，但词长 ≥ 3）

  英文词使用 Levenshtein 编辑距离纠错（容错阈值 = 词长 / 3）。

```json
[
  "阿里巴巴",
  "Kubernetes",
  "DeepSeek",
  "Paraformer",
  "vocotype"
]
```

### 3. AI 文本修正

仅 Shift+F9 长句模式启用。默认调用 DeepSeek API，可按需替换为任何 OpenAI 兼容端点。

**内置系统提示词：**
- 最小编辑原则
- 修正同音/近音错词
- 识别并执行口语修正指令（"啊不对"、"改成"、"我的意思是"等）
- 保护技术字符串（英文、缩写、路径、命令、参数）
- 只输出修正后文本，不提供解释说明

**自定义提示词示例**（在 `config.json` 中设置 `ai_correction.system_prompt`）：

```json
{
  "ai_correction": {
    "system_prompt": "你是一个语音转写文本的校对助手。请修正错别字和标点，保持原意，只输出修正后的文本。"
  }
}
```

### 运行时热加载

修改 `config/replacement_dict.json` 或 `config/proper_nouns.json` 后，**无需重启程序**——下次录音转录时会自动重新加载。

---

## 使用示例

### 基础场景

```
按住 F9 → 说"今天天气真好啊" → 松开 F9
→ ASR 转录 → 替换词典检查 → 输出"今天天气真好啊"
```

### 专有名词场景

假设 `proper_nouns.json` 中包含 `"DeepSeek"`：

```
按住 F9 → 说"我觉得的普seek的api很好用" → 松开 F9
→ ASR 转录为"我觉得的 普 seek 的 API 很好用"
→ 拼音修正为"我觉得 DeepSeek 的 API 很好用"
→ 输出"我觉得 DeepSeek 的 API 很好用"
```

### AI 润色场景

需要先配置 `ai_correction.api_key`：

```json
{
  "ai_correction": {
    "enabled": true,
    "api_key": "sk-your-deepseek-api-key",
    "model": "deepseek-chat"
  }
}
```

```
按住 Shift+F9 → 说"我今天啊不对明天要去开会" → 松开 Shift+F9
→ ASR 转录为"我今天啊不对明天要去开会"
→ AI 修正识别"啊不对"为修正指令
→ 输出"我明天要去开会。"
```

### 替换词典场景

```json
{
  "人工智障": "人工智能"
}
```

```
按住 F9 → 说"人工智障在改变世界" → 松开 F9
→ ASR 可能识别为"人工智障在改变世界"（ASR 常见错误）
→ 替换词典修正为"人工智能在改变世界"
→ 输出"人工智能在改变世界"
```

---

## 常见问题

### 首次启动很慢？

首次启动会下载 ASR 模型（~1.4GB），需保持网络畅通。后续启动仅加载模型，约 10-30 秒。

### 如何切换 GPU 加速？

在 `config.json` 中添加：

```json
{
  "asr": {
    "device": "cuda:0"
  }
}
```

需要安装 CUDA 版 PyTorch：

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu124
```

然后运行 `nvidia-smi` 确认 CUDA 可用。

### 如何切换火山引擎云端识别？

```json
{
  "backend": "volcengine",
  "volcengine": {
    "app_key": "your-app-key",
    "access_key": "your-access-key"
  }
}
```

需在[火山引擎控制台](https://console.volcengine.com/speech/app)创建应用获取凭证。

### 托盘图标没出来？

- 首次启动使用 `start_vocotype_debug.bat` 检查日志
- 确保安装了 `pystray` 和 `Pillow` 依赖
- 如果使用 Task Scheduler 自启动，请改用 `nssm` 或手动快捷方式启动，因为 Task Scheduler 在 Session 0 下无法显示托盘图标

### 如何设置开机自启动？

1. 创建 `start_vocotype.bat` 的快捷方式
2. 将快捷方式放入 `shell:startup`（Win+R → `shell:startup`）

### 日志在哪里？

项目根目录下的 `logs/` 文件夹，默认日志级别为 INFO。

---

## 项目结构

```
vocotype-cli/
├── main.py                      # 主入口
├── config.json                  # 用户配置文件（模板，API Key 已置空）
├── start_vocotype.bat           # 静默启动（无窗口）
├── start_vocotype_debug.bat     # 调试启动（有控制台）
├── app/
│   ├── __init__.py              # 模块导出
│   ├── config.py                # 配置加载
│   ├── audio_capture.py         # 音频采集
│   ├── transcribe.py            # 转录工作线程
│   ├── funasr_server.py         # FunASR 模型服务器
│   ├── funasr_config.py         # 模型配置
│   ├── download_models.py       # 模型下载
│   ├── logging_config.py        # 日志配置
│   ├── output.py                # 文本注入（SendInput）
│   ├── hotkeys.py               # 热键管理
│   ├── post_processor.py        # 后处理管道
│   ├── replacement_dict.py      # 替换词典模块
│   ├── proper_nouns.py          # 专有名词 + 拼音纠错
│   └── ai_corrector.py          # AI 修正模块（DeepSeek API）
├── config/
│   ├── replacement_dict.json    # 替换词典数据
│   └── proper_nouns.json        # 专有名词列表
├── plugins/                     # 插件目录
├── files/                       # 资源文件
├── logs/                        # 日志输出
├── requirements.txt             # 依赖清单
├── LICENSE                      # 许可证
└── README.md                    # 本文件
```

---

**注意**：`config.json` 中的 `ai_correction.api_key` 需要你自行填入 API Key。提交到仓库的版本已置空，请勿将包含真实 Key 的配置文件提交到公开仓库。
