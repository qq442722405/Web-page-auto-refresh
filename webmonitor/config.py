# -*- coding: utf-8 -*-
"""配置读写与升级迁移（不依赖 Qt，便于单元测试）。"""
import json
import os

from .paths import CONFIG_FILE
from .logging_setup import log_exception, write_log

DEFAULTS = {
    "url": "https://example.com/login",
    "account": "",
    "password": "",
    "zoom_level": 1.0,
    "auto_refresh": False,
    "operation_interval": 60,
    "operation_action": "refresh",
    "click_point": [],
    "click_point_space": "webview_local",
    "panel_collapsed": False,
    "screenshot_path": "",
    "selected_ip": "",
    "reminder_sound_index": 0,
    "reminder_custom_path": "",
    "reminder_sound_count": 3,
    "roi_list": [[100, 100, 300, 200]],
    "roi_space": "webview_local",
    "roi_multiplier": 1,
    "target_same_count": 3,
    "target_value": "",
    # --- V25 新增：后台执行策略 ---
    "cdp_enabled": True,          # 使用 Chromium 远程调试通道（真正的后台执行）
    "cdp_port": 0,                # 0 = 启动时自动挑选空闲端口
    "cdp_diagnosed": False,       # 是否已经提示过 CDP 不可用（避免每轮刷日志）
    "background_strategy": "auto",  # auto | cdp | qt | native
    "allow_temporary_foreground": True,  # 后台失败时允许“短暂置前点击”并自动还原
    "keep_page_active": True,     # 禁用 Chromium 后台降频/遮挡冻结
    "gif_screenshot_source": "cdp",  # cdp | screen
}

_ACTION_VALUES = ("refresh", "click", "background_click")
_STRATEGY_VALUES = ("auto", "cdp", "qt", "native")
_SOURCE_VALUES = ("cdp", "screen")


def clamp_int(value, low, high, fallback):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def clamp_float(value, low, high, fallback):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def normalize_roi(raw, fallback):
    """把 [x, y, w, h] 归一化为合法的整数列表。"""
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return fallback
    try:
        values = [int(v) for v in raw]
    except (TypeError, ValueError):
        return fallback
    if values[2] <= 0 or values[3] <= 0:
        return fallback
    return values


def load():
    """读取配置；任何损坏都退化为默认值，绝不因配置异常打断启动。"""
    cfg = dict(DEFAULTS)
    cfg.setdefault("screenshot_path", os.getcwd())
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                if "roi_rect" in data and "roi_list" not in data:
                    data["roi_list"] = [data["roi_rect"]]
                cfg.update(data)
        except Exception as exc:
            log_exception("读取配置文件失败，已使用默认配置", exc)

    # 旧配置可能残留 DOM 元素方案，V24.1 起统一使用坐标点击。
    cfg.pop("background_target", None)

    cfg["zoom_level"] = clamp_float(cfg.get("zoom_level"), 0.25, 3.0, 1.0)
    cfg["operation_interval"] = clamp_int(cfg.get("operation_interval"), 1, 3600, 60)
    cfg["target_same_count"] = clamp_int(cfg.get("target_same_count"), 1, 10, 3)
    cfg["cdp_port"] = clamp_int(cfg.get("cdp_port"), 0, 65535, 0)

    if cfg.get("operation_action") not in _ACTION_VALUES:
        cfg["operation_action"] = "refresh"
    if cfg.get("background_strategy") not in _STRATEGY_VALUES:
        cfg["background_strategy"] = "auto"
    if cfg.get("gif_screenshot_source") not in _SOURCE_VALUES:
        cfg["gif_screenshot_source"] = "cdp"

    point = cfg.get("click_point")
    if not isinstance(point, (list, tuple)) or len(point) < 2:
        cfg["click_point"] = []
    else:
        try:
            cfg["click_point"] = [int(point[0]), int(point[1])]
        except (TypeError, ValueError):
            cfg["click_point"] = []

    raw_list = cfg.get("roi_list")
    if isinstance(raw_list, (list, tuple)):
        boxes = []
        for item in raw_list:
            box = normalize_roi(item, None)
            if box is not None:
                boxes.append(box)
        cfg["roi_list"] = boxes
    else:
        cfg["roi_list"] = []

    for key in ("cdp_enabled", "auto_refresh", "allow_temporary_foreground",
                "keep_page_active", "panel_collapsed"):
        cfg[key] = bool(cfg.get(key, DEFAULTS.get(key, False)))
    return cfg


def save(cfg):
    try:
        payload = {k: v for k, v in cfg.items() if k != "background_target"}
        with open(CONFIG_FILE, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        return True
    except Exception as exc:
        log_exception("保存配置文件失败", exc)
        return False


def remember(cfg, **changes):
    """更新并落盘，配置面板到处 This-save 的统一入口。"""
    cfg.update(changes)
    save(cfg)
    return cfg


write_log("配置模块就绪，配置文件=%s" % CONFIG_FILE)
