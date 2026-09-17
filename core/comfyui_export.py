# -*- coding: utf-8 -*-
"""Export analyzed PDF scenes as a portable ComfyUI project package.

The workflow intentionally uses ComfyUI core image nodes plus the widely used
VideoHelperSuite ``VHS_VideoCombine`` node.  Images are shipped beside the
workflow because ComfyUI LoadImage refers to filenames in its input directory,
not to bytes embedded inside a workflow JSON file.
"""
import json
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone

from .video_prompt import build_all_scene_prompts


MAX_KEY_SCENES = 8


def _safe_name(value, fallback):
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_")
    return (name[:48] or fallback)


def _write_json(path, value):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def _copy_image(source, destination):
    if not source or not os.path.isfile(source):
        return False
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    shutil.copy2(source, destination)
    return True


def _key_scenes(scenes):
    """Use LLM-selected key scenes, then fill deterministically if needed."""
    candidates = [scene for scene in scenes if scene.get("image_path") and os.path.isfile(scene["image_path"])]
    selected = [scene for scene in candidates if scene.get("is_key_scene")]
    for scene in candidates:
        if scene not in selected:
            selected.append(scene)
        if len(selected) >= MAX_KEY_SCENES:
            break
    return selected[:MAX_KEY_SCENES]


def _node(node_id, node_type, title, pos, widgets, inputs=None, outputs=None):
    return {
        "id": node_id,
        "type": node_type,
        "pos": list(pos),
        "size": [260, 120],
        "flags": {},
        "order": node_id,
        "mode": 0,
        "inputs": inputs or [],
        "outputs": outputs or [],
        "properties": {"Node name for S&R": node_type},
        "widgets_values": widgets,
        "title": title,
    }


def _workflow(key_scenes, filenames, width, height, fps, title, manifest):
    """Return a ComfyUI UI workflow that batches selected images into an MP4."""
    nodes = []
    links = []
    next_id = 1
    last_link = 0
    image_nodes = []
    for index, (scene, filename) in enumerate(zip(key_scenes, filenames), 1):
        node_id = next_id
        next_id += 1
        nodes.append(_node(
            node_id, "LoadImage", f"Key scene {index}: PDF page {scene.get('page_source', '?')}",
            (20, 40 + (index - 1) * 150), [filename],
            outputs=[{"name": "IMAGE", "type": "IMAGE", "links": []},
                     {"name": "MASK", "type": "MASK", "links": []}],
        ))
        scale_id = next_id
        next_id += 1
        scale_input = {"name": "image", "type": "IMAGE", "link": None}
        scale_output = {"name": "IMAGE", "type": "IMAGE", "links": []}
        last_link += 1
        scale_input["link"] = last_link
        nodes[-1]["outputs"][0]["links"].append(last_link)
        links.append([last_link, node_id, 0, scale_id, 0, "IMAGE"])
        nodes.append(_node(
            scale_id, "ImageScale", f"Scale key scene {index}",
            (340, 40 + (index - 1) * 150),
            [int(width), int(height), "lanczos", "disabled"], [scale_input], [scale_output],
        ))
        repeat_id = next_id
        next_id += 1
        repeat_input = {"name": "image", "type": "IMAGE", "link": None}
        repeat_output = {"name": "IMAGE", "type": "IMAGE", "links": []}
        last_link += 1
        repeat_input["link"] = last_link
        scale_output["links"].append(last_link)
        links.append([last_link, scale_id, 0, repeat_id, 0, "IMAGE"])
        frame_count = max(1, int(round(float(scene.get("duration", 5) or 5) * max(1, int(fps)))))
        nodes.append(_node(
            repeat_id, "RepeatImageBatch", f"Hold key scene {index} ({frame_count} frames)",
            (620, 40 + (index - 1) * 150), [frame_count], [repeat_input], [repeat_output],
        ))
        image_nodes.append(repeat_id)

    # ImageBatch is a core ComfyUI node. It lets VideoHelperSuite consume all
    # selected frames as one image batch without requiring an AI video model.
    batch_node = image_nodes[0] if image_nodes else None
    for index, image_node in enumerate(image_nodes[1:], 2):
        node_id = next_id
        next_id += 1
        input_a = {"name": "image1", "type": "IMAGE", "link": None}
        input_b = {"name": "image2", "type": "IMAGE", "link": None}
        output = {"name": "IMAGE", "type": "IMAGE", "links": []}
        nodes.append(_node(node_id, "ImageBatch", f"Batch key scenes 1-{index}",
                           (900, 40 + (index - 2) * 150), [], [input_a, input_b], [output]))
        for slot, source in ((input_a, batch_node), (input_b, image_node)):
            last_link += 1
            slot["link"] = last_link
            source_node = next(item for item in nodes if item["id"] == source)
            source_node["outputs"][0]["links"].append(last_link)
            links.append([last_link, source, 0, node_id, 0, "IMAGE"])
        batch_node = node_id

    if batch_node is not None:
        video_id = next_id
        video_input = {"name": "images", "type": "IMAGE", "link": None}
        last_link += 1
        video_input["link"] = last_link
        source_node = next(item for item in nodes if item["id"] == batch_node)
        source_node["outputs"][0]["links"].append(last_link)
        links.append([last_link, batch_node, 0, video_id, 0, "IMAGE"])
        nodes.append(_node(
            video_id, "VHS_VideoCombine", "Create key-scene MP4 (VideoHelperSuite)",
            (1300, 80), [max(1, int(fps)), 0, _safe_name(title, "pdf_story"), "video/h264-mp4", False, True],
            [video_input, {"name": "audio", "type": "AUDIO", "link": None}],
            [{"name": "Filenames", "type": "VHS_FILENAMES", "links": []}],
        ))

    return {
        "last_node_id": next_id,
        "last_link_id": last_link,
        "nodes": nodes,
        "links": links,
        "groups": [],
        "config": {},
        "extra": {
            "ds": {"scale": 1, "offset": [0, 0]},
            "pdf2video": {
                "schema_version": 1,
                "title": title,
                "instructions": "Copy assets/scenes/* to ComfyUI/input/, then drag this JSON into ComfyUI. Install ComfyUI-VideoHelperSuite for VHS_VideoCombine. Scene prompts and character references are in project_manifest.json.",
                "manifest": manifest,
            },
        },
        "version": 0.4,
    }


