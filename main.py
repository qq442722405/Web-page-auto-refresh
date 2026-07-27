# -*- coding: utf-8 -*-

# 1. 先导入系统和基础库
import sys
import json
import os
import subprocess
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import cv2

try:
    import ddddocr
    HAS_DDDDOCR = True
except ImportError:
    HAS_DDDDOCR = False

# 2. 导入 PySide6 组件（确保里面包含 QWidget）
from PySide6.QtCore import QUrl, Qt, QTimer, QDateTime, QRect, QPoint, QBuffer, QIODevice, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget,
    QPushButton, QLineEdit, QLabel, QSpinBox, QCheckBox,
    QSystemTrayIcon, QMenu, QGroupBox, QSizePolicy, QFileDialog, QDialog,
    QComboBox
)
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPen, QColor
from PySide6.QtNetwork import (
    QNetworkAccessManager, QNetworkRequest, QNetworkReply, 
    QNetworkInterface, QAbstractSocket
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineCertificateError, QWebEngineScript

# 3. 导入完之后，才能定义使用 QWidget 的类！
class ROIOverlay(QWidget):
    # ... 后续代码
