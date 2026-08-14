# -*- coding: utf-8 -*-
"""OCR 处理器：从扫描页面图片中识别文字"""
import os
import re
import tempfile
import json
import subprocess
import sys
import time
# 必须在导入 OpenCV/ONNXRuntime 前设置，避免本地推理创建过多 BLAS 线程并耗尽内存。
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
# 全局 OCR 实例（避免重复加载模型）
_ocr_engine = None
_ocr_engine_name = None
OCR_STARTUP_TIMEOUT_SECONDS = max(30, int(os.environ.get("OCR_STARTUP_TIMEOUT_SECONDS", "120")))
OCR_PAGE_TIMEOUT_SECONDS = max(30, int(os.environ.get("OCR_PAGE_TIMEOUT_SECONDS", "90")))
OCR_TOTAL_TIMEOUT_SECONDS = max(300, int(os.environ.get("OCR_TOTAL_TIMEOUT_SECONDS", "1800")))


def get_ocr_engine(language="ch", engine="rapidocr"):
    """获取 OCR 引擎单例"""
    global _ocr_engine, _ocr_engine_name
    engine = (engine or "rapidocr").strip().lower()
    if engine not in ("rapidocr", "easyocr"):
        engine = "rapidocr"
    if _ocr_engine is not None and _ocr_engine_name == engine:
        return _ocr_engine
    if engine == "easyocr":
        try:
            import easyocr
        except ImportError as error:
            raise RuntimeError(
                "EasyOCR 未安装。请执行: python -m pip install easyocr"
            ) from error
        languages = ["en"] if language == "en" else ["ch_sim", "en"]
        if language == "chinese_cht":
            languages = ["ch_tra", "en"]
        _ocr_engine = easyocr.Reader(
            languages, gpu=False, verbose=False, download_enabled=True,
        )
    else:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as error:
            raise RuntimeError(
                "RapidOCR 未安装。请执行: python -m pip install rapidocr_onnxruntime"
            ) from error
        try:
            _ocr_engine = RapidOCR(lang=language)
        except TypeError:
            _ocr_engine = RapidOCR()
    _ocr_engine_name = engine
    return _ocr_engine


def _resize_for_ocr(image_path, max_dim=1600):
    """如果图片太大，缩放到合理尺寸以避免 ONNX 内存溢出。

    Args:
        image_path: 原始图片路径
        max_dim: 最大边长（像素）

    Returns:
        tuple: (处理后的图片路径, 是否为临时文件)
    """
    import cv2
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


def ocr_image(image_path, language="ch", engine="rapidocr"):
    """对单张图片进行 OCR 识别。

    Args:
        image_path: 图片路径

    Returns:
        dict: {
            'text': str,           # 全文
            'blocks': list[dict],  # 文本块列表
        }
    """
    ocr_engine = get_ocr_engine(language, engine=engine)

    # 1200px 足够识别常见连环画文字，并显著降低 ONNX 内存占用。
    tmp_path, is_temp = _resize_for_ocr(image_path, max_dim=1200)

    try:
        try:
            if (engine or "rapidocr").lower() == "easyocr":
                result = ocr_engine.readtext(tmp_path, detail=1, paragraph=False)
            else:
                result, _ = ocr_engine(tmp_path)
        except Exception as first_error:
            retry_path, retry_temp = _resize_for_ocr(image_path, max_dim=800)
            try:
                if (engine or "rapidocr").lower() == "easyocr":
                    result = ocr_engine.readtext(retry_path, detail=1, paragraph=False)
                else:
                    result, _ = ocr_engine(retry_path)
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
            conf = float(item[2]) if len(item) > 2 else 1.0

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


def ocr_pages(image_paths, progress_callback=None, language="ch", engine="rapidocr"):
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
        result = ocr_image(path, language=language, engine=engine)
        # Keep the original PDF page number encoded in page_XXXX.png.  Using
        # i + 1 here breaks non-contiguous selections such as 1,3,5-10 and
        # makes later scene-to-image mapping skip or duplicate pages.
        match = re.search(r"(?:^|_)page_(\d+)(?:\.[^.]+)?$", os.path.basename(path), re.I)
        page_num = int(match.group(1)) if match else i + 1
        results.append({
            'page_num': page_num,
            'image_path': path,
            'text': result['text'],
            'blocks': result['blocks'],
        })

        if progress_callback:
            progress_callback(i + 1, total, f"OCR识别 {i + 1}/{total}")

    return results


