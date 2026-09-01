# 网页刷新数字监控 V19.0

## 本版重点修复
- 修复 `resizeEvent` 调用不存在的 `update_alarm_buttons` 导致的异常。
- OCR 使用单独后台线程，避免阻塞主界面。
- 防止 OCR 任务重叠。
- WebEngine 正在加载时不重复 reload。
- 两次网页 reload 最少间隔 5 秒。
- WebEngine 加载超过 45 秒自动解除加载锁，避免长期卡死后刷新逻辑失控。
- 禁用 WebEngine HTTP 缓存，保留 Cookie，降低长期运行缓存增长。
- Windows 下默认关闭 GPU 合成，降低部分显卡/驱动环境下 QWebEngine 长时间运行崩溃风险。
- 不再使用 `taskkill`、`os._exit` 强制杀死自身，改为正常 Qt 退出流程。
- `日志.TXT` 持续记录运行、OCR、WebEngine、Qt 消息和诊断信息。

## GitHub Actions
工作流已改为仅 `workflow_dispatch` 手动触发，不会因为 push 自动打包。

## 运行日志
程序目录会生成/追加：`日志.TXT`。
重点关注：
- `心跳诊断`
- `WebEngine开始加载`
- `网页刷新异常`
- `OCR运行异常`
- `Qt消息`
- `FATAL`