def _character_entries(characters, key_scenes, copied_scene_names):
    entries = []
    for index, raw in enumerate(characters or [], 1):
        info = raw if isinstance(raw, dict) else {"name": str(raw)}
        name = str(info.get("name") or info.get("character") or f"character_{index}").strip()
        requested_page = info.get("reference_page") or info.get("reference_scene")
        source_scene = next((scene for scene in key_scenes
                             if str(scene.get("page_source")) == str(requested_page)), None)
        source_scene = source_scene or (key_scenes[0] if key_scenes else None)
        source_name = copied_scene_names.get(id(source_scene), "") if source_scene else ""
        entries.append({
            "name": name,
            "identity": str(info.get("identity") or ""),
            "appearance_prompt": str(info.get("appearance_prompt") or info.get("appearance") or ""),
            "consistency_prompt": str(info.get("consistency_prompt") or ""),
            "reference_scene_page": source_scene.get("page_source") if source_scene else None,
            "reference_image": f"assets/characters/{_safe_name(name, f'character_{index}')}.png" if source_name else "",
            "source_scene_image": f"assets/scenes/{source_name}" if source_name else "",
        })
    return entries


def build_comfyui_project(output_dir, story, scenes, art_style_desc, width, height,
                          fps=8):
    """Create importable ComfyUI JSON, manifest, image references, and ZIP.

    Returns paths for the JSON and ZIP. Existing processing files are only read
    and copied; no existing video pipeline artifact is changed.
    """
    project_dir = os.path.join(output_dir, "comfyui_project")
    assets_dir = os.path.join(project_dir, "assets", "scenes")
    character_dir = os.path.join(project_dir, "assets", "characters")
    os.makedirs(assets_dir, exist_ok=True)
    os.makedirs(character_dir, exist_ok=True)
    title = str((story or {}).get("title") or "PDF story").strip()
    key_scenes = _key_scenes(scenes)
    if not key_scenes:
        raise RuntimeError("没有可导出的场景图片；请先完成 PDF 页面提取或 AI 画面生成")

    copied_names = {}
    all_scene_entries = []
    for index, scene in enumerate(scenes, 1):
        image_path = scene.get("image_path") or scene.get("source_image_path")
        filename = f"scene_{index:03d}_page_{scene.get('page_source', index)}.png"
        if _copy_image(image_path, os.path.join(assets_dir, filename)):
            copied_names[id(scene)] = filename
        all_scene_entries.append({
            "scene_number": scene.get("scene_number", index),
            "page_sources": scene.get("page_sources") or [scene.get("page_source")],
            "is_key_scene": bool(scene.get("is_key_scene")),
            "duration_seconds": scene.get("duration", 5),
            "narration": scene.get("narration", ""),
            "dialogue": scene.get("dialogue") or [],
            "image_prompt": scene.get("image_prompt", ""),
            "image_file": f"assets/scenes/{filename}" if id(scene) in copied_names else "",
        })

    key_names = [copied_names[id(scene)] for scene in key_scenes if id(scene) in copied_names]
    key_scenes = [scene for scene in key_scenes if id(scene) in copied_names]
    for character in _character_entries((story or {}).get("character_bible") or (story or {}).get("characters"), key_scenes, copied_names):
        if not character["source_scene_image"]:
            continue
        source = os.path.join(project_dir, character["source_scene_image"])
        destination = os.path.join(project_dir, character["reference_image"])
        _copy_image(source, destination)

    prompts = build_all_scene_prompts(scenes, art_style_desc)
    manifest = {
        "schema_version": 1,
        "project_type": "pdf2video_comfyui_export",
        "title": title,
        "summary": str((story or {}).get("summary") or ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "canvas": {"width": int(width), "height": int(height), "fps": max(1, int(fps))},
        "requirements": {
            "comfyui_nodes": ["LoadImage", "ImageScale", "RepeatImageBatch", "ImageBatch", "VHS_VideoCombine"],
            "custom_nodes": ["ComfyUI-VideoHelperSuite (for VHS_VideoCombine)"],
        },
        "characters": _character_entries(
            (story or {}).get("character_bible") or (story or {}).get("characters"),
            key_scenes, copied_names,
        ),
        "key_scene_pages": [scene.get("page_source") for scene in key_scenes],
        "scenes": all_scene_entries,
        "video_prompts": prompts,
    }
    manifest_path = os.path.join(project_dir, "project_manifest.json")
    _write_json(manifest_path, manifest)
    workflow_path = os.path.join(project_dir, "comfyui_workflow.json")
    _write_json(workflow_path, _workflow(key_scenes, key_names, width, height, fps, title, manifest))

    guide_path = os.path.join(project_dir, "README_COMFYUI.txt")
    with open(guide_path, "w", encoding="utf-8") as handle:
        handle.write(
            "ComfyUI Project Export\n\n"
            "1. Install ComfyUI-VideoHelperSuite.\n"
            "2. Copy assets/scenes/*.png into ComfyUI/input/.\n"
            "3. Drag comfyui_workflow.json into the ComfyUI canvas.\n"
            "4. Queue Prompt to create an MP4 from the selected key-scene images.\n\n"
            "project_manifest.json contains all scene prompts, narration, character profiles, "
            "and character reference image mappings. Add the image-to-video node/model used by "
            "your own ComfyUI installation when motion generation is required.\n"
        )
    archive_path = os.path.join(output_dir, "comfyui_project.zip")
    temporary_archive = archive_path + ".part"
    with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for root, _, files in os.walk(project_dir):
            for filename in files:
                full_path = os.path.join(root, filename)
                archive.write(full_path, os.path.relpath(full_path, output_dir))
    os.replace(temporary_archive, archive_path)
    return {
        "project_dir": project_dir,
        "workflow_path": workflow_path,
        "manifest_path": manifest_path,
        "archive_path": archive_path,
    }
