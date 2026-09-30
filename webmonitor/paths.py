# -*- coding: utf-8 -*-
"""全局路径定义。

EXE 运行时以可执行文件所在目录为基准；源码运行时以工程根目录为基准，
保证 auto_login_config.json 与 日志.TXT 始终落在用户可见的位置。
"""
import os
import sys

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ICON_FILE = os.path.join(BASE_DIR, "1.ico")
CONFIG_FILE = os.path.join(BASE_DIR, "auto_login_config.json")
LOG_FILE = os.path.join(BASE_DIR, "日志.TXT")


def _fallback_dir():
    """写权限不足时的兜底目录（用户文档目录或临时目录）。"""
    for candidate in (
        os.environ.get("LOCALAPPDATA"),
        os.path.expanduser("~"),
        os.environ.get("TEMP"),
    ):
        if candidate and os.path.isdir(candidate):
            return candidate
    return os.getcwd()


def writable_path(name):
    """返回一个确认可写的绝对路径；不可写时自动退回兜底目录。"""
    target = os.path.join(BASE_DIR, name)
    try:
        probe = open(target, "a", encoding="utf-8")
        probe.close()
        return target
    except Exception:
        return os.path.join(_fallback_dir(), name)
