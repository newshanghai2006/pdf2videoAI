# -*- coding: utf-8 -*-
"""PDF 处理器：将 PDF 每页提取为高分辨率图片。

引擎选择策略（自动、可移植，无任何写死的用户路径）：

  1) 进程内渲染（默认）：当前解释器能 import PyMuPDF 时，直接在进程内渲染。
     只要 venv 装了 requirements.txt 里的 PyMuPDF，这在任何机器/系统上都开箱即用。

  2) 显式外部解释器：设置环境变量 PDF_HELPER_PYTHON 指向另一个带 PyMuPDF 的
     Python（可为解释器路径，或像 "py -3.14" 这样的带参数命令）。

  3) 自动探测：当前解释器无法 import PyMuPDF 且未显式指定时，自动尝试
     `py -3.14` / `py -3` / `python3` / `python` 等常见解释器，取第一个
     能 import PyMuPDF 的，通过子进程 pdf_helper.py 完成渲染。

  少数机器上原生绑定会被安全软件拦截/极慢加载，导致 venv 内 import 失败——此时
  策略 2/3 会自动接管。对外接口保持一致：get_page_count() / extract_pages()。
"""
import os
import json
import shlex
import shutil
import subprocess
import re

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPER = os.path.join(PROJECT_DIR, "pdf_helper.py")

# 可选：显式外部渲染解释器。支持 "C:/path/python.exe" 或 "py -3.14" 这类带参形式。
EXTERNAL_PYTHON = os.environ.get("PDF_HELPER_PYTHON", "").strip()

# 缓存自动探测结果：None=未探测；[]=探测过但没找到；否则为 argv 前缀列表
_external_cmd_cache = None


def _fitz():
    """在当前解释器内导入 PyMuPDF，成功返回模块，失败返回 None。

    兼容新旧包名：PyMuPDF>=1.24 顶层模块为 pymupdf，旧版为 fitz。
    """
    for name in ("pymupdf", "fitz"):
        try:
            return __import__(name)
        except Exception:
            continue
    return None


def _use_inprocess():
    """是否走进程内渲染：未显式指定外部解释器且当前解释器可导入 PyMuPDF。"""
    if EXTERNAL_PYTHON:
        return False
    return _fitz() is not None


# ===== 进程内渲染（默认，可移植）=====

def _inproc_count(pdf_path):
    fitz = _fitz()
    doc = fitz.open(pdf_path)
    try:
        return doc.page_count
    finally:
        doc.close()


