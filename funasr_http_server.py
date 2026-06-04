#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FunASR 常驻 HTTP 服务 — 独立模式（VocoType 未运行时使用）。

如果 VocoType 已在运行，它会自动在 http://127.0.0.1:8765 提供同样的端点，
无需单独启动此脚本。仅在 VocoType 未运行且需要 Hermes 语音识别时使用。

启动:
    python funasr_http_server.py [--port 8765] [--host 127.0.0.1] [--config config.json]

端点（与 VocoType 内嵌端点完全一致）:
    GET  /health          → {"status": "ok", ...}
    POST /transcribe       → {"text": "...", "elapsed": 1.2}
"""

import argparse
import logging
import os
import signal
import sys
import warnings

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

warnings.filterwarnings("ignore", category=UserWarning, module="jieba._compat")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("funasr_http")


def _load_post_processor(config_path: str | None):
    """Try to load PostProcessor from config. Returns None on failure."""
    if not config_path:
        config_path = os.path.join(_SCRIPT_DIR, "config.json")
    if not os.path.exists(config_path):
        logger.info("配置文件 %s 不存在，跳过后处理", config_path)
        return None

    try:
        from app.config import load_config
        from app.post_processor import PostProcessor
        config = load_config(config_path)
        pp = PostProcessor(config)
        logger.info("后处理管道已加载（替换词典=%s, 专有名词=%s, AI修正=%s）",
                     pp.replacement_dict.enabled,
                     pp.proper_nouns.enabled,
                     pp.ai_corrector.enabled)
        return pp
    except Exception as e:
        logger.warning("加载后处理管道失败: %s，将以裸 ASR 模式运行", e)
        return None


def main():
    parser = argparse.ArgumentParser(description="FunASR HTTP STT Server (standalone)")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument("--config", help="VocoType 配置文件路径（默认 config.json）")
    args = parser.parse_args()

    # 从配置文件读取 device（如果未通过环境变量设置）
    config_path = args.config or os.path.join(_SCRIPT_DIR, "config.json")
    if os.path.exists(config_path) and "FUNASR_DEVICE" not in os.environ:
        try:
            import json
            with open(config_path) as f:
                cfg = json.load(f)
            device = cfg.get("asr", {}).get("device", "")
            if device and "cuda" in device.lower():
                os.environ["FUNASR_DEVICE"] = device
                logger.info("从配置读取设备: %s", device)
        except Exception:
            pass

    # 设置默认值（仅在未通过环境变量或配置设置时生效）
    os.environ.setdefault("OMP_NUM_THREADS", "8")
    os.environ.setdefault("FUNASR_DEVICE", "cpu")
    os.environ.setdefault("FUNASR_USE_VAD", "false")
    os.environ.setdefault("FUNASR_USE_PUNC", "true")

    # 延迟导入：等 env 设置完毕再加载 FunASRServer（其模块级 setdefault 不会覆盖已设值）
    from app.funasr_server import FunASRServer
    from app.funasr_http import FunASRHttpThread

    logger.info("正在初始化 FunASR 模型（仅此一次）...")
    asr = FunASRServer()
    result = asr.initialize()
    if not result.get("success"):
        logger.error("模型初始化失败: %s", result)
        sys.exit(1)
    logger.info("模型就绪: %s", result.get("message", "ok"))

    # 加载后处理管道（替换词典 + 专有名词 + AI 修正）
    pp = _load_post_processor(args.config)
    ai_correction = False
    if pp is not None:
        # 优先读取 http_server.ai_correction，回退到全局 ai_correction.enabled
        http_cfg = {}
        try:
            from app.config import load_config
            _cfg_path = args.config or os.path.join(_SCRIPT_DIR, "config.json")
            cfg = load_config(_cfg_path)
            http_cfg = cfg.get("asr", {}).get("http_server", {})
        except Exception:
            pass
        ai_correction = http_cfg.get("ai_correction", pp.ai_corrector.enabled)

    http = FunASRHttpThread(asr, host=args.host, port=args.port,
                            post_processor=pp, ai_correction=ai_correction)
    http.start()

    def _shutdown(sig, frame):
        logger.info("收到信号 %s，正在关闭...", sig)
        http.stop()
        asr.cleanup()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info("FunASR HTTP 服务运行中，按 Ctrl+C 退出")
    try:
        signal.pause()
    except AttributeError:
        import time
        while http.is_running:
            time.sleep(1)


if __name__ == "__main__":
    main()
