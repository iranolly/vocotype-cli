#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可嵌入的 FunASR HTTP 服务组件 — 零依赖（仅用 stdlib）。

可在 VocoType 的 TranscriptionWorker 中内嵌（复用已加载模型），
也可被 funasr_http_server.py 独立启动。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _resolve_port(port: Optional[int] = None) -> int:
    """返回端口号，优先级：参数 > 环境变量 > 默认值。"""
    if port is not None:
        return port
    env = os.environ.get("FUNASR_HTTP_PORT", "")
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    return DEFAULT_PORT


def _resolve_host(host: Optional[str] = None) -> str:
    if host is not None:
        return host
    return os.environ.get("FUNASR_HTTP_HOST", DEFAULT_HOST)


class FunASRHttpThread:
    """在 daemon 线程中运行 HTTP 服务，包装已有的 FunASRServer。

    用法::

        from app.funasr_server import FunASRServer
        from app.funasr_http import FunASRHttpThread

        server = FunASRServer()
        server.initialize()
        http = FunASRHttpThread(server, port=8765)
        http.start()
        try:
            # ... 主程序运行 ...
        finally:
            http.stop()
            server.cleanup()
    """

    def __init__(
        self,
        funasr_server,          # FunASRServer 实例
        host: Optional[str] = None,
        port: Optional[int] = None,
        post_processor=None,    # PostProcessor 实例（可选）
        ai_correction: bool = False,  # HTTP 转录是否启用 AI 修正
    ):
        self._asr = funasr_server
        self._host = _resolve_host(host)
        self._port = _resolve_port(port)
        self._httpd: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._request_count = 0
        self._start_time: float = 0.0
        self._post_processor = post_processor
        self._ai_correction = ai_correction

        # 把实例引用存为类属性，让 _RequestHandler 能访问
        # （http.server 为每个请求创建新的 handler 实例，所以需要全局/类级引用）
        _RequestHandler._server_ref = self

    # ── 公共接口 ──────────────────────────────────────────────────────

    def start(self) -> None:
        """启动 HTTP 服务（非阻塞，在 daemon 线程中运行）。"""
        if self._running.is_set():
            logger.warning("HTTP 服务已在运行")
            return

        self._httpd = HTTPServer((self._host, self._port), _RequestHandler)
        self._thread = threading.Thread(
            target=self._serve_forever,
            name="funasr-http",
            daemon=True,
        )
        self._running.set()
        self._start_time = time.time()
        self._thread.start()
        logger.info("FunASR HTTP 端点已启动 → http://%s:%d", self._host, self._port)

    def stop(self) -> None:
        """停止 HTTP 服务。"""
        if not self._running.is_set():
            return
        self._running.clear()
        if self._httpd:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            self._httpd = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None
        logger.info("FunASR HTTP 端点已停止，共处理 %d 个请求", self._request_count)

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    @property
    def address(self) -> str:
        return f"http://{self._host}:{self._port}"

    def set_post_processor(self, pp) -> None:
        """注入 PostProcessor（由 TranscriptionWorker 在 _post_processor 就绪后调用）。"""
        self._post_processor = pp

    # ── 内部 ──────────────────────────────────────────────────────────

    def _serve_forever(self) -> None:
        """在线程中运行 serve_forever。"""
        try:
            self._httpd.serve_forever(poll_interval=0.5)
        except Exception:
            if self._running.is_set():
                logger.error("HTTP 服务异常退出:\n%s", traceback.format_exc())

    def _handle_transcribe(self, audio_path: str, language: str = "zh", hotword: str = "") -> dict:
        """执行一次转录，返回结果字典。"""
        self._request_count += 1
        import sys as _sys
        _sys.stderr.write("[funasr-http-DEBUG] _handle_transcribe called, request_count=%d, audio_path=%s\n" % (self._request_count, audio_path))
        _sys.stderr.flush()

        if not os.path.exists(audio_path):
            logger.warning("HTTP 转录 [#%d]: 音频文件不存在 → %s", self._request_count, audio_path)
            return {"success": False, "error": f"音频文件不存在: {audio_path}"}

        t0 = time.time()
        options = {"language": language}
        if hotword:
            options["hotword"] = hotword

        # 注入 PostProcessor 的专有名词热词（与 VocoType 自身转录路径一致）
        if self._post_processor is not None:
            try:
                pn_hotword = self._post_processor.get_hotword()
            except Exception:
                pn_hotword = ""
            if pn_hotword:
                existing = (options.get("hotword") or "").strip()
                options["hotword"] = existing + "," + pn_hotword if existing else pn_hotword

        try:
            result = self._asr.transcribe_audio(audio_path, options=options)
        except Exception:
            logger.error("HTTP 转录 [#%d]: ASR 异常\n%s", self._request_count, traceback.format_exc())
            return {
                "success": False,
                "error": traceback.format_exc(),
                "elapsed": round(time.time() - t0, 2),
            }

        elapsed = round(time.time() - t0, 2)

        if not result.get("success"):
            logger.warning("HTTP 转录 [#%d]: ASR 失败 → %s", self._request_count, result.get("error", "未知错误"))
            return {
                "success": False,
                "error": result.get("error", "未知错误"),
                "elapsed": elapsed,
            }

        text = result.get("text", "")
        logger.info("转录完成 [#%d]: %.2fs → \"%s\"", self._request_count, elapsed, text[:50])

        # 后处理：替换词典 + 专有名词（始终），AI 修正（仅当 ai_correction=true）
        if self._post_processor is not None and text:
            try:
                logger.info("后处理前原始文本: \"%s\"", text[:100])
                corrected = self._post_processor.process(text, long_mode=self._ai_correction)
                if corrected and corrected != text:
                    tag = "AI修正" if self._ai_correction else "后处理"
                    logger.info("%s [#%d]: \"%s\" → \"%s\"", tag, self._request_count, text[:50], corrected[:50])
                    text = corrected
                else:
                    logger.info("后处理无变更: \"%s\" (replacement_dict_enabled=%s, proper_nouns_enabled=%s, ai_correction=%s)",
                                 text[:50],
                                 self._post_processor.replacement_dict.enabled if hasattr(self._post_processor, 'replacement_dict') else '?',
                                 self._post_processor.proper_nouns.enabled if hasattr(self._post_processor, 'proper_nouns') else '?',
                                 self._ai_correction)
            except Exception as exc:
                logger.warning("后处理失败 [#%d]: %s，使用原始文本", self._request_count, exc)

        return {"success": True, "text": text, "elapsed": elapsed}


