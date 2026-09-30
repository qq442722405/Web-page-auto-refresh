# -*- coding: utf-8 -*-
"""Windows 真实鼠标点击 + 前台窗口保持工具。

仅作为“最后一层兜底”使用：当 Chromium 调试通道与 Qt 合成事件都不可用时，
短暂把本软件窗口置前点击，随后立刻把焦点还给原来的程序，并还原鼠标位置，
避免把用户的操作环境搞乱。
"""
import ctypes
import sys
import time
from ctypes import wintypes

IS_WINDOWS = sys.platform.startswith("win")

SW_SHOW = 5
SW_RESTORE = 9
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
INPUT_MOUSE = 0


class NativeInputUnavailable(RuntimeError):
    """当前平台不支持真实系统级点击。"""


if IS_WINDOWS:
    _ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG), ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD), ("dwExtraInfo", _ULONG_PTR),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
            ("dwExtraInfo", _ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
else:  # pragma: no cover - 仅 Windows 目标平台使用
    user32 = None
    kernel32 = None


def supported():
    return IS_WINDOWS and user32 is not None


def _require():
    if not supported():
        raise NativeInputUnavailable("仅 Windows 支持真实系统级点击")


# ============================================================ 窗口前台 ====
def foreground_window():
    _require()
    return int(user32.GetForegroundWindow() or 0)


def bring_to_front(hwnd, settle=0.08):
    """尽全力把窗口推到前台；返回是否成功。"""
    _require()
    hwnd = int(hwnd)
    if not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    foreground = user32.GetForegroundWindow()
    if int(foreground or 0) == hwnd:
        return True

    fg_thread = user32.GetWindowThreadProcessId(foreground, None)
    current_thread = kernel32.GetCurrentThreadId()
    attached = False
    if fg_thread and fg_thread != current_thread:
        attached = bool(user32.AttachThreadInput(fg_thread, current_thread, True))
    try:
        user32.ShowWindow(hwnd, SW_SHOW)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(fg_thread, current_thread, False)
    time.sleep(settle)
    return int(user32.GetForegroundWindow() or 0) == hwnd


class temporary_foreground:
    """with 语法：临时置前某个窗口，退出时自动把焦点还给原窗口。"""

    def __init__(self, hwnd, settle=0.08):
        self.hwnd = int(hwnd or 0)
        self.settle = settle
        self.previous = 0
        self.succeeded = False

    def __enter__(self):
        if not supported():
            return self
        self.previous = foreground_window()
        if self.previous != self.hwnd:
            self.succeeded = bring_to_front(self.hwnd, self.settle)
        else:
            self.succeeded = True
        return self

    def __exit__(self, exc_type, exc, tb):
        if not supported():
            return False
        if self.previous and self.previous != self.hwnd:
            try:
                bring_to_front(self.previous, 0.02)
            except Exception:
                pass
        return False


# ============================================================ 鼠标输入 ====
def cursor_position():
    _require()
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        raise NativeInputUnavailable("GetCursorPos 失败")
    return point.x, point.y


def set_cursor_position(x, y):
    _require()
    if not user32.SetCursorPos(int(x), int(y)):
        raise NativeInputUnavailable("SetCursorPos 失败")


def virtual_screen_metrics():
    """跨显示器正确的坐标换算基线。"""
    _require()
    origin_x = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
    origin_y = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
    width = user32.GetSystemMetrics(78)      # SM_CXVIRTUALSCREEN
    height = user32.GetSystemMetrics(79)     # SM_CYVIRTUALSCREEN
    return origin_x, origin_y, max(1, width), max(1, height)


def _absolute_input(x, y):
    origin_x, origin_y, width, height = virtual_screen_metrics()
    abs_x = int((x - origin_x) * 65535.0 / max(1, width - 1))
    abs_y = int((y - origin_y) * 65535.0 / max(1, height - 1))
    entry = INPUT(type=INPUT_MOUSE)
    return entry, abs_x, abs_y


def send_mouse_down_up(x, y):
    """在当前位置按下并释放鼠标左键（SendInput 绝对坐标）。"""
    _require()
    entry, abs_x, abs_y = _absolute_input(x, y)
    events = []
    for flags in (MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
                  MOUSEEVENTF_LEFTDOWN,
                  MOUSEEVENTF_LEFTUP):
        item = INPUT(type=INPUT_MOUSE)
        item.union.mi.dx = abs_x
        item.union.mi.dy = abs_y
        item.union.mi.dwFlags = flags
        events.append(item)
    array = (INPUT * len(events))(*events)
    sent = user32.SendInput(len(events), ctypes.byref(array), ctypes.sizeof(INPUT))
    if sent != len(events):
        raise NativeInputUnavailable("SendInput 只投递了 %d/%d 个事件" % (sent, len(events)))
    return True


def real_click(x, y, restore_cursor=True):
    """发送一次真实的系统级左键点击（不依赖目标窗口是否已被置前）。"""
    _require()
    saved = cursor_position()
    try:
        send_mouse_down_up(x, y)
    finally:
        if restore_cursor:
            try:
                set_cursor_position(*saved)
            except Exception:
                pass
    return True
