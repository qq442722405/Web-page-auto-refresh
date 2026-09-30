# -*- coding: utf-8 -*-
"""图像工具：QImage 转 OpenCV + 行切分（cv2/numpy 惰性导入）。"""
from PySide6.QtGui import QImage

import cv2
import numpy as np


def qimage_to_bgr(image):
    """把 Qt 截图转换为 OpenCV 的 BGR 数组。"""
    if image is None or image.isNull():
        return None
    source = image.convertToFormat(QImage.Format_RGB888)
    width, height = source.width(), source.height()
    if width <= 0 or height <= 0:
        return None
    stride = source.bytesPerLine()
    buffer = np.frombuffer(source.bits(), dtype=np.uint8, count=height * stride)
    rgb = buffer.reshape((height, stride))[:, :width * 3].reshape((height, width, 3))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def segment_rows(img_bgr):
    """按水平投影把区域切分成若干“行”，返回 [(start_y, end_y), ...]。"""
    height, width = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

    if np.mean(gray) > 127:
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    else:
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    kernel_len = max(10, width // 3)
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_len, 1))
    horizontal_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
    binary_clean = cv2.subtract(binary, horizontal_lines)

    projection = np.sum(binary_clean, axis=1)
    rows = []
    in_row = False
    start_y = 0
    min_row_height = 4

    for y, value in enumerate(projection):
        if value > 255 and not in_row:
            in_row = True
            start_y = y
        elif value <= 255 and in_row:
            in_row = False
            if (y - start_y) >= min_row_height:
                rows.append((max(0, start_y - 2), min(height, y + 2)))
    if in_row and (len(projection) - start_y) >= min_row_height:
        rows.append((max(0, start_y - 2), height))

    if not rows or (len(rows) == 1 and (rows[0][1] - rows[0][0]) > height * 0.75 and height > 30):
        contours, _ = cv2.findContours(binary_clean, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for contour in contours:
            bx, by, bw, bh = cv2.boundingRect(contour)
            if bh >= 4 and bw >= 2:
                boxes.append((bx, by, bw, bh))
        if boxes:
            boxes.sort(key=lambda item: item[1])
            grouped = []
            current = [boxes[0]]
            for box in boxes[1:]:
                prev_y = current[-1][1]
                prev_h = current[-1][3]
                if abs(box[1] - prev_y) < max(prev_h, box[3]) * 0.6:
                    current.append(box)
                else:
                    grouped.append(current)
                    current = [box]
            if current:
                grouped.append(current)
            rows = []
            for group in grouped:
                min_y = min(item[1] for item in group)
                max_y = max(item[1] + item[3] for item in group)
                rows.append((max(0, min_y - 2), min(height, max_y + 2)))

    if not rows:
        rows = [(0, height)]
    return rows


def encode_row(row_img):
    """把一行图像放大、加白边后编码为 PNG bytes，供 OCR 引擎识别。"""
    height, width = row_img.shape[:2]
    if height < 3 or width < 3:
        return None
    resized = cv2.resize(row_img, (width * 2, height * 2), interpolation=cv2.INTER_CUBIC)
    padded = cv2.copyMakeBorder(resized, 10, 10, 15, 15, cv2.BORDER_CONSTANT, value=[255, 255, 255])
    ok, buffer = cv2.imencode(".png", padded, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    del resized, padded
    if not ok:
        return None
    return bytes(buffer)
