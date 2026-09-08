# 网页刷新数字监控 V22.0

## 浏览器内核
本程序使用 **PySide6 Qt WebEngine + Chromium 内核**进行网页渲染，属于 Chromium 系列内核，与 Google Chrome / Microsoft Edge 使用同系列网页渲染技术。

程序不使用 IE 内核，也不要求电脑另外安装 Chrome。

## 网页刷新优化
自动刷新会检查网页是否仍在加载：

- 正在加载时跳过刷新；
- 页面加载完成后重新进入刷新周期；
- 避免“页面还没打开就又到时间刷新”的循环。

## OCR
程序继续使用 ddddocr + OpenCV/NumPy 图像预处理进行屏幕区域数字识别，不要求网页能够直接提供数字数据。

## 打包
GitHub Actions 使用 **workflow_dispatch 手动触发**，采用 PyInstaller `--onedir` 分目录模式。

最终产物：

`dist/网页刷新数字监控/网页刷新数字监控.exe`

整个 `网页刷新数字监控` 文件夹需要一起使用，不能只拿 EXE 单独运行。

## ddddocr
打包前会实际初始化 ddddocr 模型；PyInstaller 会收集 ddddocr 与 ONNX Runtime 的运行文件和模型。

## 日志
程序目录自动记录 `日志.TXT`，用于排查长期运行、网页加载、OCR 和 Chromium WebEngine 问题。
