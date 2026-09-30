# 网页刷新数字监控 V25 · 后台执行优化版

这一版解决的核心问题：**前面开着别的软件时，后台点击刷新/查询不执行**，同时对全部代码做了重构与稳定性优化。

## 一、为什么以前的“后台点击”会在前面的软件打开时失效

原来的后台点击是把鼠标事件投给 Qt 的 Chromium 渲染控件，这在窗口被遮挡、失去激活状态时并不可靠：

1. 窗口一旦被遮挡或长时间不激活，Chromium 会把页面当成后台页，渲染/输入管线进入降频或冻结状态；
2. Qt 的遮挡检测（native window occlusion）也会让网页停止响应输入；
3. 旧版 OCR 使用屏幕抓取（grabWindow），被其它窗口挡住时抓到的就是别人的画面。

V25 用三层递进通道解决这个问题，**并且不移动鼠标、不抢焦点**：

| 通道 | 原理 | 是否受遮挡影响 |
| --- | --- | --- |
| ① 调试通道 CDP（默认首选） | 通过本机 127.0.0.1 的 Chromium 调试端口（`Input.dispatchMouseEvent`）直接把鼠标事件交给网页 | **不受影响**，窗口甚至可以不在前台 |
| ② Qt 合成事件 | 向 Chromium 渲染控件投递 Qt 鼠标事件 | 基本不受影响（第二梯队） |
| ③ 真实置前点击（兜底） | 短暂把本软件置前点击，完成后**立刻把焦点还给原来的程序**并还原鼠标位置 | 只在①、②都失败时使用 |

同时给 Chromium 加上了 `--disable-renderer-backgrounding --disable-backgrounding-occluded-windows --disable-background-timer-throttling --disable-features=CalculateNativeWinOcclusion`，从底层禁止窗口被遮挡后页面被冻结/降频。

### OCR 也不再依赖屏幕抓取

新版 OCR 优先使用 `Page.captureScreenshot` 直接从网页取图（失败时才回落到屏幕抓取）。因此：

- 别的软件盖在上面、或者把本软件窗口拖到屏幕外，识别结果依然正确；
- 识别框坐标也统一改为**网页内坐标**，移动、缩放软件窗口都不需要重新框选；
- 旧配置（屏幕坐标的选框与点位）会在首次启动时自动转换一次。

## 二、使用方式

1. 启动软件加载网页。
2. 右上角 ⚙️ → **二. 后台执行（前置也能刷新）**：
   - 执行通道默认「🤖 智能」：调试通道 → Qt 事件 → 置前点击；
   - 想只用最稳的那一档，可选「⚡ 仅调试通道（CDP）」；
   - 点 **🔎 自检** 可以立刻看到三条通道的实际可用性和当前 URL。
3. 到时操作选择「🕶️ 后台点击（不打扰操作）」→ **📍 拾取点击点位** → **🧪 测试一次当前操作** 验证。
4. 打开其它软件，让它盖住本软件，观察日志：出现 `已通过调试通道发送点击` 即为真后台成功。

> 提示：本软件的三种刷新方式（刷新网页 / 前台点击 / 后台点击）共用同一个“拾取点位”，前后台不需要分别取点。

### 通道相关的安全与兼容说明

- 调试端口只绑定本机 `127.0.0.1`，外部机器无法访问；局域网内的其它用户进程理论上可用本机端口，如需彻底关闭，在「后台执行」区域把 `cdp_enabled` 设为 `false`（配置文件 `auto_login_config.json`）即可自动回落到其它通道。
- 若公司安全策略禁止本地调试端口，软件会自动识别并回落到 Qt 事件通道，无需手动改代码。

## 三、代码结构

```
main.py                  启动入口（环境/Chromium 参数 → QApplication → 主窗口）
webmonitor/
  logging_setup.py       持久化日志、崩溃钩子、faulthandler、内存心跳
  paths.py               配置/日志/图标路径，无写权限时自动兜底
  config.py              配置读写、字段校验、损坏文件降级
  alarm.py               报警规则（连续相同行 / 目标值 + 增量确认）
  coords.py              控件坐标 <-> CSS 像素换算
  gate.py                操作闸门，避免并发/跨模式残留
  cdp.py                 最小 Chrome DevTools Protocol 客户端（纯标准库）
  qt_click.py            Qt 合成鼠标事件
  win_input.py           Windows 真实点击 + 前台窗口保持/还原
  page_controller.py     点击 / 刷新 / 截图的三层降级调度
  web_page.py            新窗口在同网页打开、证书容错
  ui_overlay.py          网页内识别框浮层 + 右上角控制栏
  ocr_worker.py          ddddocr 工作线程（引擎在工作线程内初始化）
  image_tools.py         QImage→OpenCV、行切分、PNG 编码
  screenshot_service.py  手机扫码看图（带随机令牌的本地 HTTP）
  autofill.py            账号密码自动填充脚本
  window.py              主窗口装配与业务流程
```

### 相对 V24.5 的优化点

- **后台执行可靠性**：三层通道 + 自动降级 + 失败原因如实上报 + 内置自检。
- **OCR 不受遮挡**：改为从网页直接取图，识别框改为网页内坐标。
- **启动更快**：ddddocr 在工作线程初始化；cv2/numpy 惰性导入；主窗口不再做重复初始化。
- **去掉重复/死代码**：删除了重复的 OCR 引擎、无用的信号代理与遗留入口，并把 2000+ 行的单文件 `main.py` 拆成职责清晰的模块。
- **稳定性**：操作闸门防止并发点击与定时器打架；每次调用都带超时，网络异常不卡界面；退出时停全部定时器并回收 OCR 线程与 HTTP 服务。
- **可维护性**：纯逻辑模块（alarm / coords / gate / config / cdp）不依赖 Qt，可单独单元测试。

## 四、运行与打包

```powershell
python -m pip install -r requirements.txt
python main.py
```

GitHub Actions 仍使用「Windows EXE 手动打包」（`.github/workflows/build.yml`），Python 3.11 + PySide6(<6.10)；下载后解压**完整目录**运行，不要只拷 exe。

## 五、测试

```powershell
python -m unittest discover -s tests -v      # 纯逻辑回归（无需 GUI/OCR）
python tests/test_image_tools.py             # 图像处理管线（需要 numpy/opencv）
python tests/test_cdp_live.py                # 用本机 Edge/Chrome 验证调试通道能否真后台点击
python tests/test_browser.py                 # 真实 Qt WebEngine：取点、点击、缩放、滚动、被遮挡
python tests/test_new_windows.py             # 新窗口/新标签链接在原网页打开
```

实机结果：`test_cdp_live.py` 在完全不前置浏览器窗口的情况下，成功触发按钮、canvas、Shadow DOM 的完整 `pointerdown→mousedown→pointerup→mouseup→click` 事件序列（`trusted=true`），并成功取到裁剪区域的截图，验证了后台执行通道确实可用。
