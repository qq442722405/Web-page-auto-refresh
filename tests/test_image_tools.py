# -*- coding: utf-8 -*-
"""图像处理管线的离线校验（需要 numpy/opencv）。

    python tests/test_image_tools.py

把合成的数字行图像走一遍真实流程：QImage -> BGR -> 行切分 -> PNG 编码。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Windows CI 的控制台默认使用 cp1252，打印中文会抛 UnicodeEncodeError。
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is None:
        continue
    try:
        _reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def build_rows():
    import numpy as np
    image = np.full((150, 260, 3), 255, dtype=np.uint8)
    # 三行“数字”，每行由若干短笔画组成（不能有整行长条，否则会被当作表格横线剔除）
    for top in (10, 60, 110):
        for left in range(20, 120, 26):
            image[top:top + 28, left:left + 18] = 0
    return image


def main():
    from PySide6.QtGui import QImage
    from webmonitor.image_tools import encode_row, qimage_to_bgr, segment_rows

    rgb = build_rows()
    height, width = rgb.shape[:2]
    q_image = QImage(rgb.data, width, height, width * 3, QImage.Format_RGB888)
    bgr = qimage_to_bgr(q_image)
    if bgr is None:
        raise AssertionError("QImage 转换失败")
    if bgr.shape[:2] != (height, width):
        raise AssertionError("转换尺寸不一致: %s" % (bgr.shape,))
    print("PASS QImage -> BGR %sx%s" % (bgr.shape[1], bgr.shape[0]))

    rows = segment_rows(bgr)
    if len(rows) != 3:
        raise AssertionError("行切分结果异常: %s" % rows)
    for (start, end) in rows:
        if end - start < 4:
            raise AssertionError("切分出行高过小: %s" % ((start, end),))
    print("PASS 行切分得到 %d 行: %s" % (len(rows), rows))

    encoded_rows = []
    for start, end in rows:
        encoded = encode_row(bgr[start:end, :])
        if not encoded:
            raise AssertionError("PNG 编码失败")
        encoded_rows.append(encoded)
    if not all(item[:8] == b"\x89PNG\r\n\x1a\n" for item in encoded_rows):
        raise AssertionError("PNG 文件头不正确")
    print("PASS 每行 PNG 编码成功，大小 = %s bytes" % [len(item) for item in encoded_rows])

    empty = encode_row(bgr[0:2, :])
    if empty is not None:
        raise AssertionError("过小的行不应被编码")
    print("PASS 过小行被忽略")
    print("图像处理管线校验通过")


if __name__ == "__main__":
    try:
        main()
    except ImportError as exc:
        print("SKIP 缺少依赖: %s" % exc)
