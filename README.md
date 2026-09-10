# 后台鼠标点击测试工具

用于测试网页在 QWebEngine 中接受哪一种“后台点击”。

## 使用
1. 输入网页地址，点击“打开网页”。
2. 点击“拾取点击位置”。
3. 在网页目标按钮上点击一次。
4. 分别测试方案 1~4，并观察网页反应和日志。

### 方案
- 方案1：`element.click()`
- 方案2：完整 MouseEvent / PointerEvent 链
- 方案3：PointerEvent + click 组合
- 方案4：Windows `PostMessage(WM_MOUSEMOVE/WM_LBUTTONDOWN/UP)`，不移动真实鼠标

如果方案4仍不能触发，说明 QWebEngine/Chromium 子窗口不接受这种消息，需要换更底层的输入注入或针对网页事件做专门适配。
