"""Text injection utilities for Windows.

CS2 等全屏游戏退出后常见问题：
1. Ctrl/Alt/Shift 粘滞 → 后续模拟按键变成快捷键
2. SendInput 回报成功但焦点不在目标文本框
3. 中文 IME 打开时 KEYEVENTF_UNICODE 被吞掉
4. 64 位下 INPUT 结构体尺寸不正确导致行为异常

策略：抬起粘滞键 → 剪贴板 Ctrl+V（主路径）→ UNICODE → 控件消息 → keyboard 库
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import time


logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

SendInput = user32.SendInput
GetMessageExtraInfo = user32.GetMessageExtraInfo
PeekMessageW = user32.PeekMessageW
GetForegroundWindow = user32.GetForegroundWindow
GetWindowTextW = user32.GetWindowTextW
GetWindowTextLengthW = user32.GetWindowTextLengthW
GetClassNameW = user32.GetClassNameW
PostMessageW = user32.PostMessageW
SendMessageW = user32.SendMessageW
GetWindowThreadProcessId = user32.GetWindowThreadProcessId
AttachThreadInput = user32.AttachThreadInput
GetGUIThreadInfo = user32.GetGUIThreadInfo
GetAsyncKeyState = user32.GetAsyncKeyState
MapVirtualKeyW = user32.MapVirtualKeyW

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_EXTENDEDKEY = 0x0001

VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_SHIFT = 0x10
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_V = 0x56
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LMENU = 0xA4
VK_RMENU = 0xA5

WM_CHAR = 0x0102
WM_PASTE = 0x0302
EM_REPLACESEL = 0x00C2
PM_NOREMOVE = 0x0000

_MODIFIER_VKS = (
    VK_CONTROL,
    VK_LCONTROL,
    VK_RCONTROL,
    VK_MENU,
    VK_LMENU,
    VK_RMENU,
    VK_SHIFT,
    VK_LSHIFT,
    VK_RSHIFT,
    VK_LWIN,
    VK_RWIN,
)

# 已知支持 EM_REPLACESEL / WM_PASTE 的原生控件类名片段
_NATIVE_EDIT_HINTS = (
    "edit",
    "richedit",
    "richedit20",
    "richedit50",
    "scintilla",
    "textBox",  # WinForms 有时暴露
)


# ---------------------------------------------------------------------------
# Win32 structures（完整 union，保证 x64 下 sizeof(INPUT) 正确）
# ---------------------------------------------------------------------------

if hasattr(wintypes, "ULONG_PTR"):
    ULONG_PTR = wintypes.ULONG_PTR  # type: ignore[attr-defined]
else:
    ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", INPUT_UNION),
    ]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_message_queue() -> None:
    msg = wintypes.MSG()
    PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)


def _window_title(hwnd: int) -> str:
    if not hwnd:
        return ""
    length = GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _window_class(hwnd: int) -> str:
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    GetClassNameW(hwnd, buf, 256)
    return buf.value


def _get_focused_hwnd() -> int:
    """真正持有键盘焦点的控件（优先插入符/焦点子控件）。"""
    fg = GetForegroundWindow()
    if not fg:
        return 0

    tid = GetWindowThreadProcessId(fg, None)
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    if GetGUIThreadInfo(tid, ctypes.byref(info)):
        for candidate in (info.hwndCaret, info.hwndFocus, info.hwndActive, fg):
            if candidate:
                return int(candidate)
    return int(fg)


def _is_native_edit(hwnd: int) -> bool:
    cls = _window_class(hwnd).lower()
    return any(h.lower() in cls for h in _NATIVE_EDIT_HINTS)


def _emit_vk(vk: int, key_up: bool = False) -> bool:
    scan = MapVirtualKeyW(vk, 0) & 0xFF
    flags = KEYEVENTF_KEYUP if key_up else 0
    if vk in (VK_LWIN, VK_RWIN, VK_RMENU, VK_MENU):
        flags |= KEYEVENTF_EXTENDEDKEY

    inp = INPUT(
        type=INPUT_KEYBOARD,
        union=INPUT_UNION(
            ki=KEYBDINPUT(
                wVk=vk,
                wScan=scan,
                dwFlags=flags,
                time=0,
                dwExtraInfo=GetMessageExtraInfo(),
            )
        ),
    )
    return SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) == 1


def _release_stuck_modifiers() -> None:
    """抬起粘滞修饰键（CS2 退出后常见）。"""
    _ensure_message_queue()
    stuck = []
    for vk in _MODIFIER_VKS:
        if GetAsyncKeyState(vk) & 0x8000:
            stuck.append(hex(vk))
        # 无论状态如何都强制 keyup 一次，更稳
        _emit_vk(vk, key_up=True)
    if stuck:
        logger.info("检测到并抬起粘滞修饰键: %s", ", ".join(stuck))
    else:
        logger.debug("已强制抬起全部修饰键（预防粘滞）")


def _emit_unicode_unit(unit: int) -> bool:
    arr = (INPUT * 2)(
        INPUT(
            type=INPUT_KEYBOARD,
            union=INPUT_UNION(
                ki=KEYBDINPUT(
                    wVk=0,
                    wScan=unit,
                    dwFlags=KEYEVENTF_UNICODE,
                    time=0,
                    dwExtraInfo=GetMessageExtraInfo(),
                )
            ),
        ),
        INPUT(
            type=INPUT_KEYBOARD,
            union=INPUT_UNION(
                ki=KEYBDINPUT(
                    wVk=0,
                    wScan=unit,
                    dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                    time=0,
                    dwExtraInfo=GetMessageExtraInfo(),
                )
            ),
        ),
    )
    sent = SendInput(2, ctypes.byref(arr), ctypes.sizeof(INPUT))
    if sent != 2:
        err = kernel32.GetLastError()
        logger.warning(
            "SendInput UNICODE 失败 unit=U+%04X sent=%s err=%s sizeof(INPUT)=%s",
            unit,
            sent,
            err,
            ctypes.sizeof(INPUT),
        )
        return False
    return True


def _emit_unicode_char(char: str) -> bool:
    cp = ord(char)
    if cp > 0xFFFF:
        cp -= 0x10000
        hi = 0xD800 + (cp >> 10)
        lo = 0xDC00 + (cp & 0x3FF)
        return _emit_unicode_unit(hi) and _emit_unicode_unit(lo)
    return _emit_unicode_unit(cp)


def _copy_to_clipboard(payload: str):
    import pyperclip

    try:
        prev = pyperclip.paste()
    except Exception:
        prev = None
    pyperclip.copy(payload)
    # 确认写进去了
    try:
        if pyperclip.paste() != payload:
            # 再试一次
            time.sleep(0.02)
            pyperclip.copy(payload)
    except Exception:
        pass
    return pyperclip, prev


def _restore_clipboard(pyperclip, prev) -> None:
    if prev is None:
        return
    try:
        pyperclip.copy(prev)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def type_text(text: str, append_newline: bool = False, method: str = "auto") -> None:
    if not text:
        return

    payload = text + ("\r\n" if append_newline else "")
    fg = GetForegroundWindow()
    focused = _get_focused_hwnd()
    logger.info(
        "开始注入 len=%d method=%s sizeof(INPUT)=%d fg=0x%X('%s') focus=0x%X class='%s'",
        len(payload),
        method,
        ctypes.sizeof(INPUT),
        int(fg or 0),
        _window_title(int(fg or 0))[:40],
        int(focused or 0),
        _window_class(int(focused or 0))[:40],
    )

    _ensure_message_queue()
    _release_stuck_modifiers()

    method = (method or "auto").lower()
    if method == "unicode":
        order = ["unicode", "clipboard", "editmsg", "type", "postmsg"]
    elif method == "type":
        order = ["type", "clipboard", "unicode", "editmsg", "postmsg"]
    elif method == "clipboard":
        order = ["clipboard", "editmsg", "unicode", "type", "postmsg"]
    else:
        # auto：剪贴板 Ctrl+V 对浏览器/编辑器/Electron 最稳（尤其 CS2 后）
        order = ["clipboard", "editmsg", "unicode", "type", "postmsg"]

    for mode in order:
        ok = False
        try:
            if mode == "clipboard":
                ok = _try_clipboard_ctrl_v(payload)
            elif mode == "editmsg":
                ok = _try_native_edit_message(payload)
            elif mode == "unicode":
                ok = _type_with_unicode(payload)
            elif mode == "type":
                ok = _type_with_keyboard(payload)
            elif mode == "postmsg":
                ok = _try_postmessage_injection(payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("注入方式 %s 异常: %s", mode, exc)
            ok = False

        if ok:
            logger.info("文本注入成功，方式=%s", mode)
            return
        logger.warning("注入方式 %s 未生效，尝试下一个", mode)

    logger.error("所有文本注入方式均失败: %s", payload[:80])


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


def _try_clipboard_ctrl_v(payload: str) -> bool:
    """剪贴板 + 模拟 Ctrl+V（主路径）。"""
    try:
        pyperclip, prev = _copy_to_clipboard(payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("剪贴板写入失败: %s", exc)
        return False

    try:
        _release_stuck_modifiers()
        if not _emit_ctrl_v():
            return False
        # 给目标应用读剪贴板的时间；过早恢复会导致粘贴空内容
        time.sleep(0.12)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ctrl+V 注入异常: %s", exc)
        return False
    finally:
        time.sleep(0.05)
        _restore_clipboard(pyperclip, prev)


def _try_native_edit_message(payload: str) -> bool:
    """仅对原生 Edit/RichEdit/Scintilla 使用 EM_REPLACESEL + WM_PASTE。"""
    hwnd = _get_focused_hwnd()
    if not hwnd:
        logger.warning("editmsg: 无焦点控件")
        return False

    if not _is_native_edit(hwnd):
        logger.debug(
            "editmsg: 跳过非原生控件 class='%s'",
            _window_class(hwnd),
        )
        return False

    try:
        pyperclip, prev = _copy_to_clipboard(payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("editmsg 剪贴板失败: %s", exc)
        return False

    fg = GetForegroundWindow()
    target_tid = GetWindowThreadProcessId(fg or hwnd, None)
    current_tid = kernel32.GetCurrentThreadId()
    attached = False
    if target_tid and target_tid != current_tid:
        attached = bool(AttachThreadInput(current_tid, target_tid, True))

    try:
        # EM_REPLACESEL：lParam 必须是宽字符串指针
        text_ptr = ctypes.c_wchar_p(payload)
        SendMessageW(hwnd, EM_REPLACESEL, 1, text_ptr)
        SendMessageW(hwnd, WM_PASTE, 0, 0)
        logger.info(
            "editmsg 已发送 hwnd=0x%X class='%s'",
            int(hwnd),
            _window_class(hwnd),
        )
        time.sleep(0.05)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("editmsg 异常: %s", exc)
        return False
    finally:
        if attached:
            AttachThreadInput(current_tid, target_tid, False)
        time.sleep(0.05)
        _restore_clipboard(pyperclip, prev)


def _type_with_unicode(payload: str) -> bool:
    _ensure_message_queue()
    _release_stuck_modifiers()
    for char in payload:
        if not _emit_unicode_char(char):
            logger.info("UNICODE 失败 %r，20ms 后重试", char)
            time.sleep(0.02)
            _release_stuck_modifiers()
            if not _emit_unicode_char(char):
                return False
        time.sleep(0.001)
    return True


def _type_with_keyboard(payload: str) -> bool:
    try:
        import keyboard

        _ensure_message_queue()
        _release_stuck_modifiers()
        keyboard.write(payload, delay=0.001)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("keyboard.write 失败: %s", exc)
        return False


def _try_postmessage_injection(payload: str) -> bool:
    hwnd = _get_focused_hwnd()
    if not hwnd:
        logger.warning("PostMessage: 无焦点窗口")
        return False

    fg = GetForegroundWindow()
    target_tid = GetWindowThreadProcessId(fg or hwnd, None)
    current_tid = kernel32.GetCurrentThreadId()
    attached = False
    if target_tid and target_tid != current_tid:
        attached = bool(AttachThreadInput(current_tid, target_tid, True))

    try:
        for char in payload:
            if char == "\n":
                continue
            code = 0x0D if char == "\r" else ord(char)
            if not PostMessageW(hwnd, WM_CHAR, code, 0):
                logger.warning("PostMessage WM_CHAR 失败 char=%r", char)
                return False
            time.sleep(0.001)
        return True
    finally:
        if attached:
            AttachThreadInput(current_tid, target_tid, False)


def _emit_ctrl_v() -> bool:
    """发送完整 Ctrl+V，并确保结束后 Ctrl 已抬起。"""
    _ensure_message_queue()

    # 先确保左右 Ctrl 都抬起
    _emit_vk(VK_CONTROL, key_up=True)
    _emit_vk(VK_LCONTROL, key_up=True)
    _emit_vk(VK_RCONTROL, key_up=True)
    time.sleep(0.01)

    steps = [
        (VK_CONTROL, False),
        (VK_V, False),
        (VK_V, True),
        (VK_CONTROL, True),
    ]
    for vk, key_up in steps:
        if not _emit_vk(vk, key_up=key_up):
            logger.warning("Ctrl+V 单键失败 vk=0x%X up=%s，重试", vk, key_up)
            time.sleep(0.02)
            if not _emit_vk(vk, key_up=key_up):
                # 失败时务必抬起 Ctrl，避免用户键盘被卡住
                _emit_vk(VK_CONTROL, key_up=True)
                return False
        time.sleep(0.008)

    # 双保险抬起
    _emit_vk(VK_CONTROL, key_up=True)
    return True
