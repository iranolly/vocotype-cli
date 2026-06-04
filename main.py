"""Command-line entry for the speak-keyboard prototype."""

from __future__ import annotations

import argparse
import logging
import os
import time

from pynput import keyboard as pynput_kb

from app import TranscriptionResult, TranscriptionWorker, load_config, type_text
from app.post_processor import PostProcessor
from app.logging_config import setup_logging

import pystray
from PIL import Image, ImageDraw


logger = logging.getLogger(__name__)

# 启动保护期（秒）：忽略此时间段内的所有 F9 按键，防止启动误触
_STARTUP_GUARD_SECONDS = 2.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Speak Keyboard prototype")
    parser.add_argument("--config", help="Path to config JSON")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single transcription cycle for debugging",
    )
    parser.add_argument("--save-dataset", action="store_true", help="Persist audio/text pairs")
    parser.add_argument("--dataset-dir", default="dataset", help="Dataset output directory")
    return parser.parse_args()


def _cleanup_and_exit(worker):
    """统一清理资源并退出。"""
    try:
        worker.stop()
    except Exception:
        pass
    try:
        worker.cleanup()
    except Exception:
        pass
    logger.info("所有资源已清理，正常退出")
    import sys
    sys.exit(0)


def _build_tray_icon():
    """创建系统托盘图标。"""
    img = Image.new("RGB", (16, 16), (0, 120, 212))
    draw = ImageDraw.Draw(img)
    draw.ellipse([1, 1, 14, 14], fill="white")
    draw.polygon([(5, 5), (11, 5), (8, 11)], fill=(0, 120, 212))
    return img


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    
    # 配置日志系统（统一配置）
    from app.config import ensure_logging_dir
    log_dir_abs = ensure_logging_dir(config)
    setup_logging(
        level=config["logging"].get("level", "INFO"),
        log_dir=log_dir_abs
    )

    output_cfg = config.get("output", {})
    output_method = output_cfg.get("method", "auto")
    append_newline = output_cfg.get("append_newline", False)

    # GPU 设备设置（如果配置了 device）
    asr_cfg = config.get("asr", {})
    device = asr_cfg.get("device", "")
    if device:
        os.environ["FUNASR_DEVICE"] = device
        logger.info("设置 FunASR 设备为: %s", device)

    # 将 pip 安装的 nvidia CUDA/cuDNN DLL 路径加入 PATH
    if "cuda" in device.lower() or "gpu" in device.lower():
        try:
            import onnxruntime, site
            for p in site.getsitepackages():
                if 'vocotype' in p:
                    sp = p; break
            else:
                sp = site.getsitepackages()[0]
            for sub in ['cublas', 'cuda_runtime', 'cudnn', 'cufft', 'curand', 'cuda_nvrtc', 'nvjitlink']:
                bin_dir = os.path.join(sp, 'nvidia', sub.replace('_', ''), 'bin')
                if os.path.exists(bin_dir):
                    os.environ['PATH'] = bin_dir + os.pathsep + os.environ['PATH']
            onnxruntime.preload_dlls()
            logger.info("ONNX Runtime CUDA DLL 预加载完成")
        except Exception as e:
            logger.warning("ONNX Runtime CUDA 预加载失败: %s", e)

    # 初始化后处理管道（替换词典 + 专有名词 + AI 修正）
    post_processor = PostProcessor(config)
    hotword = post_processor.get_hotword()
    if hotword:
        asr_cfg = config.setdefault("asr", {})
        existing = (asr_cfg.get("hotword") or "").strip()
        if existing:
            asr_cfg["hotword"] = existing + "," + hotword
        else:
            asr_cfg["hotword"] = hotword
        logger.info("已注入 %d 个专有名词到 ASR hotword", len(post_processor.proper_nouns.get_words()))

    # 先创建worker（没有回调）
    worker = TranscriptionWorker(
        config_path=args.config,
        on_result=None,  # 稍后设置
    )
    worker.set_post_processor(post_processor)

    # 创建result handler（需要worker引用）
    worker.on_result = _make_result_handler(output_method, append_newline, worker)
    if args.save_dataset:
        from app.plugins.dataset_recorder import wrap_result_handler
        worker.on_result = wrap_result_handler(worker.on_result, worker, args.dataset_dir)

    # 记录键盘监听器启动时间，用于启动保护期
    _listener_started_at = None

    # PTT 模式：按住录音，松开识别（使用 pynput）
    # Shift 键状态跟踪（用于 Shift+F9 长句模式）
    _shift_pressed = [False]

    def _on_press(key):
        nonlocal _listener_started_at
        try:
            # 跟踪 Shift 键
            if key in (pynput_kb.Key.shift, pynput_kb.Key.shift_l, pynput_kb.Key.shift_r):
                _shift_pressed[0] = True
                return

            if key == pynput_kb.Key.f9:
                # 启动保护期：启动后前 N 秒忽略所有 F9，防止幽灵事件导致误触
                if _listener_started_at is not None:
                    elapsed = time.monotonic() - _listener_started_at
                    if elapsed < _STARTUP_GUARD_SECONDS:
                        logger.debug("启动保护期（%.1f秒），忽略 F9", _STARTUP_GUARD_SECONDS - elapsed)
                        return

                # 防重复：按住 F9 时键盘自动重复会多次触发 on_press
                if worker.is_running:
                    logger.debug("已在录音中，忽略重复 F9")
                    return

                is_long = _shift_pressed[0]
                worker._long_mode = is_long
                mode_name = "AI 润色" if is_long else "快速"
                logger.info("开始录音（%s模式）", mode_name)
                worker.start()
        except Exception as exc:
            logger.debug("键盘处理错误: %s", exc)

    def _on_release(key):
        try:
            if key in (pynput_kb.Key.shift, pynput_kb.Key.shift_l, pynput_kb.Key.shift_r):
                _shift_pressed[0] = False
                return

            if key == pynput_kb.Key.f9 and worker.is_running:
                worker.stop()
        except Exception:
            pass

    # 启动键盘监听器
    _listener = pynput_kb.Listener(on_press=_on_press, on_release=_on_release)
    _listener.daemon = True
    _listener.start()
    _listener_started_at = time.monotonic()

    logger.info(
        "Speak Keyboard 启动完成（启动保护期 %.0f 秒），按住 F9 录音（快速），按住 Shift+F9 录音（AI 润色）",
        _STARTUP_GUARD_SECONDS,
    )
    if args.once:
        # --once 模式：忽略启动保护期，立即开始
        worker._long_mode = False
        worker.start()
        input("按 Enter 停止并退出...")
        if worker.is_running:
            worker.stop()
        _cleanup_and_exit(worker)
    else:
        # 创建系统托盘图标
        _has_tray = False
        _tray_icon = None
        try:
            def _on_exit(icon, item):
                """退出托盘图标（必须先 stop() 让图标消失，再让 run() 返回）"""
                icon.stop()

            _tray_icon = pystray.Icon(
                "vocotype",
                _build_tray_icon(),
                "VocoType - 按住 F9 录音",
                menu=pystray.Menu(
                    pystray.MenuItem("退出", _on_exit, default=True)
                ),
            )
            _has_tray = True
        except Exception as exc:
            logger.info("托盘图标不可用，键盘快捷键仍然正常工作: %s", exc)

        if _has_tray and _tray_icon is not None:
            _tray_icon.run()
        else:
            # 无图标模式：保持主线程运行
            _listener.join()
        
        _cleanup_and_exit(worker)


def _make_result_handler(output_method: str, append_newline: bool, worker: TranscriptionWorker):
    def _handle_result(result: TranscriptionResult) -> None:
        if result.error:
            logger.error("转写失败: %s", result.error)
            return

        # 获取转录统计信息
        stats = worker.transcription_stats
        
        logger.info(
            "转写成功: %s (推理 %.2fs) [已完成 %d/%d，队列剩余 %d]",
            result.text,
            result.inference_latency,
            stats["completed"],
            stats["submitted"],
            stats["pending"],
        )
        type_text(
            result.text,
            append_newline=append_newline,
            method=output_method,
        )

    return _handle_result


if __name__ == "__main__":
    main()
