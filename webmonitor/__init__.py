# -*- coding: utf-8 -*-
"""网页刷新数字监控 —— 后台执行优化版。

模块说明：
    logging_setup / paths    运行日志与崩溃诊断
    config                   配置读写与校验
    alarm                    报警规则判定（纯逻辑）
    coords                   坐标换算（纯逻辑）
    gate                     操作闸门（纯逻辑）
    cdp                      最小 Chromium 调试协议客户端（纯标准库）
    qt_click / win_input     Qt 合成鼠标事件 / Windows 真实点击
    page_controller          点击、刷新、截图的多层降级调度
    ocr_worker / image_tools OCR 与图像处理
    ui_overlay               网页内识别框浮层与右上角控制栏
    window                   主窗口装配与业务流程
"""
__version__ = "25.0.0"
