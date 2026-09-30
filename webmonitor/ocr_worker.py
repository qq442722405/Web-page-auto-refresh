# -*- coding: utf-8 -*-
"""OCR 后台工作线程。

- ddddocr 只初始化一次，并放在工作线程里初始化，避免启动卡界面；
- 识别过程完全脱离主线程，长时间运行也不会拖动界面；
- 每一轮结束后立刻释放 PNG byte 引用，避免内存持续增长。
"""
import re

from PySide6.QtCore import QObject, Signal, Slot

HAS_DDDDOCR = False
DDDDOCR_ERROR = ""
try:
    import ddddocr  # noqa: F401
    HAS_DDDDOCR = True
except Exception as import_error:  # pragma: no cover - 运行环境缺失时才会走到
    import traceback
    DDDDOCR_ERROR = "".join(traceback.format_exception_only(
        type(import_error), import_error)).strip()


class OCRWorker(QObject):
    finished = Signal(object, object)
    status = Signal(str, object)  # message, is_error

    def __init__(self):
        super().__init__()
        self.engine = None
        self.error = ""
        self.ready = False

    @Slot()
    def initialize(self):
        if self.ready or self.error:
            return
        if not HAS_DDDDOCR:
            self.error = DDDDOCR_ERROR or "ddddocr 未安装"
            self.status.emit("ddddocr 不可用: %s" % self.error, True)
            return
        try:
            self.engine = ddddocr.DdddOcr(show_ad=False)
            self.ready = True
            self.status.emit("ddddocr 识别引擎初始化成功", False)
        except Exception as exc:
            self.error = str(exc)
            self.status.emit("ddddocr 初始化失败: %s" % exc, True)

    @Slot(object)
    def process(self, jobs):
        if self.engine is None:
            self.finished.emit(None, self.error or DDDDOCR_ERROR or "ddddocr 初始化失败")
            return
        results = []
        try:
            for box_idx, row_pngs in jobs:
                digits = []
                for png in row_pngs:
                    raw = str(self.engine.classification(png))
                    cleaned = raw.replace(",", ".").replace(":", ".")
                    found = re.findall(r"\d+\.?\d*", cleaned)
                    if found:
                        digits.append(found[0])
                results.append((box_idx, digits))
            self.finished.emit(results, None)
        except Exception as exc:
            self.finished.emit(None, str(exc))
        finally:
            # 主动断开对本轮大块 PNG 数据的引用，长期运行不易堆积。
            jobs = None
            results = None
