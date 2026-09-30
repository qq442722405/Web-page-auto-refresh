# -*- coding: utf-8 -*-
"""坐标换算（网页控件逻辑像素 <-> Chromium CSS 像素）。

所有持久化坐标统一为 QWebEngineView 的内部逻辑像素：
  - 移动/缩放软件窗口无需重新取点；
  - 换算到 CDP 时除以网页缩放系数即可得到 CSS 像素；
  - 需要真实屏幕坐标时再由 Qt 的 mapToGlobal 换算。
"""


def valid_zoom(zoom):
    try:
        zoom = float(zoom)
    except (TypeError, ValueError):
        return 1.0
    if zoom <= 0:
        return 1.0
    return zoom


def to_css(value, zoom):
    """控件像素 -> CSS 像素。"""
    return float(value) / valid_zoom(zoom)


def css_point(x, y, zoom):
    return (max(0.0, to_css(x, zoom)), max(0.0, to_css(y, zoom)))


def css_clip(x, y, width, height, zoom):
    """把控件局部矩形换算为 CDP Page.captureScreenshot 的裁剪参数。"""
    zoom = valid_zoom(zoom)
    return {
        "x": max(0.0, x / zoom),
        "y": max(0.0, y / zoom),
        "width": max(1.0, width / zoom),
        "height": max(1.0, height / zoom),
        "scale": 1.0,
    }


def rect_to_list(rect):
    return [int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height())]


def clamp_rect_values(x, y, width, height, max_x, max_y):
    """限制拖拽结果在可视区域内，避免识别框完全跑出网页。"""
    limit_x = max(1, int(max_x))
    limit_y = max(1, int(max_y))
    x = max(0, min(int(x), limit_x - 1))
    y = max(0, min(int(y), limit_y - 1))
    width = max(1, min(int(width), limit_x - x))
    height = max(1, min(int(height), limit_y - y))
    return x, y, width, height
