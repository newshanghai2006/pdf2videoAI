# -*- coding: utf-8 -*-
"""AI 图像生成器：根据场景提示词，用可配置的 AI 模型生成全新彩色画面。

兼容：
- gpt-image-1 系列（返回 b64_json，尺寸 1024x1024 / 1536x1024 / 1024x1536）
- dall-e-3 / dall-e-2（支持 quality / response_format，尺寸 1024x1024 / 1792x1024 / 1024x1792）
- 其它任意 OpenAI 兼容图像模型（自动去除不支持的参数）
"""
import os
import time
import base64
import urllib.request
import urllib.error
import json
import re
import mimetypes
from openai import OpenAI
from PIL import Image

from config import OPENAI_API_KEY, OPENAI_BASE_URL, IMAGE_MODEL, IMAGE_QUALITY
from .prompt_optimizer import optimize_chinese_visual_prompt
from .rate_limiter import get_agnes_image_limiter, nvidia_limiter


def get_client(api_key=None, base_url=None):
    """获取 OpenAI 客户端"""
    key = (api_key or OPENAI_API_KEY or "").strip()
    url = (base_url or OPENAI_BASE_URL or "").strip().rstrip("/")
    if not key:
        raise ValueError("未配置 OpenAI API Key")
    return OpenAI(api_key=key, base_url=url)


def _download(url, output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=60) as resp:
        with open(output_path, 'wb') as f:
            f.write(resp.read())


def _is_nvidia(base_url, api_key):
    return ("nvidia" in (base_url or "").lower()) or (api_key or "").startswith("nvapi-")


def _is_agnes(base_url, model=None):
    return ("agnes-ai.com" in (base_url or "").lower()
            or (model or "").lower().startswith("agnes-image-"))


def _agnes_images_endpoint(base_url):
    base = (base_url or "https://apihub.agnes-ai.com/v1").strip().rstrip("/")
    if base.endswith("/images/generations"):
        return base
    if base.endswith("/v1"):
        return base + "/images/generations"
    return base + "/v1/images/generations"


def _image_data_uri(path):
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _agnes_generate(prompt, output_path, api_key, base_url, model,
                    orientation="landscape", size_tier="1K", input_paths=None):
    """调用 Agnes Image 2.0 Flash，支持文生图和 Data URI 图生图。"""
    tier = str(size_tier or "1K").upper()
    if tier not in ("1K", "2K", "3K", "4K"):
        raise ValueError("Agnes 图片尺寸档位必须是 1K、2K、3K 或 4K")
    ratio = "9:16" if orientation == "portrait" else "16:9"
    payload = {
        "model": model,
        "prompt": prompt,
        "size": tier,
        "ratio": ratio,
        "extra_body": {"response_format": "b64_json"},
    }
    if input_paths:
        payload["extra_body"]["image"] = [_image_data_uri(path) for path in input_paths]

    request = urllib.request.Request(
        _agnes_images_endpoint(base_url),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        get_agnes_image_limiter(tier).wait()
        with urllib.request.urlopen(request, timeout=360) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Agnes 图像模型 '{model}' 调用失败: HTTP {error.code}: {detail}"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Agnes 图像模型 '{model}' 连接失败: {error}") from error

    items = result.get("data") or []
    if not items:
        raise RuntimeError(f"Agnes 图像接口未返回 data[0]: {result}")
    item = items[0]
    tmp_path = output_path + ".raw"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    if item.get("b64_json"):
        with open(tmp_path, "wb") as handle:
            handle.write(base64.b64decode(item["b64_json"]))
    elif item.get("url"):
        _download(item["url"], tmp_path)
    else:
        raise RuntimeError(f"Agnes 图像接口未返回 data[0].url/b64_json: {result}")
    try:
        _flatten_to_rgb(tmp_path, output_path)
    finally:
        _safe_remove_file(tmp_path)
    return output_path


