@echo off
python -m pip install -r requirements.txt
python -m py_compile main.py
pyinstaller --noconfirm --clean --onedir --windowed --name="后台鼠标点击测试工具" main.py
pause
