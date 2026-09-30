# -*- coding: utf-8 -*-
"""持久化运行日志、崩溃诊断与进程心跳。

日志独立于界面存在：程序闪退、卡死、被系统结束后，仍能直接查看最后一刻的状态。
"""
import faulthandler
import os
import sys
import threading
import time
import traceback

from .paths import LOG_FILE, writable_path

_log_lock = threading.RLock()
_fatal = threading.RLock()
_log_file_handle = None
_log_path = None
_faulthandler_file = None

try:
    _log_path = writable_path(os.path.basename(LOG_FILE))
    if os.path.abspath(_log_path) != os.path.abspath(LOG_FILE):
        sys.stderr.write("日志文件不可写，已切换到: %s\n" % _log_path)
except Exception:
    _log_path = LOG_FILE


def log_path():
    return _log_path


def _open_handle():
    global _log_file_handle
    if _log_file_handle is None or _log_file_handle.closed:
        _log_file_handle = open(_log_path, "a", encoding="utf-8", buffering=1)
    return _log_file_handle


def write_log(text, level="INFO"):
    """线程安全地追加一行日志；任何异常都不得影响业务逻辑。"""
    try:
        line = "[%s] [%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), level, text)
        with _log_lock:
            handle = _open_handle()
            handle.write(line.replace("\n", " | ") + "\n")
            handle.flush()
    except Exception:
        pass


def log_exception(prefix, exc=None):
    try:
        if exc is not None:
            detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        else:
            detail = traceback.format_exc()
        write_log("%s\n%s" % (prefix, detail), "ERROR")
    except Exception:
        pass


def install_crash_hooks():
    """把主线程、子线程异常与底层致命信号全部写入日志。"""
    def _main_excepthook(exc_type, exc_value, exc_tb):
        try:
            detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            write_log("未处理的主线程异常:\n%s" % detail, "FATAL")
        except Exception:
            pass
        try:
            sys.__excepthook__(exc_type, exc_value, exc_tb)
        except Exception:
            pass

    sys.excepthook = _main_excepthook

    if hasattr(threading, "excepthook"):
        def _thread_excepthook(args):
            try:
                detail = "".join(traceback.format_exception(args.exc_type, args.exc_value,
                                                            args.exc_traceback))
                write_log("线程异常 (%s):\n%s" % (getattr(args.thread, "name", "unknown"), detail),
                          "FATAL")
            except Exception:
                pass
        threading.excepthook = _thread_excepthook

    global _faulthandler_file
    try:
        _faulthandler_file = open(_log_path, "a", encoding="utf-8", buffering=1)
        faulthandler.enable(_faulthandler_file, all_threads=True)
    except Exception as exc:
        _faulthandler_file = None
        write_log("faulthandler 启用失败: %s" % exc, "WARN")


def flush_logs():
    for handle in (_log_file_handle, _faulthandler_file):
        try:
            if handle is not None and not handle.closed:
                handle.flush()
        except Exception:
            pass


def process_memory_mb():
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            psapi = ctypes.WinDLL("Psapi.dll")
            kernel32 = ctypes.WinDLL("kernel32.dll")
            if psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(),
                                          ctypes.byref(counters), counters.cb):
                return counters.WorkingSetSize / 1024.0 / 1024.0
        else:
            import resource
            value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return value / 1024.0
    except Exception:
        return None


install_crash_hooks()
write_log("运行环境初始化完成，PID=%s，日志文件=%s" % (os.getpid(), _log_path))
