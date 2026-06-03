# Deployment Patches

记录了针对依赖包手动修复的 bug，重装环境后需重新应用。

## 1. funasr_onnx — Hotword 输入长度硬编码

**文件**: `.venv/Lib/site-packages/funasr_onnx/paraformer_bin.py`  
**方法**: `ContextualParaformer.proc_hotword()`

- 原代码 `pad_list(..., max_len=10)` 硬编码 10 字符
- ONNX 模型 `model_eb.onnx` 在导出时固定了第二维 10
- 英文 hotword 如 "DeepSeek-R1"（11 字符）导致 `could not broadcast input array from shape (11,) into shape (10,)`
- 修复：重新导出 ONNX 模型，将 `hotword` 输入第二维设为动态轴 `max_len`

**重新导出步骤**：
```python
from funasr import AutoModel
model = AutoModel(model='iic/speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404', device='cpu', disable_update=True)
# 需先 patch export_meta.py 增加 hotword 第 2 维 dynamic_axes
```

## 2. funasr_onnx — Hotword OOV 日志刷屏

**文件**: `.venv/Lib/site-packages/funasr_onnx/paraformer_bin.py`  
**方法**: `ContextualParaformer.proc_hotword()`

- 中文 vocab 不含大写英文字母，每个英文 hotword 每个字母打一条 warning
- 修复：预处理时 `w.lower()` 转小写 + 过滤不在 vocab 中的字符

## 3. funasr_onnx — pad_list 动态 max_len 崩溃

**文件**: `.venv/Lib/site-packages/funasr_onnx/utils/utils.py`  
**方法**: `pad_list()`

- `x.size(0)` 是 PyTorch 语法，numpy array 的 `.size` 是属性（int），调用 `.size(0)` 报 `'int' object is not callable`
- 修复：改为 `x.shape[0]`

## 4. funasr — cif_predictor torch._check JIT 不兼容

**文件**: `.venv/Lib/site-packages/funasr/models/paraformer/cif_predictor.py`

- `torch._check(frames.shape[0] != 0)` 在 TorchScript tracing 时报 `TypeError: cond must be a bool`
- 修复：改为 `assert frames.shape[0] != 0`

## 5. ONNX 导出 — model_quant.onnx 类型不匹配

**问题**: PyTorch `torch.export` 路径导出的 `model.onnx` 中 `Less` Op 存在 float/int64 类型不匹配，量化后无法加载  
**修复**: 使用 `torch.onnx.export(..., dynamo=False)` 强制 legacy 导出路径，分别重新导出 backbone + embedder，再重新量化

## 6. AI 修正提示词

**文件**: `config.json`（gitignored，含 API key）

- 切换提供商为 MiniMax CN（MiniMax-M3）
- 增加自我纠正语义理解、结构化输出等规则

## 关键模型文件

ONNX 模型缓存位置：
```
~/.cache/modelscope/hub/models/iic/speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404/
```

重新导出的文件（动态 hotword 轴）：
- `model_eb.onnx` — hotword 输入 shape `['num_hotwords', 'max_len']`
- `model_eb_quant.onnx`
- `model.onnx` — 重新用 legacy exporter 导出
- `model_quant.onnx`