def _safe_historical_prompt(prompt):
    """将可能触发过滤的战争描述改写为非血腥、适合全年龄的历史场景。"""
    replacements = {
        r"\b(blood|bloody|gore|gory)\b": "",
        r"\b(kill(?:ed|ing)?|murder(?:ed|ing)?)\b": "defeated",
        r"\b(dead|death|corpse|bodies)\b": "fallen banners",
        r"\b(wound(?:ed|s|ing)?|injur(?:y|ed|ies))\b": "exhausted",
        r"\b(behead(?:ed|ing)?|decapitat(?:ed|ion|ing))\b": "captured",
        r"\b(stab(?:bed|bing)?|slash(?:ed|ing)?)\b": "confronting",
        r"\b(weapon|sword|spear|arrow|gun)\b": "historical ceremonial equipment",
    }
    safe = prompt
    for pattern, value in replacements.items():
        safe = re.sub(pattern, value, safe, flags=re.IGNORECASE)
    safe = re.sub(r"\s+", " ", safe).strip()
    return (
        safe[:1800]
        + " Family-friendly historical illustration, non-graphic, no blood, "
          "no visible injury, no dead bodies, no explicit violence. Preserve the era, "
          "characters, location, composition and dramatic storytelling atmosphere."
    )


def _nvidia_generate(prompt, output_path, api_key, model, width=1024, height=1024):
    """NVIDIA NIM 图像生成，兼容 Flux 官方示例及 SDXL 返回格式。"""
    endpoint = f"https://ai.api.nvidia.com/v1/genai/{model}"
    payload = {"prompt": prompt, "width": width, "height": height,
               "seed": 0, "steps": 4 if "flux" in model.lower() else 25}
    if "stabilityai" in model.lower():
        payload = {"text_prompts": [{"text": prompt, "weight": 1}],
                   "cfg_scale": 5, "sampler": "K_EULER_ANCESTRAL",
                   "seed": 0, "steps": 25}
    request = urllib.request.Request(
        endpoint, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json",
                 "Content-Type": "application/json"}, method="POST")
    try:
        nvidia_limiter.wait()
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.loads(response.read().decode("utf-8"))
        encoded = data.get("image")
        if not encoded and data.get("images"):
            encoded = data["images"][0].get("image") or data["images"][0].get("base64")
        if not encoded and data.get("artifacts"):
            encoded = data["artifacts"][0].get("base64")
        if not encoded:
            raise RuntimeError(f"NVIDIA 图像接口未返回 artifacts.base64: {data}")
        raw = output_path + ".raw"
        with open(raw, "wb") as handle:
            handle.write(base64.b64decode(encoded))
        _flatten_to_rgb(raw, output_path)
        _safe_remove_file(raw)
        return output_path
    except urllib.error.HTTPError as error:
        try:
            detail = error.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(error)
        raise RuntimeError(
            f"NVIDIA 图像模型 '{model}' 调用失败: HTTP {error.code}: {detail}"
        ) from error
    except Exception as error:
        raise RuntimeError(f"NVIDIA 图像模型 '{model}' 调用失败: {error}") from error


def _flatten_to_rgb(src_path, dst_path):
    """将图片（可能是 RGBA）去透明通道后保存为纯 RGB PNG。

    AI 生成的图像（gpt-image-1 / DALL·E）常带透明背景，
    直接喂给 ffmpeg 的 libx264 会导致 get_buffer() 失败。
    """
    img = Image.open(src_path).convert("RGBA")
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    img = Image.alpha_composite(bg, img).convert("RGB")
    img.save(dst_path)