def _write_json_atomic(path, payload):
    temporary = path + ".part"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    os.replace(temporary, path)


def _stop_process(process):
    if process.poll() is not None:
        return
    try:
        # TerminateProcess returns immediately on Windows. Do not invoke a
        # shell-level task killer here: a broken native runtime can make that
        # command block and would defeat the timeout protecting this task.
        process.terminate()
        process.wait(timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        try:
            process.kill()
            process.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass


def ocr_pages_with_timeout(image_paths, progress_callback=None, language="ch", engine="rapidocr"):
    """Run OCR in a killable child process instead of blocking a task thread.

    Native ONNX initialization can become stuck on a server because of an
    incompatible runtime, a model-file lock, or security software. Threads
    cannot stop native code safely, while a child process can be terminated.
    """
    if not image_paths:
        return []
    work_dir = tempfile.mkdtemp(prefix="pdf2video_ocr_")
    request_path = os.path.join(work_dir, "request.json")
    result_path = os.path.join(work_dir, "results.json")
    progress_path = os.path.join(work_dir, "progress.json")
    worker_path = os.path.join(os.path.dirname(__file__), "ocr_worker.py")
    _write_json_atomic(request_path, {
        "image_paths": list(image_paths),
        "language": language,
        "engine": engine,
        "result_path": result_path,
        "progress_path": progress_path,
    })
    process = None
    started = time.monotonic()
    last_progress = started
    last_current = 0
    total = len(image_paths)
    try:
        process = subprocess.Popen(
            [sys.executable, worker_path, request_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if progress_callback:
            progress_callback(0, total, f"正在启动 {engine} OCR 引擎...")
        while process.poll() is None:
            now = time.monotonic()
            try:
                with open(progress_path, "r", encoding="utf-8") as handle:
                    progress = json.load(handle)
            except (OSError, ValueError, TypeError):
                progress = {}
            current = max(0, min(total, int(progress.get("current", 0) or 0)))
            if current > last_current:
                last_current = current
                last_progress = now
            if progress_callback:
                if current:
                    progress_callback(current, total, progress.get("message") or f"OCR识别 {current}/{total}")
                else:
                    elapsed = int(now - started)
                    progress_callback(0, total, f"正在加载 {engine} OCR 引擎（{elapsed}/{OCR_STARTUP_TIMEOUT_SECONDS} 秒）...")
            if not last_current and now - started > OCR_STARTUP_TIMEOUT_SECONDS:
                raise TimeoutError(
                    f"{engine} OCR 初始化超过 {OCR_STARTUP_TIMEOUT_SECONDS} 秒。"
                    "请检查服务器内存、ONNXRuntime 安装和模型文件权限，或在页面改选 EasyOCR。"
                )
            if last_current and now - last_progress > OCR_PAGE_TIMEOUT_SECONDS:
                raise TimeoutError(
                    f"{engine} OCR 在第 {last_current + 1} 页超过 {OCR_PAGE_TIMEOUT_SECONDS} 秒未完成。"
                    "请缩小页码范围后重试，或改选 EasyOCR。"
                )
            if now - started > OCR_TOTAL_TIMEOUT_SECONDS:
                raise TimeoutError(
                    f"OCR 总处理超过 {OCR_TOTAL_TIMEOUT_SECONDS} 秒，任务已停止。"
                    "请缩小页码范围或检查服务器资源。"
                )
            time.sleep(1)
        _, stderr = process.communicate(timeout=5)
        if process.returncode != 0:
            raise RuntimeError(f"{engine} OCR 子进程失败: {(stderr or '').strip()[-1600:]}")
        with open(result_path, "r", encoding="utf-8") as handle:
            results = json.load(handle)
        if not isinstance(results, list) or len(results) != total:
            raise RuntimeError(f"{engine} OCR 子进程未返回完整结果")
        return results
    finally:
        if process is not None:
            _stop_process(process)
        try:
            import shutil
            shutil.rmtree(work_dir, ignore_errors=True)
        except OSError:
            pass
