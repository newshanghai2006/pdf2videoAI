# -*- coding: utf-8 -*-
"""Isolated OCR worker used to keep ONNXRuntime failures out of Flask threads."""
import json
import os
import sys

from ocr_processor import ocr_pages


def _write_json_atomic(path, payload):
    temporary = path + ".part"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    os.replace(temporary, path)


def main(request_path):
    with open(request_path, "r", encoding="utf-8") as handle:
        request = json.load(handle)
    image_paths = request.get("image_paths") or []
    progress_path = request["progress_path"]

    def report(current, total, message):
        _write_json_atomic(progress_path, {
            "current": current,
            "total": total,
            "message": message,
        })

    results = ocr_pages(
        image_paths,
        progress_callback=report,
        language=request.get("language", "ch"),
        engine=request.get("engine", "rapidocr"),
    )
    _write_json_atomic(request["result_path"], results)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: ocr_worker.py <request.json>")
    main(sys.argv[1])
