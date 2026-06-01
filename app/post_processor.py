"""后处理管道：替换词典 -> 拼音修正 -> AI 修正"""

from __future__ import annotations
import logging
from typing import Dict, Optional
from .replacement_dict import ReplacementDict
from .proper_nouns import ProperNouns
from .ai_corrector import AICorrector

logger = logging.getLogger(__name__)

class PostProcessor:
    def __init__(self, config: Optional[Dict] = None):
        cfg = config or {}
        self.replacement_dict = ReplacementDict(cfg.get("replacement_dict", {}))
        if self.replacement_dict.enabled:
            logger.info("替换词典: 已启用 (%d 条)", len(self.replacement_dict._replacements))
        else:
            logger.info("替换词典: 未启用")
        self.proper_nouns = ProperNouns(cfg.get("proper_nouns", {}))
        if self.proper_nouns.enabled:
            logger.info("专有名词: 已启用 (%d 个词)", len(self.proper_nouns.get_words()))
        else:
            logger.info("专有名词: 未启用")
        self.ai_corrector = AICorrector(cfg.get("ai_correction", {}))
        if self.ai_corrector.enabled:
            logger.info("AI 修正: 已启用 (endpoint=%s, model=%s)", self.ai_corrector.endpoint, self.ai_corrector.model)
        else:
            logger.info("AI 修正: 未启用")

    def reload(self) -> None:
        """热加载替换词典和专有名词（每次转录前调用）"""
        self.replacement_dict.reload()
        self.proper_nouns.reload()

    def get_hotword(self) -> str:
        return self.proper_nouns.get_hotword()

    def process(self, text: str, long_mode: bool = False) -> str:
        """对 ASR 识别结果执行后处理。

        Args:
            text: ASR 识别文本
            long_mode: 是否启用 AI 修正（F9=快速模式不启用，Shift+F9=长句模式启用）
        """
        if not text:
            return text
        # Stage 1: 替换词典（始终执行）
        text = self.replacement_dict.process(text)
        # Stage 2: 拼音修正（专有名词同音/近音匹配，始终执行）
        if self.proper_nouns.enabled:
            corrected = self.proper_nouns.phonetic_correct(text)
            if corrected != text:
                logger.info("拼音修正专有名词: '%s' -> '%s'", text[:80], corrected[:80])
                text = corrected
        # Stage 3: AI 修正（仅长句模式启用）
        if long_mode and self.ai_corrector.should_correct(text):
            polished, metrics = self.ai_corrector.correct(text)
            if metrics.applied:
                logger.info("AI 修正: 已修正 (耗时 %.0fms)", metrics.latency_ms)
            text = polished
        # Stage 4: 去除句末标点
        text = self._strip_trailing_punctuation(text)
        return text

    @staticmethod
    def _strip_trailing_punctuation(text: str) -> str:
        """去除文本末尾的标点符号（。！？!?，,；;等）。"""
        if not text:
            return text
        return text.rstrip("。！!，,；;：:、…～~")
