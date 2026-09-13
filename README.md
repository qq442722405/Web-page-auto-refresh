# 网页刷新数字监控 V24.2 前台点击稳定版

本版本将“后台点击拾取点位”恢复为真实 Windows 前台鼠标点击。

- 到时操作：刷新网页 / 前台点击拾取点位
- 拾取保存 QWebEngineView 内部坐标
- 执行时自动转换为 Windows 屏幕坐标
- 使用 Windows SetCursorPos + mouse_event 发送真实左键点击
- 删除网页元素点击相关功能
