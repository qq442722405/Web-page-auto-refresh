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
        for i in range(1,5):
            b=QPushButton(f'测试方案 {i}'); b.clicked.connect(lambda _, n=i:self.test(n)); row2.addWidget(b)
        self.web=QWebEngineView(); lay.addWidget(self.web,1)
        self.log=QTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(150); lay.addWidget(self.log)
        self.web.installEventFilter(self)
        self.web.loadFinished.connect(lambda ok:self.logmsg('页面加载 '+('成功' if ok else '失败')))
        self.load()
    def logmsg(self,s): self.log.append(time.strftime('%H:%M:%S')+' '+s)
    def load(self): self.web.load(QUrl(self.url.text().strip())); self.logmsg('加载: '+self.url.text().strip())
    def pick_point(self):
        self.pick=None; self.status.setText('请在网页上点击要测试的位置（ESC取消）'); self.logmsg('进入拾取模式')
        self.web.setCursor(Qt.CrossCursor); self.picking=True
    def eventFilter(self,obj,e):
        if obj is self.web and getattr(self,'picking',False):
            if e.type()==e.Type.MouseButtonPress and e.button()==Qt.LeftButton:
                pos=e.position().toPoint(); self.pick=(pos.x(),pos.y()); self.picking=False; self.web.unsetCursor()
                self.status.setText(f'已拾取 Web坐标: {self.pick}')
                self.logmsg(f'拾取位置 {self.pick}')
                return True
            if e.type()==e.Type.KeyPress and e.key()==Qt.Key_Escape:
                self.picking=False; self.web.unsetCursor(); self.logmsg('取消拾取'); return True
        return super().eventFilter(obj,e)
    def test(self,n):
        if not self.pick: self.logmsg('请先拾取位置'); return
        x,y=self.pick; self.logmsg(f'开始测试方案 {n}: {x},{y}')
        if n==1: self.js_click(x,y)
        elif n==2: self.js_mouse(x,y)
        elif n==3: self.qt_mouse_event(x,y)
        elif n==4: self.win_message_click(x,y)
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

if __name__=='__main__':
    app=QApplication(sys.argv); w=TestWindow(); w.show(); sys.exit(app.exec())
