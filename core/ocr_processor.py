# -*- coding: utf-8 -*-
"""OCR 处理器：从扫描页面图片中识别文字"""
import os
import tempfile
# 必须在导入 OpenCV/ONNXRuntime 前设置，避免本地推理创建过多 BLAS 线程并耗尽内存。
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
import cv2
from rapidocr_onnxruntime import RapidOCR


# 全局 OCR 实例（避免重复加载模型）
_ocr_engine = None


def get_ocr_engine(language="ch"):
    """获取 OCR 引擎单例"""
    global _ocr_engine
    if _ocr_engine is None:
        try:
            _ocr_engine = RapidOCR(lang=language)
        except TypeError:
            _ocr_engine = RapidOCR()
    return _ocr_engine


def _resize_for_ocr(image_path, max_dim=1600):
    """如果图片太大，缩放到合理尺寸以避免 ONNX 内存溢出。

    Args:
        image_path: 原始图片路径
        max_dim: 最大边长（像素）

    Returns:
        tuple: (处理后的图片路径, 是否为临时文件)
    """
    img = cv2.imread(image_path)
    if img is None:
        return image_path, False

    h, w = img.shape[:2]
    if max(h, w) <= max_dim:
        return image_path, False

    scale = max_dim / max(h, w)
    new_w, new_h = int(w * scale), int(h * scale)
    img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)

    # 使用系统临时目录，避免沙箱安全删除拦截
    fd, tmp_path = tempfile.mkstemp(suffix='.png', prefix='ocr_resized_')
    os.close(fd)
    cv2.imwrite(tmp_path, img)
    return tmp_path, True


def ocr_image(image_path, language="ch"):
    """对单张图片进行 OCR 识别。

    Args:
        image_path: 图片路径

    Returns:
        dict: {
            'text': str,           # 全文
            'blocks': list[dict],  # 文本块列表
        }
    """
    engine = get_ocr_engine(language)

    # 1200px 足够识别常见连环画文字，并显著降低 ONNX 内存占用。
    tmp_path, is_temp = _resize_for_ocr(image_path, max_dim=1200)

    try:
        try:
            result, _ = engine(tmp_path)
        except Exception as first_error:
            retry_path, retry_temp = _resize_for_ocr(image_path, max_dim=800)
            try:
                result, _ = engine(retry_path)
            except Exception as retry_error:
                raise RuntimeError(
                    "OCR 推理失败。已限制 ONNX/OpenBLAS 线程并使用 800px 图片重试；"
                    f"首次错误: {first_error}; 重试错误: {retry_error}"
                ) from retry_error
            finally:
                if retry_temp:
                    try:
                        os.remove(retry_path)
                    except OSError:
                        pass

        if not result:
            return {'text': '', 'blocks': []}

        blocks = []
        texts = []
        for item in result:
            # item: [box_coords, text, confidence]
            box = item[0]
            text = str(item[1]).strip()
            conf = float(item[2])

            if text and conf > 0.3:
                # 计算文本块中心位置
                xs = [float(p[0]) for p in box]
                ys = [float(p[1]) for p in box]

                blocks.append({
                    'text': text,
                    'confidence': conf,
                    'position': {
                        'x': sum(xs) / len(xs),
                        'y': sum(ys) / len(ys),
                        'top': min(ys),
                        'bottom': max(ys),
                        'left': min(xs),
                        'right': max(xs),
                    }
                })
                texts.append(text)

        return {
            'text': '\n'.join(texts),
            'blocks': blocks,
        }
    finally:
        if is_temp:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def ocr_pages(image_paths, progress_callback=None, language="ch"):
    """对多张页面图片进行 OCR 识别。

    Args:
        image_paths: 图片路径列表
        progress_callback: 回调函数 (current, total, message)

    Returns:
        list[dict]: 每页的 OCR 结果列表
    """
    total = len(image_paths)
    results = []

    for i, path in enumerate(image_paths):
        result = ocr_image(path, language=language)
        results.append({
            'page_num': i + 1,
            'image_path': path,
            'text': result['text'],
            'blocks': result['blocks'],
        })

        if progress_callback:
            progress_callback(i + 1, total, f"OCR识别 {i + 1}/{total}")

    return results
