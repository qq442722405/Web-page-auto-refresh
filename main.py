# -*- coding: utf-8 -*-
"""启动入口。

注意：QTWEBENGINE_REMOTE_DEBUGGING 与 QTWEBENGINE_CHROMIUM_FLAGS 必须在
创建 QApplication 之前写入环境变量，否则 Chromium 不会打开调试端口，
也就无法使用“真后台”执行通道。
"""
import os
import socket
import sys

from webmonitor.config import load as load_config
from webmonitor.logging_setup import log_exception, write_log, flush_logs

DEFAULT_CDP_PORT = 9333


def find_free_port(start=DEFAULT_CDP_PORT, tries=50):
    """挑一个本机空闲端口给 Chromium 调试服务使用。"""
    for port in range(start, start + tries):
        probe = socket.socket()
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            probe.close()
    return 0


def prepare_chromium_environment(config):
    """准备 Chromium 启动参数，返回实际使用的调试端口（0 表示未启用）。"""
    try:
        base_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
        stable = [
            "--ignore-certificate-errors",
            "--enable-gpu",
            "--enable-gpu-compositing",
            "--disable-features=RendererCodeIntegrity,CalculateNativeWinOcclusion",
        ]
        if config.get("keep_page_active", True):
            # 关键：禁止窗口被遮挡/切到后台后 Chromium 冻结页面或降频，
            # 这样“前面的软件挡住本软件”时依然能照常刷新和点击。
            stable += [
                "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows",
                "--disable-background-timer-throttling",
            ]
        missing = [flag for flag in stable if flag not in base_flags]
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (base_flags + " " + " ".join(missing)).strip()
    except Exception as exc:
        log_exception("Chromium 参数设置失败", exc)

    if not config.get("cdp_enabled", True):
        # 继承来的环境变量也要清掉，确保“关闭”真的关闭。
        os.environ.pop("QTWEBENGINE_REMOTE_DEBUGGING", None)
        write_log("调试通道已在配置中关闭")
        return 0

    port = int(config.get("cdp_port") or 0) or find_free_port()
    if port:
        os.environ["QTWEBENGINE_REMOTE_DEBUGGING"] = "127.0.0.1:%d" % port
        write_log("已开启本机调试通道 127.0.0.1:%d（仅本机可访问）" % port)
    return port


def main(config=None, cdp_port=None):
    if config is None:
        config = load_config()
    if cdp_port is None:
        cdp_port = prepare_chromium_environment(config)
    write_log("开始创建 QApplication")
    from PySide6.QtCore import Qt, qInstallMessageHandler
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    try:
        def qt_message_handler(mode, context, message):
            write_log("Qt消息[%s]: %s | %s:%s" % (mode, message, context.file, context.line), "QT")
        qInstallMessageHandler(qt_message_handler)
    except Exception as exc:
        log_exception("Qt 日志钩子安装失败", exc)

    app.setQuitOnLastWindowClosed(True)
    app.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    app.setStyleSheet("""
        QMainWindow, QWidget, QDialog { background-color: #1a1a24; color: #cdd6f4; }
        QGroupBox { font-weight: bold; border: 1px solid #3b3b4f; border-radius: 6px;
                    margin-top: 8px; padding-top: 8px; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #38bdf8; }
        QLineEdit, QSpinBox, QComboBox { background-color: #262636; color: #ffffff;
                    border: 1px solid #3b3b4f; border-radius: 4px; padding: 3px; }
        QSpinBox::up-button, QSpinBox::down-button { width: 0px; height: 0px; border: none; }
        QSpinBox::up-arrow, QSpinBox::down-arrow { image: none; }
        QPushButton { background-color: #2d2d3f; color: #ffffff; border: 1px solid #474765;
                      border-radius: 4px; padding: 4px; }
        QPushButton:hover { background-color: #3b3b54; }
        QLabel { color: #94a3b8; }
    """)

    from webmonitor.window import MainWindow

    write_log("开始创建主窗口")
    window = MainWindow(config, cdp_port=cdp_port)
    window.show()
    write_log("主窗口已显示，进入事件循环")
    exit_code = app.exec()
    write_log("事件循环结束，exit_code=%s" % exit_code)
    flush_logs()
    return exit_code


if __name__ == "__main__":
    try:
        _config = load_config()
        _port = prepare_chromium_environment(_config)
    except Exception as start_exc:  # 配置异常也不得阻止启动
        log_exception("启动准备阶段异常", start_exc)
        from webmonitor.config import DEFAULTS
        _config = dict(DEFAULTS)
        _config.setdefault("screenshot_path", os.getcwd())
        _port = 0
    try:
        sys.exit(main(_config, _port))
    except Exception as fatal:
        log_exception("主程序启动/事件循环异常", fatal)
        raise
