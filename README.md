# 网页刷新数字监控 V24.4 · 前后台坐标点击版

本压缩包是修复后的完整源码，包含原有 Windows EXE 打包工作流，不包含已编译的 EXE。

## 本次修复

原版本的后台操作先查找网页元素，再保存选择器和文字指纹，因此画布、跨域框架等位置无法拾取，文字变化也会让操作失败。

现在前台和后台使用同一个“拾取点击点位”入口，共享同一组网页区域坐标：

- 前台点击：按原有方式移动系统鼠标并执行点击，目标网页需要在前台。
- 后台点击：向本软件内嵌浏览器的 Chromium 输入控件发送鼠标移动、左键按下和松开事件。不会移动系统鼠标，不主动激活软件窗口，其他程序盖在上面也不会改变事件接收对象。
- 取点只保存坐标，不触发网页按钮；Esc 取消时保留原点位。切换前后台无须重复取点。
- 不再保存或匹配网页元素，不再依赖 JavaScript 的 element.click()。文字变化、canvas、Shadow DOM、框架都由浏览器按坐标处理。

## 使用

1. 加载网页，选择“后台点击（不移动鼠标）”。
2. 点击“拾取点击点位”，在网页需要点击的位置按下并松开鼠标。设置面板会自动收起。
3. 确认显示坐标，使用“测试一次当前操作”验证，再开启定时操作。
4. 可以将其他程序放到本软件前面；后台操作仍发送给本软件内的网页。

点位自动保存到 auto_login_config.json。已有网页局部坐标继续使用；旧的后台元素记录不再使用，如果以前只有元素记录，需要重新取一次点。

坐标相对于网页可视区域，移动软件窗口无需重新取点。网页滚动、缩放或布局改变后，坐标处的内容可能变化，需要检查或重新取点。后台点击仅针对软件内嵌网页，不控制其他软件。

OCR 仍使用屏幕截图，本次修复不改变其遮挡限制。最小化、隐藏窗口或网页自行暂停时的行为不作为本次保证；推荐保持窗口展开，让其他程序覆盖它。发送成功表示鼠标事件已交给浏览器，最终结果以网页为准。

## 运行与打包

建议 Windows + Python 3.11，在本目录安装依赖后运行：

```powershell
python -m pip install -r requirements.txt
python main.py
```

GitHub 仓库根目录应为本目录，main.py、requirements.txt 与 .github 同级。手动运行 Actions 的“Windows EXE 手动打包”工作流；下载并解压完整产物目录运行，不要只拷贝 exe。

工作流已改为使用真实 Qt WebEngine 测试后台坐标点击，不再用 Edge 的 DOM 测试替代。

## 测试

无需 GUI/OCR 依赖的逻辑测试：

```powershell
python -m unittest discover -s tests -v
python -m compileall -q main.py background_click.py tests
```

安装 PySide6 后，在 Windows 桌面环境运行实际浏览器测试：

```powershell
python tests/test_browser.py
$env:QT_SCALE_FACTOR = '1.5'
python tests/test_browser.py
```

测试使用真实 WebEngine 和项目中的实际取点浮层，验证取点不点击、完整鼠标事件、单次计数、画布、Shadow DOM、同源/隔离源框架、文字变化、网页缩放、滚动、窗口移动及外部进程遮挡。测试临时创建一个遮挡窗口，结束时自动关闭。逻辑测试通过 AST 提取实际主窗口方法执行，避免启动 OCR 和 HTTP 服务。

本地验证结果见 验证记录.txt。

## 实现参考

Qt 鼠标事件使用接收控件的局部坐标、窗口坐标和全局坐标构造，坐标保持 Qt 逻辑像素，由 Qt/Chromium 处理网页缩放与设备像素比：[Qt QMouseEvent 文档](https://doc.qt.io/qtforpython-6.8/PySide6/QtGui/QMouseEvent.html)。
