# -*- coding: utf-8 -*-
"""PDF 渲染辅助进程：在系统 Python 3.14 下用 PyMuPDF(fitz) 渲染 PDF。

由 core/pdf_processor.py 通过子进程调用，用来规避 venv 中 pypdfium2 首次导入
需要约 30 分钟（原生 pdfium.dll 被安全软件极慢扫描）导致上传/渲染卡死的问题。

用法:
  python pdf_helper.py count  <pdf>
  python pdf_helper.py render <pdf> <out_dir> <pages_json> [dpi]

stdout: 结果（count 输出页数；render 输出 JSON）
stderr: 进度行 "PROG <current> <total>"
"""
import sys
import os
import json

import fitz


def do_count(pdf_path):
    doc = fitz.open(pdf_path)
    n = doc.page_count
    doc.close()
    # 输出到 stdout，供父进程解析
    print(n)


def do_render(pdf_path, out_dir, pages, dpi):
    os.makedirs(out_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    zoom = float(dpi) / 72.0
    mat = fitz.Matrix(zoom, zoom)
    total = len(pages)
    for idx, pno in enumerate(pages, 1):
        page = doc[pno - 1]
        pix = page.get_pixmap(matrix=mat)
        out = os.path.join(out_dir, "page_%04d.png" % pno)
        pix.save(out)
        sys.stderr.write("PROG %d %d\n" % (idx, total))
        sys.stderr.flush()
    doc.close()
    print(json.dumps({"ok": True, "count": total}))


def main():
    if len(sys.argv) < 3:
        sys.stderr.write("usage: pdf_helper.py count|render ...\n")
        sys.exit(2)
    cmd = sys.argv[1]
    if cmd == "count":
        do_count(sys.argv[2])
    elif cmd == "render":
        pdf_path = sys.argv[2]
        out_dir = sys.argv[3]
        pages = json.loads(sys.argv[4])
        dpi = sys.argv[5] if len(sys.argv) > 5 else "200"
        do_render(pdf_path, out_dir, pages, float(dpi))
    else:
        sys.stderr.write("unknown cmd: %s\n" % cmd)
        sys.exit(2)


if __name__ == "__main__":
    main()