def _inproc_render(pdf_path, out_dir, pages, dpi, progress_callback=None):
    fitz = _fitz()
    os.makedirs(out_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    try:
        zoom = float(dpi) / 72.0
        mat = fitz.Matrix(zoom, zoom)
        total = len(pages)
        for idx, pno in enumerate(pages, 1):
            output_path = os.path.join(out_dir, "page_%04d.png" % pno)
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                if progress_callback:
                    progress_callback(idx, total, "复用PDF页面 %s/%s" % (idx, total))
                continue
            page = doc[pno - 1]
            pix = page.get_pixmap(matrix=mat)
            pix.save(output_path)
            if progress_callback:
                progress_callback(idx, total, "提取PDF页面 %s/%s" % (idx, total))
    finally:
        doc.close()


# ===== 子进程后备：外部解释器 + pdf_helper.py =====

def _probe_can_import_fitz(argv_prefix):
    """探测某个解释器命令能否 import PyMuPDF。argv_prefix 为列表。"""
    try:
        r = subprocess.run(
            argv_prefix + ["-c", "import importlib,sys;"
                           "sys.exit(0 if (importlib.util.find_spec('pymupdf') "
                           "or importlib.util.find_spec('fitz')) else 1)"],
            capture_output=True, timeout=30,
        )
        return r.returncode == 0
    except Exception:
        return False


def _candidate_commands():
    """返回按优先级排序的候选外部解释器命令（argv 前缀列表的列表）。"""
    cands = []
    py = shutil.which("py")  # Windows Python launcher
    if py:
        for ver in ("-3.14", "-3.13", "-3.12", "-3.11", "-3.10", "-3"):
            cands.append([py, ver])
    for exe in ("python3", "python"):
        p = shutil.which(exe)
        if p and os.path.realpath(p) != os.path.realpath(__import__("sys").executable):
            cands.append([p])
    return cands


def _resolve_external_cmd():
    """确定用于 pdf_helper.py 的外部解释器命令（argv 前缀列表），失败返回 []。"""
    global _external_cmd_cache

    # 显式配置优先（每次都用，不缓存否定结果）
    if EXTERNAL_PYTHON:
        prefix = shlex.split(EXTERNAL_PYTHON, posix=False) if (
            " " in EXTERNAL_PYTHON) else [EXTERNAL_PYTHON]
        return prefix

    if _external_cmd_cache is not None:
        return _external_cmd_cache

    found = []
    for cmd in _candidate_commands():
        if _probe_can_import_fitz(cmd):
            found = cmd
            break
    _external_cmd_cache = found
    return found


def _run_helper(args, progress_callback=None, timeout=900):
    """调用 pdf_helper.py 子进程，返回其 stdout（已 strip）。"""
    prefix = _resolve_external_cmd()
    if not prefix:
        raise RuntimeError(
            "当前解释器无法导入 PyMuPDF，且未找到可用的外部渲染解释器。"
            "请在本项目的 venv 中安装依赖（pip install -r requirements.txt），"
            "或设置环境变量 PDF_HELPER_PYTHON 指向一个已安装 PyMuPDF 的 Python "
            "（可用形式如 'py -3.14' 或解释器完整路径）。"
        )

    proc = subprocess.Popen(
        prefix + [HELPER] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=PROJECT_DIR,
    )

    err_lines = []
    if proc.stderr:
        for line in proc.stderr:
            line = line.rstrip("\n")
            if line.startswith("PROG ") and progress_callback:
                try:
                    _, c, t = line.split()
                    progress_callback(
                        int(c), int(t), "提取PDF页面 %s/%s" % (c, t)
                    )
                except ValueError:
                    pass
            elif line:
                err_lines.append(line)

    out, _ = proc.communicate(timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError("PDF 渲染失败: " + " | ".join(err_lines[-5:]))
    return out.strip()


# ===== 对外接口 =====

def get_page_count(pdf_path):
    """获取 PDF 页数。"""
    if _use_inprocess():
        return _inproc_count(pdf_path)
    out = _run_helper(["count", pdf_path])
    try:
        return int(out.strip().splitlines()[-1])
    except Exception:
        raise RuntimeError("无法获取 PDF 页数，渲染器输出: %r" % out)


def parse_page_selection(selection, total):
    """解析页面选择，如 ``1,3,5-10,12-20``，返回 1-based 页码列表。"""
    if total < 1:
        raise ValueError("PDF 没有可用页面")
    if selection is None or str(selection).strip() == "":
        return list(range(1, total + 1))
    pages = set()
    for token in re.split(r"[,，;；\s]+", str(selection).strip()):
        if not token:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if not match:
            raise ValueError(f"页面选择格式错误: {token}（示例: 1,3,5-10）")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < 1 or start > end or end > total:
            raise ValueError(f"页面范围 {token} 超出 1-{total} 或起止顺序错误")
        pages.update(range(start, end + 1))
    if not pages:
        raise ValueError("未选择任何页面")
    return sorted(pages)


def extract_pages(pdf_path, output_dir, dpi=200, prefix="page",
                  start_page=None, end_page=None, page_selection=None,
                  progress_callback=None):
    """将 PDF 指定页范围提取为 PNG 图片。

    Args:
        pdf_path: PDF 文件路径
        output_dir: 输出目录
        dpi: 渲染分辨率
        prefix: 文件名前缀（兼容旧接口，实际统一用 page_xxxx.png 命名）
        start_page: 起始页（1-based），None 表示从第1页
        end_page: 结束页（1-based, inclusive），None 表示到最后一页
        progress_callback: 回调函数 (current, total, message)

    Returns:
        list[str]: 生成的图片路径列表
    """
    total = get_page_count(pdf_path)
    if start_page is None:
        start_page = 1
    if end_page is None:
        end_page = total
    if page_selection is not None and str(page_selection).strip():
        pages = parse_page_selection(page_selection, total)
    else:
        start_page = max(1, min(start_page, total))
        end_page = max(start_page, min(end_page, total))
        pages = list(range(start_page, end_page + 1))

    if _use_inprocess():
        _inproc_render(pdf_path, output_dir, pages, dpi,
                       progress_callback=progress_callback)
    else:
        _run_helper(
            ["render", pdf_path, output_dir, json.dumps(pages), str(dpi)],
            progress_callback=progress_callback,
        )

    image_paths = [os.path.join(output_dir, "page_%04d.png" % p) for p in pages]
    return image_paths
