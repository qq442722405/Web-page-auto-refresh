# -*- coding: utf-8 -*-
"""网页对象：新窗口处理与证书容错。

单独成模块便于测试：不需要 OCR/图像处理依赖即可导入。
"""
from PySide6.QtWebEngineCore import QWebEngineCertificateError, QWebEnginePage


class MonitorWebPage(QWebEnginePage):
    """单网页监控：用户触发的新窗口请求一律在本网页区域内打开。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.newWindowRequested.connect(self._open_requested_window)

    def _open_requested_window(self, request):
        # openIn 保留 Chromium 原始导航请求（含 POST 数据），
        # 比用 requestedUrl 重新导航更保真，也不会重复触发点击。
        if request.isUserInitiated():
            request.openIn(self)

    def certificateError(self, error: QWebEngineCertificateError) -> bool:  # noqa: N802
        error.acceptCertificate()
        return True
