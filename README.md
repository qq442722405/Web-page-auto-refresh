# 网页刷新数字监控 V20.1

本版本为 PyInstaller `--onedir` 分目录版，解决 GitHub Actions 验证路径错误。

## 打包结果

PyInstaller 使用 `--onedir` 后，EXE 位于：

`dist/网页刷新数字监控/网页刷新数字监控.exe`

不能使用 `dist/网页刷新数字监控.exe` 判断文件是否存在。

## ddddocr 模型

工程代码不要求根目录存在 `models` 文件夹。GitHub Actions 使用 `--collect-all ddddocr`，会把 ddddocr 包内的数据和模型一起收集到打包目录。

## GitHub Actions

仅支持手动触发：`workflow_dispatch`。
