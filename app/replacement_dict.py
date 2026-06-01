"""替换词典模块"""
from __future__ import annotations
import json, logging, os
from typing import Dict, Optional
logger = logging.getLogger(__name__)

class ReplacementDict:
    def __init__(self, config: Optional[Dict] = None):
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.path = str(cfg.get("path", "config/replacement_dict.json"))
        self._replacements: Dict[str, str] = {}
        self._loaded = False
        if self.enabled:
            self._load()

    def _load(self) -> None:
        expanded = os.path.expanduser(self.path)
        if not os.path.exists(expanded):
            logger.warning("替换词典文件不存在: %s, 跳过", expanded)
            self.enabled = False
            return
        try:
            with open(expanded, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.error("替换词典格式错误")
                self.enabled = False
                return
            self._replacements = dict(sorted(data.items(), key=lambda x: -len(x[0])))
            self._loaded = True
            logger.info("替换词典已加载, 共 %d 条规则", len(self._replacements))
        except Exception as exc:
            logger.error("加载替换词典失败: %s", exc)
            self.enabled = False

    def reload(self) -> None:
        """重新加载替换词典文件"""
        logger.info("热加载替换词典...")
        self._load()

    def process(self, text: str) -> str:
        if not self.enabled or not self._loaded or not text:
            return text
        result = text
        for old, new in self._replacements.items():
            if old in result:
                result = result.replace(old, new)
        return result