class _RequestHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器（每个请求一个实例）。"""

    # 类级引用，由 FunASRHttpThread.__init__ 设置
    _server_ref: Optional[FunASRHttpThread] = None

    def log_message(self, fmt, *args):
        logger.debug("HTTP %s", fmt % args)

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            ref = self._server_ref
            self._send_json({
                "status": "ok" if (ref and ref.is_running) else "stopped",
                "models_loaded": True,
                "uptime_seconds": round(time.time() - ref._start_time, 1) if ref and ref._start_time else 0,
                "request_count": ref._request_count if ref else 0,
                "device": os.environ.get("FUNASR_DEVICE", "cpu"),
                "post_processor": ref._post_processor is not None if ref else False,
                "ai_correction": ref._ai_correction if ref else False,
            })
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/transcribe":
            self._send_json({"error": "not found"}, 404)
            return

        ref = self._server_ref
        if not ref or not ref.is_running:
            self._send_json({"success": False, "error": "服务未就绪"}, 503)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length) if content_length > 0 else b"{}"

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self._send_json({"success": False, "error": "请求体不是合法 JSON"}, 400)
            return

        audio_path = payload.get("audio_path", "")
        language = payload.get("language", "zh")
        hotword = payload.get("hotword", "")

        if not audio_path:
            self._send_json({"success": False, "error": "缺少 audio_path 参数"}, 400)
            return

        result = ref._handle_transcribe(audio_path, language, hotword)
        status = 200 if result.get("success") else 500
        self._send_json(result, status)