def _save_response(response, output_path):
    """从 images.generate 响应中保存图片，兼容 url 和 b64_json 两种返回形式。

    保存后立即去除透明通道（转 RGB），避免后续 ffmpeg 编码崩溃。
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    data = response.data[0]
    tmp_path = output_path + ".raw"
    if getattr(data, 'b64_json', None):
        with open(tmp_path, 'wb') as f:
            f.write(base64.b64decode(data.b64_json))
    elif getattr(data, 'url', None):
        _download(data.url, tmp_path)
    else:
        raise RuntimeError("图像响应中既没有 url 也没有 b64_json")
    _flatten_to_rgb(tmp_path, output_path)
    _safe_remove_file(tmp_path)
    return output_path


def _safe_remove_file(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _choose_sizes(model, orientation):
    """根据模型类型返回优先级排序的尺寸列表。"""
    model_l = (model or "").lower()
    if "gpt-image" in model_l:
        # gpt-image-1 支持 1024x1024 / 1536x1024 / 1024x1536
        if orientation == "portrait":
            return ["1024x1536", "1024x1024"]
        return ["1536x1024", "1024x1024"]
    if "dall-e" in model_l:
        if orientation == "portrait":
            return ["1024x1792", "1792x1024", "1024x1024"]
        return ["1792x1024", "1024x1792", "1024x1024"]
    # 未知模型：只试最通用的 1024x1024
    return ["1024x1024"]


def generate_scene_image(prompt, output_path, orientation="landscape",
                         api_key=None, base_url=None, quality=None,
                         image_model=None, image_size_tier="1K"):
    """用 AI 模型生成单张场景画面。

    Args:
        prompt: 英文画面提示词
        output_path: 输出图片路径
        orientation: landscape / portrait
        api_key: API Key
        base_url: API base URL
        quality: standard / hd（仅 DALL·E 生效）
        image_model: 图像生成模型名称（默认用 config.IMAGE_MODEL）

    Returns:
        str: 保存的图片路径
    """
    prompt = optimize_chinese_visual_prompt(prompt)
    model = image_model or IMAGE_MODEL
    resolved_key = (api_key or OPENAI_API_KEY or "").strip()
    resolved_url = (base_url or OPENAI_BASE_URL or "").strip()
    if not resolved_key:
        raise ValueError("未配置图像 API Key")
    if _is_agnes(resolved_url, model):
        return _agnes_generate(
            prompt, output_path, resolved_key, resolved_url, model,
            orientation=orientation, size_tier=image_size_tier,
        )
    if _is_nvidia(resolved_url, resolved_key):
        if orientation == "portrait":
            nvidia_width, nvidia_height = 1024, 1024
        else:
            nvidia_width, nvidia_height = 1024, 1024
        return _nvidia_generate(prompt, output_path, resolved_key, model,
                                 width=nvidia_width, height=nvidia_height)
    client = get_client(resolved_key, resolved_url)
    model_l = model.lower()

    sizes = _choose_sizes(model, orientation)
    quality_val = quality or IMAGE_QUALITY
    is_dalle = "dall-e" in model_l

    last_err = None
    for size in sizes:
        try:
            kwargs = {"model": model, "prompt": prompt, "n": 1, "size": size}
            # 仅 DALL·E 系列支持 quality / response_format 参数
            if is_dalle:
                kwargs["quality"] = quality_val
                kwargs["response_format"] = "url"
            response = client.images.generate(**kwargs)
            return _save_response(response, output_path)
        except Exception as e:
            last_err = e
            continue

    # 最终兜底：去掉所有可选参数，只给最小集合
    try:
        response = client.images.generate(
            model=model, prompt=prompt, n=1, size="1024x1024")
        return _save_response(response, output_path)
    except Exception as e:
        last_err = e

    raise RuntimeError(
        f"图像模型 '{model}' 调用失败: {last_err}。"
        f"请确认你的代理/API 支持该图像模型名称，"
        f"或在界面中改用 'gpt-image-1' / 'dall-e-3' 等。"
    )


def test_image_model(api_key=None, base_url=None, image_model=None,
                     image_size_tier="1K"):
    """快速测试图像模型是否可用，返回识别到的能力信息。

    用于前端的「连接测试」按钮，避免跑完整流程才发现模型名不对。

    Returns:
        dict: {'model', 'response_type'(url/b64_json), 'size', 'supported': True}
    """
    model = image_model or IMAGE_MODEL
    resolved_key = (api_key or OPENAI_API_KEY or "").strip()
    resolved_url = (base_url or OPENAI_BASE_URL or "").strip()
    if _is_agnes(resolved_url, model):
        import tempfile
        test_path = os.path.join(tempfile.gettempdir(), "agnes_image_test.png")
        try:
            _agnes_generate(
                "A simple red circle on a white background, test pattern.",
                test_path, resolved_key, resolved_url, model,
                orientation="landscape", size_tier=image_size_tier,
            )
            return {"model": model, "response_type": "b64_json",
                    "size": str(image_size_tier).upper(), "supported": True}
        finally:
            _safe_remove_file(test_path)
    client = get_client(resolved_key, resolved_url)
    if _is_nvidia(resolved_url, resolved_key):
        import tempfile
        test_path = os.path.join(tempfile.gettempdir(), "nvidia_image_test.png")
        try:
            _nvidia_generate("A simple red circle on a white background, test pattern.",
                             test_path, resolved_key, model, width=1024, height=1024)
            return {"model": model, "response_type": "base64", "supported": True}
        finally:
            _safe_remove_file(test_path)
    model_l = model.lower()
    is_dalle = "dall-e" in model_l

    # 用最简单的测试图：红圈
    test_prompt = "A simple red circle on a white background, test pattern."

    # 尝试多种参数组合，找到可用的那一种
    attempts = []
    if is_dalle:
        attempts = [
            {"size": "1024x1024", "quality": "standard", "response_format": "url"},
            {"size": "1024x1024"},
        ]
    elif "gpt-image" in model_l:
        attempts = [
            {"size": "1024x1024"},
        ]
    else:
        attempts = [
            {"size": "1024x1024"},
        ]

    last_err = None
    for combo in attempts:
        try:
            response = client.images.generate(model=model, prompt=test_prompt, n=1, **combo)
            data = response.data[0]
            if getattr(data, 'b64_json', None):
                return {"model": model, "response_type": "b64_json",
                        "size": combo.get("size"), "supported": True}
            if getattr(data, 'url', None):
                return {"model": model, "response_type": "url",
                        "size": combo.get("size"), "supported": True}
        except Exception as e:
            last_err = e

    raise RuntimeError(
        f"图像模型 '{model}' 测试失败: {last_err}。"
        f"请确认你的代理支持该图像模型名称。"
    )


def generate_all_scenes(scenes, output_dir, orientation="landscape",
                        api_key=None, base_url=None, quality=None,
                        image_model=None, progress_callback=None,
                        content_filter_callback=None, image_size_tier="1K"):
    """为所有场景生成 AI 画面。

    Args:
        scenes: 场景列表
        output_dir: 输出目录
        orientation: landscape / portrait
        api_key: API Key
        base_url: API base URL
        quality: standard / hd
        image_model: 图像生成模型名称
        progress_callback: 回调函数 (current, total, message, image_path)

    Returns:
        list[dict]: 更新后的场景列表（添加 image_path 字段）
    """
    total = len(scenes)
    os.makedirs(output_dir, exist_ok=True)

    for i, scene in enumerate(scenes):
        out_path = os.path.join(output_dir, f"scene_{i + 1:04d}.png")
        prompt = scene.get('image_prompt', 'A beautiful Chinese historical scene')

        if scene.get('is_pdf_cover'):
            scene['image_path'] = scene.get('source_image_path')
            if progress_callback:
                progress_callback(i + 1, total, f"PDF 封面保留原图 {i + 1}/{total}",
                                  scene.get('image_path'))
            continue

        if progress_callback:
            progress_callback(i, total, f"AI生成画面 {i + 1}/{total}", None)

        # 重试机制
        max_retries = 3
        content_filter_rewritten = False
        for attempt in range(max_retries):
            try:
                generate_scene_image(
                    prompt, out_path, orientation,
                    api_key=api_key, base_url=base_url, quality=quality,
                    image_model=image_model, image_size_tier=image_size_tier,
                )
                scene['image_path'] = out_path
                break
            except Exception as e:
                if "CONTENT_FILTERED" in str(e).upper():
                    if content_filter_callback and not content_filter_rewritten:
                        revised = content_filter_callback(scene, prompt, str(e))
                        if revised:
                            prompt = revised
                            content_filter_rewritten = True
                            scene['image_prompt_safe'] = prompt
                            continue
                    if not content_filter_rewritten:
                        content_filter_rewritten = True
                        prompt = _safe_historical_prompt(prompt)
                        scene['image_prompt_safe'] = prompt
                        if progress_callback:
                            progress_callback(i, total,
                                              f"场景 {i + 1} 被过滤，改写为安全提示词后重试",
                                              None)
                        continue
                    fallback = scene.get('source_image_path')
                    if fallback and os.path.exists(fallback):
                        scene['image_path'] = fallback
                        scene['image_generation_warning'] = '安全提示词仍被 NVIDIA 过滤，已使用 PDF 原页面'
                        if progress_callback:
                            progress_callback(i + 1, total,
                                              f"场景 {i + 1} 被内容过滤，使用 PDF 原页面",
                                              fallback)
                        break
                    raise RuntimeError(f"场景 {i + 1} 被 NVIDIA 内容过滤，且没有原页面可降级") from e
                if attempt < max_retries - 1:
                    time.sleep(3)
                else:
                    raise RuntimeError(f"场景 {i + 1} 画面生成失败: {e}")

        if progress_callback and not scene.get('image_generation_warning'):
            progress_callback(i + 1, total, f"画面生成完成 {i + 1}/{total}",
                              scene.get('image_path'))

    return scenes


def colorize_page(image_path, output_path, api_key=None, base_url=None,
                  image_model=None, style="traditional Chinese gongbi watercolor",
                  image_size_tier="1K"):
    """使用支持 images.edit 的模型给黑白页面设色，同时保留原线稿和构图。"""
    model = image_model or IMAGE_MODEL
    prompt = (
        "Colorize this black-and-white Chinese gongbi illustration. "
        "Preserve every original line, character, face, costume, speech bubble, "
        "composition and historical detail exactly. Add tasteful natural colors "
        f"in a {style} style. Do not redraw, crop, add, remove, blur or change any text."
    )
    resolved_key = (api_key or OPENAI_API_KEY or "").strip()
    resolved_url = (base_url or OPENAI_BASE_URL or "").strip()
    if _is_agnes(resolved_url, model):
        with Image.open(image_path) as source_image:
            orientation = "portrait" if source_image.height > source_image.width else "landscape"
        return _agnes_generate(
            prompt, output_path, resolved_key, resolved_url, model,
            orientation=orientation, size_tier=image_size_tier,
            input_paths=[image_path],
        )
    client = get_client(resolved_key, resolved_url)
    with open(image_path, "rb") as source:
        try:
            response = client.images.edit(
                model=model, image=source, prompt=prompt, n=1, size="1024x1024"
            )
        except Exception as error:
            raise RuntimeError(
                f"图像模型 '{model}' 不支持图片编辑或调用失败: {error}。"
                "请使用支持 images.edit 的模型/网关。"
            ) from error
    return _save_response(response, output_path)


def colorize_pages(image_paths, output_dir, api_key=None, base_url=None,
                   image_model=None, progress_callback=None,
                   image_size_tier="1K"):
    os.makedirs(output_dir, exist_ok=True)
    result = []
    total = len(image_paths)
    for index, image_path in enumerate(image_paths):
        page_number = os.path.splitext(os.path.basename(image_path))[0].split('_')[-1]
        output_path = os.path.join(output_dir, f"color_{page_number}.png")
        if progress_callback:
            progress_callback(index, total, f"美化彩色页面 {index + 1}/{total}", None)
        colorize_page(image_path, output_path, api_key=api_key, base_url=base_url,
                      image_model=image_model, image_size_tier=image_size_tier)
        result.append(output_path)
        if progress_callback:
            progress_callback(index + 1, total, f"彩色页面完成 {index + 1}/{total}", output_path)
    return result
