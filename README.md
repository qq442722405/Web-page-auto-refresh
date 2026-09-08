# 网页刷新数字监控 V24.2

本版本基于 V24.1 修改：
- 页面设置保持“操作间隔”逻辑。
- 到时操作支持：刷新网页、点击拾取点位、点击网页元素。
- 新增“拾取网页元素”：在网页上移动鼠标查看高亮，点击目标元素后保存 CSS Selector + XPath。
- 到时执行网页元素点击时，优先 CSS Selector，失败后使用 XPath，并调用网页元素 click()。
- 保留真实系统鼠标点位点击方式。
- 保留 Chromium / Qt WebEngine、GPU、磁盘缓存、OCR、报警、截图、账号、日志和 GitHub Actions。
