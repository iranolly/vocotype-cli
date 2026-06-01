"""专有名词模块"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, List, Optional

from pypinyin import lazy_pinyin, Style

logger = logging.getLogger(__name__)


class ProperNouns:
    def __init__(self, config: Optional[dict] = None):
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.path = str(cfg.get("path", "config/proper_nouns.json"))
        self._words: List[str] = []
        # 中文专有名词拼音条目: (pinyin_tone, pinyin_plain, original_word)
        self._pinyin_entries: List[tuple[str, str, str]] = []
        # 纯英文专有名词: original_word（用于 Levenshtein 距离匹配）
        self._english_words: List[str] = []
        self._loaded = False
        if self.enabled:
            self._load()

    def _load(self) -> None:
        expanded = os.path.expanduser(self.path)
        if not os.path.exists(expanded):
            logger.warning("专有名词文件不存在: %s, 跳过", expanded)
            self.enabled = False
            return
        try:
            with open(expanded, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                logger.error("专有名词格式错误")
                self.enabled = False
                return
            self._words = [str(w).strip() for w in data if str(w).strip()]
            self._build_entries()
            self._loaded = True
            logger.info("专有名词已加载, 共 %d 个词", len(self._words))
        except Exception as exc:
            logger.error("加载专有名词失败: %s", exc)
            self.enabled = False

    def _build_entries(self) -> None:
        """为所有专有名词预计算拼音/拼写表示。"""
        self._pinyin_entries = []
        self._english_words = []
        for word in self._words:
            if not word:
                continue
            # 纯英文/数字词：保存原词用于拼写纠错
            if re.fullmatch(r"[a-zA-Z0-9_\-.+]+", word):
                self._english_words.append(word)
                continue
            # 中文词：计算拼音
            py_tone = "".join(lazy_pinyin(word, style=Style.TONE3))
            py_plain = "".join(lazy_pinyin(word, style=Style.NORMAL))
            self._pinyin_entries.append((py_tone, py_plain, word))
        # 长词优先匹配
        self._pinyin_entries.sort(key=lambda x: -len(x[0]))
        self._english_words.sort(key=lambda x: -len(x))

    def reload(self) -> None:
        """重新加载专有名词文件"""
        logger.info("热加载专有名词...")
        self._load()

    def get_hotword(self) -> str:
        if not self.enabled or not self._loaded:
            return ""
        return ",".join(self._words)

    def get_words(self) -> List[str]:
        return list(self._words) if self._loaded else []

    def phonetic_correct(self, text: str) -> str:
        """对 ASR 输出做滑动窗口拼音匹配（中文）+ 拼写纠错（英文）。

        中文：滑动窗口拼音匹配，自动修正同音/近音字。
        英文：Levenshtein 距离容错修正拼写错误。
        """
        if not text or not self._loaded:
            return text

        chars = list(text)

        # --- 中文拼音匹配 ---
        if self._pinyin_entries:
            # 找出所有汉字位置
            han_positions = [i for i, c in enumerate(chars)
                             if '\u4e00' <= c <= '\u9fff']
            if han_positions:
                corrected_ranges: list[range] = []

                for py_tone, py_plain, pn_word in self._pinyin_entries:
                    pn_len = len(pn_word)
                    for start_pos in han_positions:
                        # 已被修正跳过
                        if any(start_pos in r for r in corrected_ranges):
                            continue
                        end_pos = start_pos + pn_len - 1
                        if end_pos >= len(chars):
                            continue
                        segment_positions = list(range(start_pos, end_pos + 1))
                        if not all(p in han_positions for p in segment_positions):
                            continue

                        segment = "".join(chars[start_pos:end_pos + 1])

                        # a) 精确拼音匹配（带声调）
                        seg_py_tone = "".join(lazy_pinyin(segment, style=Style.TONE3))
                        if seg_py_tone == py_tone:
                            if segment != pn_word:
                                logger.info(
                                    "拼音精确修正: '%s'(%s) -> '%s'",
                                    segment, seg_py_tone, pn_word,
                                )
                                for i, c in enumerate(pn_word):
                                    chars[start_pos + i] = c
                                corrected_ranges.append(range(start_pos, end_pos + 1))
                            continue  # 继续检查下一个位置

                        # b) 无音调精确匹配
                        seg_py_plain = "".join(lazy_pinyin(segment, style=Style.NORMAL))
                        if seg_py_plain == py_plain:
                            if segment != pn_word:
                                logger.info(
                                    "拼音(无调)修正: '%s'(%s) -> '%s'",
                                    segment, seg_py_plain, pn_word,
                                )
                                for i, c in enumerate(pn_word):
                                    chars[start_pos + i] = c
                                corrected_ranges.append(range(start_pos, end_pos + 1))
                            continue

                        # c) 逐字音近匹配
                        if len(segment) >= 2:
                            diffs = 0
                            for sc, pc in zip(segment, pn_word):
                                if sc == pc:
                                    continue
                                s_py = lazy_pinyin(sc, style=Style.NORMAL)[0]
                                p_py = lazy_pinyin(pc, style=Style.NORMAL)[0]
                                if s_py == p_py:
                                    continue
                                diffs += 1
                            # 同音不同字：diffs==0
                            # 或 >=3 字词允许 1 个异音字
                            if (diffs == 0 or (diffs == 1 and len(segment) >= 3)):
                                if segment != pn_word:
                                    logger.info(
                                        "拼音%s修正: '%s' -> '%s'",
                                        "近似" if diffs else "同音",
                                        segment, pn_word,
                                    )
                                    for i, c in enumerate(pn_word):
                                        chars[start_pos + i] = c
                                    corrected_ranges.append(range(start_pos, end_pos + 1))
                                continue

        # --- 英文拼写纠错 ---
        if self._english_words:
            # 找出所有英文单词位置
            for em in re.finditer(r"[a-zA-Z_+]+", "".join(chars)):
                start, end = em.start(), em.end()
                token = em.group()
                if not token:
                    continue

                # 先检查精确匹配（大小写敏感）
                token_lower = token.lower()
                for en_word in self._english_words:
                    if token == en_word or token_lower == en_word.lower():
                        break  # 精确匹配，不修正
                else:
                    # 没有精确匹配，尝试 Levenshtein 纠错
                    best_word = None
                    best_dist = 999
                    for en_word in self._english_words:
                        # 长度差异超过一半则跳过
                        if abs(len(token) - len(en_word)) > max(2, len(en_word) // 2):
                            continue
                        dist = self._levenshtein(token_lower, en_word.lower())
                        if dist < best_dist:
                            best_dist = dist
                            best_word = en_word

                    # 允许的容错阈值：每 3 个字符最多 1 个差异
                    max_dist = max(1, len(best_word) // 3) if best_word else 999
                    if best_word and 0 < best_dist <= max_dist and best_word != token:
                        logger.info(
                            "英文拼写修正: '%s' -> '%s' (dist=%d)",
                            token, best_word, best_dist,
                        )
                        # 保持原大小写风格
                        if token[0].isupper():
                            chars[start:end] = list(best_word[0].upper() + best_word[1:])
                        else:
                            chars[start:end] = list(best_word)

        return "".join(chars)

    @staticmethod
    def _levenshtein(a: str, b: str) -> int:
        """计算两个字符串的 Levenshtein 编辑距离。"""
        if len(a) < len(b):
            a, b = b, a
        if not b:
            return len(a)
        prev_row = list(range(len(b) + 1))
        for i, ca in enumerate(a):
            curr_row = [i + 1]
            for j, cb in enumerate(b):
                cost = 0 if ca == cb else 1
                curr_row.append(
                    min(
                        curr_row[j] + 1,
                        prev_row[j + 1] + 1,
                        prev_row[j] + cost,
                    )
                )
            prev_row = curr_row
        return prev_row[-1]
