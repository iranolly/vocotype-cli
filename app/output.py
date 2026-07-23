"""Text injection utilities for Windows."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import time


logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32
SendInput = user32.SendInput
GetMessageExtraInfo = user32.GetMessageExtraInfo
PeekMessageW = user32.PeekMessageW
GetForegroundWindow = user32.GetForegroundWindow
PostMessageW = user32.PostMessageW
GetWindowThreadProcessId = user32.GetWindowThreadProcessId
AttachThreadInput = user32.AttachThreadInput

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_CONTROL = 0x11
VK_V = 0x56
WM_CHAR = 0x0102
PM_NOREMOVE = 0x0000


def _ensure_message_queue() -> None:
    """确保当前线程拥有 Windows 消息队列。

    SendInput 在线程没有消息队列时可能失败，尤其是在全屏游戏退出后。
    调用 PeekMessage(PM_NOREMOVE) 会延迟创建线程的消息队列。
    """
    msg = wintypes.MSG()
    PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)


if hasattr(wintypes, "ULONG_PTR"):
    ULONG_PTR = wintypes.ULONG_PTR  # type: ignore[attr-defined]
else:  # Fallback for Python builds lacking ULONG_PTR in wintypes
    if ctypes.sizeof(ctypes.c_void_p) == ctypes.sizeof(ctypes.c_uint64):
        ULONG_PTR = ctypes.c_uint64
    else:
        ULONG_PTR = ctypes.c_uint32


class KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class InputUnion(ctypes.Union):
    _fields_ = [("ki", KeyboardInput)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", InputUnion)]


def _emit_unicode_char(char: str) -> bool:
    code_point = ord(char)
    input_array_type = INPUT * 2
    inputs = input_array_type(
        INPUT(
            type=INPUT_KEYBOARD,
            union=InputUnion(
                ki=KeyboardInput(
                    wVk=0,
                    wScan=code_point,
                    dwFlags=KEYEVENTF_UNICODE,
                    time=0,
                    dwExtraInfo=GetMessageExtraInfo(),
                )
            ),
        ),
        INPUT(
            type=INPUT_KEYBOARD,
            union=InputUnion(
                ki=KeyboardInput(
                    wVk=0,
                    wScan=code_point,
                    dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                    time=0,
                    dwExtraInfo=GetMessageExtraInfo(),
                )
            ),
        ),
    )
    pointer = ctypes.byref(inputs[0])
    sent = SendInput(len(inputs), pointer, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        logger.warning("SendInput 发送字符失败，char=%s，返回值=%s", char, sent)
        return False
    return True


def type_text(text: str, append_newline: bool = False, method: str = "auto") -> None:
    if not text:
        return

    payload = text + ("\r\n" if append_newline else "")
    logger.debug("注入文本: %s", payload)

    # 确保线程消息队列已初始化，让 SendInput 更可靠
    _ensure_message_queue()

    method = (method or "auto").lower()
    if method == "unicode":
        order = ["unicode"]
    elif method == "type":
        order = ["type", "unicode", "clipboard", "postmsg"]
    elif method == "clipboard":
        order = ["clipboard", "unicode", "type", "postmsg"]
    else:
        # auto 默认优先级：unicode (最可靠，正确校验返回值)
        # -> clipboard (剪贴板+Ctrl+V，适合大段文本)
        # -> type (keyboard库，如果已安装)
        # -> postmsg (PostMessage WM_CHAR 终极兜底，绕过 SendInput)
        order = ["unicode", "clipboard", "type", "postmsg"]

    for mode in order:
        if mode == "unicode" and _type_with_unicode(payload):
            return
        if mode == "clipboard" and _try_clipboard_injection(payload):
            return
        if mode == "type" and _type_with_keyboard(payload):
            return
        if mode == "postmsg" and _try_postmessage_injection(payload):
            return

    logger.error("所有文本注入方式均失败: %s", payload)


def _type_with_keyboard(payload: str) -> bool:
    """使用 keyboard 库注入文本（如果已安装）。不校验返回值。"""
    try:
        import keyboard

        _ensure_message_queue()
        keyboard.write(payload, delay=0.001)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("keyboard.write 失败: %s", exc)
        return False


def _type_with_unicode(payload: str) -> bool:
    """通过 SendInput KEYEVENTF_UNICODE 逐字符注入。
    每个字符都校验返回值，失败时等待 20ms 重试一次。
    """
    _ensure_message_queue()
    for char in payload:
        if not _emit_unicode_char(char):
            # 第一次失败后可能是系统状态问题，等待 20ms 后重试一次
            logger.info("SendInput 失败了（%r），20ms 后重试...", char)
            time.sleep(0.02)
            if not _emit_unicode_char(char):
                return False
    return True


def _try_clipboard_injection(payload: str) -> bool:
    """剪贴板注入：复制文本到剪贴板，然后模拟 Ctrl+V。"""
    try:
        import pyperclip
    except ImportError:
        return False

    try:
        prev_clip = pyperclip.paste()
    except Exception:
        prev_clip = None

    try:
        pyperclip.copy(payload)
        success = _emit_ctrl_v()
    except Exception as exc:  # noqa: BLE001
        logger.debug("剪贴板注入失败，退回逐字符输入: %s", exc)
        success = False
    finally:
        if prev_clip is not None:
            try:
                pyperclip.copy(prev_clip)
            except Exception:
                pass

    return success


def _try_postmessage_injection(payload: str) -> bool:
    """最终绝活：直接向前景窗口发送 WM_CHAR 消息。

    这完全绕过了 SendInput，因此不受 VAC 反作弊拦截或
    游戏 Raw Input 残留状态的影响。

    注意：部分现代 UI 框架（WPF/Qt/Web）不处理 WM_CHAR 消息，
    这种情况下此方法会失败，但不影响其他方法。
    """
    hwnd = GetForegroundWindow()
    if not hwnd:
        logger.warning("PostMessage: 没有前景窗口")
        return False

    # 将当前线程附加到目标窗口的线程，确保消息能被正确处理
    target_tid = GetWindowThreadProcessId(hwnd, None)
    current_tid = ctypes.windll.kernel32.GetCurrentThreadId()
    attached = False
    if target_tid != current_tid:
        if not AttachThreadInput(current_tid, target_tid, True):
            logger.warning("PostMessage: AttachThreadInput 失败，尝试不加附加继续")
        else:
            attached = True

    try:
        for char in payload:
            if char == '\n':
                # \r\n 中的 \n 跳过，只有 \r 投递回车
                continue
            code = ord(char)
            if char == '\r':
                code = 0x0D  # carriage return

            result = PostMessageW(hwnd, WM_CHAR, code, 0)
            if not result:
                logger.warning("PostMessage WM_CHAR 失败: char=%s (U+%04X)", char, code)
                return False
            time.sleep(0.001)  # 防止窗口消息队列溢出
        return True
    finally:
        if attached:
            AttachThreadInput(current_tid, target_tid, False)


def _emit_ctrl_v() -> bool:
    _ensure_message_queue()
    input_array_type = INPUT * 4
    inputs = input_array_type(
        INPUT(
            type=INPUT_KEYBOARD,
            union=InputUnion(
                ki=KeyboardInput(
                    wVk=VK_CONTROL,
                    wScan=0,
                    dwFlags=0,
                    time=0,
                    dwExtraInfo=GetMessageExtraInfo(),
                )
            ),
        ),
        INPUT(
            type=INPUT_KEYBOARD,
            union=InputUnion(
                ki=KeyboardInput(
                    wVk=VK_V,
                    wScan=0,
                    dwFlags=0,
                    time=0,
                    dwExtraInfo=GetMessageExtraInfo(),
                )
            ),
        ),
        INPUT(
            type=INPUT_KEYBOARD,
            union=InputUnion(
                ki=KeyboardInput(
                    wVk=VK_V,
                    wScan=0,
                    dwFlags=KEYEVENTF_KEYUP,
                    time=0,
                    dwExtraInfo=GetMessageExtraInfo(),
                )
            ),
        ),
        INPUT(
            type=INPUT_KEYBOARD,
            union=InputUnion(
                ki=KeyboardInput(
                    wVk=VK_CONTROL,
                    wScan=0,
                    dwFlags=KEYEVENTF_KEYUP,
                    time=0,
                    dwExtraInfo=GetMessageExtraInfo(),
                )
            ),
        ),
    )
    pointer = ctypes.byref(inputs[0])
    sent = SendInput(len(inputs), pointer, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        logger.warning("SendInput Ctrl+V 失败，返回值=%s", sent)
        # 等待 20ms 后重试一次（全屏游戏切换时常有短暂延迟）
        time.sleep(0.02)
        sent_retry = SendInput(len(inputs), pointer, ctypes.sizeof(INPUT))
        if sent_retry != len(inputs):
            logger.warning("SendInput Ctrl+V 第二次重试失败，返回值=%s", sent_retry)
            return False

    return True
