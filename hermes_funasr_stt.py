#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hermes STT command provider — FunASR HTTP 客户端。

连接到 VocoType 内嵌的 FunASR HTTP 端点（或独立的 funasr_http_server.py），
避免每次转录都重新加载模型。

优先连接 VocoType 已加载的模型（共用 GPU 显存），
如果 VocoType 未运行则自动启动独立服务。

Usage:
  hermes_funasr_stt.py --input <audio> --output <transcript.txt>
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error

# ── Constants ──────────────────────────────────────────────────────────
HTTP_HOST = os.environ.get("FUNASR_HTTP_HOST", "127.0.0.1")
HTTP_PORT = int(os.environ.get("FUNASR_HTTP_PORT", "8765"))
HTTP_BASE = f"http://{HTTP_HOST}:{HTTP_PORT}"

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_SCRIPT = os.path.join(_SCRIPT_DIR, "funasr_http_server.py")
PYTHON_EXE = sys.executable

# ── FFmpeg helpers ─────────────────────────────────────────────────────


def _find_ffmpeg() -> str:
    candidates = [os.path.join(_SCRIPT_DIR, "ffmpeg.exe"), "ffmpeg"]
    for c in candidates:
        try:
            subprocess.run([c, "-version"], capture_output=True, timeout=5)
            return c
        except Exception:
            continue
    return "ffmpeg"


def _convert_to_wav(input_path: str) -> str:
    ext = os.path.splitext(input_path)[1].lower()
    if ext == ".wav":
        return input_path
    ffmpeg = _find_ffmpeg()
    fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="hermes_funasr_")
    os.close(fd)
    cmd = [
        ffmpeg, "-y", "-i", input_path,
        "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
        "-loglevel", "error", tmp_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"ERROR: ffmpeg conversion failed: {result.stderr}", file=sys.stderr)
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        sys.exit(5)
    return tmp_path


# ── HTTP client ────────────────────────────────────────────────────────


def _http_request(method: str, path: str, body: dict | None = None, timeout: float = 15) -> dict:
    """Send JSON request to the HTTP server."""
    url = f"{HTTP_BASE}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"success": False, "error": f"HTTP {e.code}: {raw[:200]}"}
    except urllib.error.URLError as e:
        return {"success": False, "error": f"连接失败: {e.reason}"}


def _is_server_running() -> bool:
    try:
        resp = _http_request("GET", "/health", timeout=2)
        return resp.get("status") == "ok"
    except Exception:
        return False


def _ensure_server() -> bool:
    """Ensure the FunASR HTTP server is running. Returns True if ready."""
    if _is_server_running():
        return True

    # VocoType 未运行 → 启动独立服务
    print("# VocoType 未检测到，正在启动独立 FunASR HTTP 服务...", file=sys.stderr)
    proc = subprocess.Popen(
        [PYTHON_EXE, SERVER_SCRIPT, "--host", HTTP_HOST, "--port", str(HTTP_PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )

    # 等待服务就绪（最多 45 秒，因为首次加载模型需要 ~8s）
    deadline = time.time() + 45
    while time.time() < deadline:
        time.sleep(0.5)
        if proc.poll() is not None:
            print(f"# 独立服务启动失败 (exit {proc.returncode})", file=sys.stderr)
            return False
        if _is_server_running():
            print("# FunASR HTTP 服务已就绪", file=sys.stderr)
            return True

    print("# 服务启动超时", file=sys.stderr)
    return False


# ── Main ───────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="FunASR STT for Hermes")
    parser.add_argument("--input", required=True, help="Input audio file")
    parser.add_argument("--output", required=True, help="Output transcript file (txt)")
    parser.add_argument("--language", default="zh", help="Language code")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"ERROR: audio file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    if not _ensure_server():
        print("ERROR: 无法连接 FunASR HTTP 服务", file=sys.stderr)
        sys.exit(2)

    # 转换非 WAV 格式（Hermes Desktop 录音为 .webm）
    wav_path = _convert_to_wav(args.input)
    is_temp = wav_path != args.input

    try:
        result = _http_request("POST", "/transcribe", {
            "audio_path": os.path.abspath(wav_path),
            "language": args.language,
        }, timeout=120)

        if not result.get("success"):
            print(f"ERROR: transcription failed: {result.get('error')}", file=sys.stderr)
            sys.exit(3)

        text = result.get("text", "").strip()
        elapsed = result.get("elapsed", 0)

        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)

        print(text)
        print(f"# Transcribed in {elapsed:.1f}s, {len(text)} chars", file=sys.stderr)

    finally:
        if is_temp and os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except OSError:
                pass


if __name__ == "__main__":
    main()
