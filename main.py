import sys, ctypes, json, time
from PySide6.QtCore import Qt, QPoint, QTimer, QUrl
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, QLabel, QTextEdit, QSpinBox
from PySide6.QtWebEngineWidgets import QWebEngineView

class TestWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('后台鼠标点击测试工具')
        self.resize(1200, 800)
        self.pick = None
        root=QWidget(); self.setCentralWidget(root); lay=QVBoxLayout(root)
        row=QHBoxLayout(); lay.addLayout(row)
        self.url=QLineEdit('https://example.com'); row.addWidget(self.url,1)
        b=QPushButton('打开网页'); b.clicked.connect(self.load); row.addWidget(b)
        self.status=QLabel('未拾取'); lay.addWidget(self.status)
        row2=QHBoxLayout(); lay.addLayout(row2)
        p=QPushButton('🎯 拾取点击位置'); p.clicked.connect(self.pick_point); row2.addWidget(p)
        for i in range(1,7):
            b=QPushButton(f'测试方案 {i}'); b.clicked.connect(lambda _, n=i:self.test(n)); row2.addWidget(b)
        self.web=QWebEngineView(); lay.addWidget(self.web,1)
        self.log=QTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(150); lay.addWidget(self.log)
        self.web.installEventFilter(self)
        self.web.loadFinished.connect(lambda ok:self.logmsg('页面加载 '+('成功' if ok else '失败')))
        self.load()
    def logmsg(self,s): self.log.append(time.strftime('%H:%M:%S')+' '+s)
    def load(self): self.web.load(QUrl(self.url.text().strip())); self.logmsg('加载: '+self.url.text().strip())
    def pick_point(self):
        self.pick = None
        self.picking = True
        self.status.setText("🎯 拾取模式：请点击网页目标位置（ESC取消）")
        self.logmsg("进入拾取模式：已启用十字准星，请点击网页目标位置")
        try:
            self.web.setCursor(Qt.CursorShape.CrossCursor)
            self.web.viewport().setCursor(Qt.CursorShape.CrossCursor)
            self.web.viewport().installEventFilter(self)
        except Exception as e:
            self.logmsg("设置网页十字准星异常: " + repr(e))
        try:
            QApplication.instance().setOverrideCursor(Qt.CursorShape.CrossCursor)
        except Exception as e:
            self.logmsg("设置系统十字准星异常: " + repr(e))

    def _finish_pick(self, global_pos):
        if not getattr(self, "picking", False):
            return False
        vp = self.web.mapFromGlobal(global_pos)
        if not self.web.rect().contains(vp):
            return False
        self.pick = (vp.x(), vp.y())
        self.picking = False
        try:
            self.web.unsetCursor()
            self.web.viewport().unsetCursor()
            self.web.viewport().removeEventFilter(self)
        except Exception:
            pass
        try:
            QApplication.instance().restoreOverrideCursor()
        except Exception:
            pass
        self.status.setText(f"✅ 已拾取 Web坐标: {self.pick}")
        self.logmsg(f"拾取位置成功: Web坐标 {self.pick}")
        return True

    def eventFilter(self, obj, e):
        if getattr(self, "picking", False):
            if e.type() == QEvent.Type.MouseButtonPress and e.button() == Qt.MouseButton.LeftButton:
                try:
                    gp = e.globalPosition().toPoint()
                except AttributeError:
                    gp = e.globalPos()
                if self._finish_pick(gp):
                    return True
            if e.type() == QEvent.Type.KeyPress and e.key() == Qt.Key.Key_Escape:
                self.picking = False
                try:
                    self.web.unsetCursor()
                    self.web.viewport().unsetCursor()
                    self.web.viewport().removeEventFilter(self)
                    QApplication.instance().restoreOverrideCursor()
                except Exception:
                    pass
                self.status.setText("已取消拾取")
                self.logmsg("取消拾取")
                return True
        return super().eventFilter(obj, e)

    def test(self,n):
        if not self.pick: self.logmsg('请先拾取位置'); return
        x,y=self.pick; self.logmsg(f'开始测试方案 {n}: {x},{y}')
        if n==1: self.js_click(x,y)
        elif n==2: self.js_mouse(x,y)
        elif n==3: self.qt_mouse_event(x,y)
        elif n==4: self.win_message_click(x,y)
        elif n==5: self.scheme5_child_windows(x,y)
        elif n==6: self.scheme6_real_click(x,y)
    def js_click(self,x,y):
        js=f'''(()=>{{let e=document.elementFromPoint({x},{y}); if(!e)return 'NO_ELEMENT'; e.click(); return 'OK:'+e.tagName+':'+(e.innerText||'').slice(0,30)}})()'''
        self.web.page().runJavaScript(js,lambda r:self.logmsg('方案1结果: '+str(r)))
    def js_mouse(self,x,y):
        js=f'''(()=>{{let e=document.elementFromPoint({x},{y}); if(!e)return 'NO_ELEMENT'; ['pointerover','pointerdown','mousedown','pointerup','mouseup','click'].forEach(t=>e.dispatchEvent(new MouseEvent(t,{{bubbles:true,cancelable:true,view:window,clientX:{x},clientY:{y},button:0,buttons:t.includes('down')?1:0}}))); return 'OK:'+e.tagName}})()'''
        self.web.page().runJavaScript(js,lambda r:self.logmsg('方案2结果: '+str(r)))
    def qt_mouse_event(self,x,y):
        # Inject a JS event while temporarily focusing the WebEngine view; useful for testing event chains.
        self.web.page().runJavaScript(f'''(()=>{{let e=document.elementFromPoint({x},{y}); if(!e)return 'NO_ELEMENT'; let r=e.getBoundingClientRect(); let X=r.left+r.width/2,Y=r.top+r.height/2; e.dispatchEvent(new PointerEvent('pointerdown',{{bubbles:true,clientX:X,clientY:Y,pointerId:1}})); e.dispatchEvent(new PointerEvent('pointerup',{{bubbles:true,clientX:X,clientY:Y,pointerId:1}})); e.click(); return 'OK:'+e.tagName}})()''',lambda r:self.logmsg('方案3结果: '+str(r)))
    def win_message_click(self,x,y):
        # Send WM_MOUSE messages to the WebEngine native child without moving the real cursor.
        if sys.platform!='win32': self.logmsg('方案4仅支持Windows'); return
        try:
            hwnd=int(self.web.winId())
            user32=ctypes.windll.user32
            WM_MOUSEMOVE=0x0200; WM_LBUTTONDOWN=0x0201; WM_LBUTTONUP=0x0202; MK_LBUTTON=0x0001
            lp=(y<<16)|(x & 0xffff)
            user32.PostMessageW(hwnd,WM_MOUSEMOVE,0,lp)
            user32.PostMessageW(hwnd,WM_LBUTTONDOWN,MK_LBUTTON,lp)
            user32.PostMessageW(hwnd,WM_LBUTTONUP,0,lp)
            self.logmsg(f'方案4已发送 Windows WM_MOUSE 消息 hwnd={hwnd}')
        except Exception as e: self.logmsg('方案4异常: '+repr(e))


    def _enum_child_windows(self, parent_hwnd):
        if sys.platform != "win32":
            return []
        user32 = ctypes.windll.user32
        CALLBACK = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        result = []

        @CALLBACK
        def callback(hwnd, lparam):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            result.append((int(hwnd), buf.value))
            return True

        user32.EnumChildWindows(wintypes.HWND(parent_hwnd), callback, 0)
        return result

    def scheme5_child_windows(self, x, y):
        if sys.platform != "win32":
            self.logmsg("方案5仅支持Windows")
            return

        user32 = ctypes.windll.user32
        parent = int(self.web.winId())
        children = self._enum_child_windows(parent)
        self.logmsg(f"方案5: parent HWND={parent}, 子窗口数量={len(children)}")

        screen_pt = self.web.mapToGlobal(QPoint(x, y))
        sx, sy = screen_pt.x(), screen_pt.y()

        candidates = []
        for hwnd, cls in children:
            rect = wintypes.RECT()
            if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                continue
            if rect.left <= sx < rect.right and rect.top <= sy < rect.bottom:
                candidates.append((hwnd, cls, rect))

        candidates.sort(
            key=lambda item: max(1, (item[2].right-item[2].left) *
                                 (item[2].bottom-item[2].top))
        )

        if not candidates:
            self.logmsg(f"方案5: 没找到包含屏幕坐标 ({sx},{sy}) 的子窗口")
            return

        for hwnd, cls, rect in candidates:
            cpt = wintypes.POINT(sx, sy)
            if not user32.ScreenToClient(wintypes.HWND(hwnd), ctypes.byref(cpt)):
                continue
            lp = ((cpt.y & 0xffff) << 16) | (cpt.x & 0xffff)
            try:
                user32.SendMessageW(wintypes.HWND(hwnd), 0x0200, 0, lp)
                user32.SendMessageW(wintypes.HWND(hwnd), 0x0201, 0x0001, lp)
                time.sleep(0.03)
                user32.SendMessageW(wintypes.HWND(hwnd), 0x0202, 0, lp)
                self.logmsg(
                    f"方案5发送: hwnd={hwnd} class={cls} "
                    f"screen=({sx},{sy}) client=({cpt.x},{cpt.y})"
                )
                return
            except Exception as e:
                self.logmsg(f"方案5异常 hwnd={hwnd}: {e}")

        self.logmsg("方案5: 候选窗口发送失败")

    def scheme6_real_click(self, x, y):
        if sys.platform != "win32":
            self.logmsg("方案6仅支持Windows")
            return

        user32 = ctypes.windll.user32

        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        old = POINT()
        if not user32.GetCursorPos(ctypes.byref(old)):
            self.logmsg("方案6: 无法读取鼠标位置")
            return

        target = self.web.mapToGlobal(QPoint(x, y))
        old_x, old_y = old.x, old.y
        self.logmsg(
            f"方案6: 真实点击 target=({target.x()},{target.y()}) "
            f"原位置=({old_x},{old_y})"
        )

        try:
            user32.SetCursorPos(target.x(), target.y())
            time.sleep(0.10)
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            time.sleep(0.05)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(0.10)
        finally:
            user32.SetCursorPos(old_x, old_y)

        self.logmsg("方案6完成：真实点击已发送，鼠标已恢复原位置")

if __name__=='__main__':
    app=QApplication(sys.argv); w=TestWindow(); w.show(); sys.exit(app.exec())
