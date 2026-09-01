# 网页刷新数字监控 V19.1

本版本重点修复 **Windows EXE 打包后提示“未安装 ddddocr”**的问题。

## ddddocr 修复

GitHub Actions 打包时会：

1. 安装固定版本 `ddddocr==1.6.1`；
2. 安装 `onnxruntime`；
3. 打包前实际初始化 `ddddocr.DdddOcr()` 做模型自检；
4. PyInstaller 使用 `--collect-all ddddocr` 把 `.onnx` 模型文件一起打入 EXE；
5. 同时收集 ONNX Runtime 的模块和二进制文件；
6. 最终检查 EXE 是否生成。

这样不会再出现 Python 环境明明安装了 ddddocr，但 onefile EXE 内缺少 `common.onnx/common_old.onnx` 等模型文件的问题。

## 打包

GitHub Actions 现在保持 **手动触发**：Actions → Windows EXE 手动打包 → Run workflow。

## 日志

程序目录会生成 `日志.TXT`。如果 ddddocr 导入失败，日志会记录实际异常原因，而不再简单显示“未安装”。
