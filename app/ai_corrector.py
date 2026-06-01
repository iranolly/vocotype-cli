"""Remote AI 文本修正模块"""
from __future__ import annotations
import json, logging, time, urllib.error, urllib.parse, urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = (
    "你是中文语音转写文本的后处理器。\n\n"
    "目标：在不改变原意、不新增事实的前提下，做最小必要修正，让文本通顺、自然、易读。\n\n"
    "仅允许：\n"
    "1. 补充/修改/删除标点\n"
    "2. 调整断句与分句\n"
    "3. 删除明显口头禅、重复词、无意义语气词\n"
    "4. 修正明显同音/近音错词、漏字、多字\n"
    "5. 原句明显不通顺时，做最小限度顺句\n\n"
    "核心约束：\n"
    "- 最小编辑：能不改就不改，能少改就少改\n"
    "- 含义守恒：不新增事实、细节、观点、结论\n"
    "- 技术字符串保真：英文、缩写、模型名、版本号、路径、命令、参数按原样保留\n"
    "- 数字规范：默认保留阿拉伯数字\n"
    "- 不确定时保留原样，避免误改\n\n"
    "输出要求：只输出最终文本，不要任何说明。"
)

@dataclass
class CorrectionMetrics:
    used: bool
    applied: bool
    latency_ms: float
    reason: str

class AICorrector:
    def __init__(self, config: Optional[Dict] = None):
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.endpoint = str(cfg.get("endpoint", "https://api.deepseek.com/v1/chat/completions"))
        self.api_key = str(cfg.get("api_key", "")).strip()
        self.model = str(cfg.get("model", "deepseek-chat"))
        self.timeout_ms = max(200, int(cfg.get("timeout_ms", 15000)))
        self.min_chars = max(1, int(cfg.get("min_chars", 8)))
        self.max_tokens = max(1, int(cfg.get("max_tokens", 256)))
        self.temperature = float(cfg.get("temperature", 0.0))
        self.top_p = float(cfg.get("top_p", 0.9))
        user_prompt = cfg.get("system_prompt")
        self.system_prompt = str(user_prompt).strip() if user_prompt else DEFAULT_SYSTEM_PROMPT

    def should_correct(self, text: str) -> bool:
        if not self.enabled or not text or not text.strip():
            return False
        return len(text.strip()) >= self.min_chars

    def correct(self, text: str) -> Tuple[str, CorrectionMetrics]:
        start = time.perf_counter()
        original = text or ""
        if not self.enabled:
            return original, CorrectionMetrics(False, False, 0.0, "disabled")
        stripped = original.strip()
        if len(stripped) < self.min_chars:
            return original, CorrectionMetrics(False, False, 0.0, "too_short")
        try:
            result = self._call_api(stripped)
            polished = result.strip()
            if not polished:
                return original, CorrectionMetrics(True, False, (time.perf_counter() - start) * 1000, "empty_response")
            return polished, CorrectionMetrics(used=True, applied=(polished != original),
                latency_ms=(time.perf_counter() - start) * 1000.0, reason="ok")
        except TimeoutError:
            return original, CorrectionMetrics(True, False, (time.perf_counter() - start) * 1000, "timeout")
        except Exception as exc:
            logger.warning("AI 修正失败: %s", exc)
            return original, CorrectionMetrics(True, False, (time.perf_counter() - start) * 1000, "exception")

    def _call_api(self, text: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": f"原文：{text}\n输出："},
            ],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "stream": False,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        request = urllib.request.Request(self.endpoint, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=max(0.05, self.timeout_ms / 1000.0)) as resp:
            body = resp.read().decode("utf-8")
        parsed = json.loads(body)
        choices = parsed.get("choices")
        if isinstance(choices, list) and choices:
            msg = (choices[0] or {}).get("message", {})
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                return content
        raise ValueError("API 返回内容为空")
