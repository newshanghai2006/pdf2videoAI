# -*- coding: utf-8 -*-
"""故事分析器：用 LLM 理解 OCR 文字，拆分为场景，生成画面提示词"""
import json
import os
from openai import APIStatusError, BadRequestError, OpenAI
import httpx

from config import OPENAI_API_KEY, OPENAI_BASE_URL, LLM_MODEL, ART_STYLES
from .rate_limiter import nvidia_limiter


def get_client(api_key=None, base_url=None):
    """获取 OpenAI 兼容客户端。

    base_url 应是服务商给出的 API 根地址，例如
    https://sample.com/openapi；SDK 会在其后追加 /chat/completions。
    """
    key = (api_key or OPENAI_API_KEY or "").strip()
    url = (base_url or OPENAI_BASE_URL or "").strip().rstrip("/")
    if not key:
        raise ValueError("未配置 OpenAI API Key，请在界面中填写或在环境变量中设置 OPENAI_API_KEY")
    if not url.startswith(("http://", "https://")):
        raise ValueError("API Base URL 必须以 http:// 或 https:// 开头")
    timeout = httpx.Timeout(connect=15.0, read=300.0, write=60.0, pool=15.0)
    is_nvidia = "nvidia" in url.lower() or key.startswith("nvapi-")
    # SDK 内置重试不会经过限速器；NVIDIA 改为关闭内置重试，避免突破 RPM。
    client = OpenAI(api_key=key, base_url=url, timeout=timeout,
                    max_retries=0 if is_nvidia else 1)
    client._pdf2video_is_nvidia = is_nvidia
    return client


def _is_gpt5(model):
    return (model or "").lower().startswith(("gpt-5", "o1", "o3", "o4"))


def _unsupported_parameter(error, parameter):
    """只识别网关明确返回的参数不兼容错误，避免误重试连接故障。"""
    if not isinstance(error, BadRequestError):
        return False
    message = str(error).lower()
    return parameter.lower() in message and any(
        word in message for word in ("unsupported", "not support", "unknown", "invalid")
    )


def _collect_stream(stream):
    """收集兼容 OpenAI Chat Completions 的流式文本。"""
    parts = []
    for chunk in stream:
        if not chunk.choices:
            continue
        content = chunk.choices[0].delta.content
        if content:
            parts.append(content)
    return "".join(parts)


def _chat(client, model, messages, temperature=0.7, json_mode=False,
          max_tokens=4096):
    """稳健的 chat.completions 调用。

    兼容不同模型家族：
    - GPT-4 / 4o 使用 max_tokens
    - GPT-5 / o 系列使用 max_completion_tokens，且不强制 temperature
    - 兼容网关明确拒绝 token 参数时，再回退到另一种参数名
    所有请求使用流式响应，避免长内容在代理层等待完整响应而超时。
    """
    kwargs = {"model": model, "messages": messages, "stream": True}
    if not _is_gpt5(model):
        kwargs["temperature"] = temperature
    token_parameter = "max_completion_tokens" if _is_gpt5(model) else "max_tokens"
    kwargs[token_parameter] = max_tokens
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        if getattr(client, "_pdf2video_is_nvidia", False):
            nvidia_limiter.wait()
        return _collect_stream(client.chat.completions.create(**kwargs))
    except Exception as e:
        # 少数兼容网关虽然使用 GPT-5 名称，但只实现了旧参数。
        if _unsupported_parameter(e, token_parameter):
            kwargs.pop(token_parameter)
            fallback = "max_tokens" if token_parameter == "max_completion_tokens" else "max_completion_tokens"
            kwargs[fallback] = max_tokens
            if getattr(client, "_pdf2video_is_nvidia", False):
                nvidia_limiter.wait()
            return _collect_stream(client.chat.completions.create(**kwargs))
        raise


def _error_message(error, base_url, model):
    prefix = f"LLM '{model}' 连接失败（{base_url}）"
    if isinstance(error, APIStatusError):
        return f"{prefix}: HTTP {error.status_code} - {error.message}"
    return f"{prefix}: {type(error).__name__}: {error}"


def _normalize_batch_page_sources(scenes, batch_pages, empty_pages=None):
    """把一个 LLM 批次的场景页码绑定到该批真实 PDF 页码。

    模型有时会忽略输入中的绝对页码，在每个批次都返回 1、2、3，或者
    跳过一个页码。若直接合并这些结果，后面的图片和旁白会整体错位。
    只有一页一场景、页码有效且不重复时才保留模型结果；否则按批次
    页序重新绑定。缺少的页由上层用该页 OCR 文本新增独立场景，禁止
    把两页合并进同一张场景图片。
    """
    if not scenes or not batch_pages:
        return scenes
    empty_pages = set(empty_pages or ())

    # LLMs commonly omit scanned cover pages with no OCR text. In that case
    # the returned scene count matches the non-empty pages, so bind by the
    # non-empty page sequence and let the caller insert explicit blank scenes.
    non_empty_pages = [page for page in batch_pages if page not in empty_pages]
    if empty_pages and len(scenes) == len(non_empty_pages):
        for index, scene in enumerate(scenes):
            page = non_empty_pages[index]
            scene['page_sources'] = [page]
            scene['page_source'] = page
            scene['scene_number'] = index + 1
        return scenes

    # If the model did return one scene for every page, the input order is the
    # only reliable mapping for an empty page; clear any accidental narration
    # attached to that page below.
    if empty_pages and len(scenes) == len(batch_pages):
        for index, scene in enumerate(scenes):
            page = batch_pages[index]
            scene['page_sources'] = [page]
            scene['page_source'] = page
            scene['scene_number'] = index + 1
            if page in empty_pages:
                scene['narration'] = ''
                scene['dialogue'] = []
        return scenes

    allowed = set(batch_pages)
    claimed = []
    valid = True
    for scene in scenes:
        refs = scene.get('page_sources') or [scene.get('page_source')]
        current = []
        for value in refs:
            try:
                page = int(value)
            except (TypeError, ValueError):
                continue
            if page in allowed and page not in current:
                current.append(page)
        if len(current) != 1 or any(page in claimed for page in current):
            valid = False
            break
        claimed.extend(current)
    if valid:
        for scene in scenes:
            refs = scene.get('page_sources') or [scene.get('page_source')]
            refs = [int(value) for value in refs if str(value).lstrip('-').isdigit()]
            scene['page_sources'] = refs
            scene['page_source'] = refs[0]
        # Comic pages are a chronological source. Keep the returned scene
        # order aligned with that source even if the model reordered entries.
        scenes.sort(key=lambda item: min(item.get('page_sources') or [0]))
        for index, scene in enumerate(scenes, 1):
            scene['scene_number'] = index
        return scenes

    # Bind at most one real page to each returned scene. Any pages left over
    # remain unclaimed so app._ensure_ai_page_coverage can create independent
    # OCR-backed scenes for them instead of combining page images.
    if len(scenes) > len(batch_pages):
        del scenes[len(batch_pages):]
    for index, scene in enumerate(scenes):
        page = batch_pages[min(index, len(batch_pages) - 1)]
        scene['page_sources'] = [page]
        scene['page_source'] = page
        scene['scene_number'] = index + 1
    return scenes


def analyze_story(ocr_results, art_style="cinematic", api_key=None, base_url=None,
                  llm_model=None, progress_callback=None):
    """用 LLM 分析 OCR 文字，拆分为场景并生成画面提示词。

    Args:
        ocr_results: list[dict] - OCR 结果列表（每页一个）
        art_style: 艺术风格 key（见 config.ART_STYLES）
        api_key: OpenAI API Key
        base_url: API base URL
        llm_model: LLM 模型名称（默认用 config.LLM_MODEL）
        progress_callback: 回调函数 (current, total, message)

    Returns:
        dict: {
            'title': str,
            'summary': str,
            'characters': list[str],
            'scenes': list[dict],
        }
    """
    # 大文档一次返回全部场景容易超过输出 token 上限并产生残缺 JSON。
    # 分批分析后合并，既控制输入上下文，也控制每次结构化输出长度。
    batch_size = 8
    if len(ocr_results) > batch_size:
        batches = [ocr_results[i:i + batch_size]
                   for i in range(0, len(ocr_results), batch_size)]
        merged = {'title': '', 'summary': '', 'characters': [], 'scenes': []}
        summaries = []
        for batch_index, batch in enumerate(batches):
            if progress_callback:
                progress_callback(batch_index, len(batches),
                                  f"AI 分批分析 {batch_index + 1}/{len(batches)}")
            part = analyze_story(batch, art_style=art_style, api_key=api_key,
                                 base_url=base_url, llm_model=llm_model)
            _normalize_batch_page_sources(
                part.get('scenes', []),
                [int(item['page_num']) for item in batch],
                empty_pages={int(item['page_num']) for item in batch
                             if not str(item.get('text') or '').strip()},
            )
            if not merged['title']:
                merged['title'] = part.get('title', '')
            if part.get('summary'):
                summaries.append(part['summary'])
            for character in part.get('characters', []):
                if character not in merged['characters']:
                    merged['characters'].append(character)
            merged['scenes'].extend(part.get('scenes', []))
        for index, scene in enumerate(merged['scenes'], 1):
            scene['scene_number'] = index
        merged['summary'] = ' '.join(summaries)
        if progress_callback:
            progress_callback(len(batches), len(batches), "AI 分批分析完成")
        return merged

    if progress_callback:
        progress_callback(0, 1, "准备AI分析...")

    client = get_client(api_key, base_url)
    style_desc = ART_STYLES.get(art_style, ART_STYLES["cinematic"])
    model = llm_model or LLM_MODEL

    # 拼接所有页面的 OCR 文字
    pages_text = []
    for r in ocr_results:
        page_text = r['text'].strip()
        pages_text.append(
            f"【第{r['page_num']}页】\n"
            f"{page_text or '（本页无可识别文字，必须保留为无旁白画面）'}"
        )

    if not any(r['text'].strip() for r in ocr_results):
        raise ValueError("OCR 未识别到任何文字，无法进行AI分析")

    full_text = "\n\n".join(pages_text)

    system_prompt = f"""你是一位专业的电影编剧和视觉导演。你正在分析一部中国历史连环画（漫画）的扫描OCR文字，需要将其转化为电影剧本和分镜方案。

你的任务：
1. 阅读并理解所有OCR识别的文字（可能有不准确之处，请根据上下文推断修正）
2. 理解故事情节、角色关系、对话内容和战争场景
3. 严格按输入 PDF 页拆分场景：每一页对应一个场景，不得漏页、合并页或把一页拆成多个场景
4. 为每个场景编写旁白文字（用于配音）
5. 为每个场景生成详细的英文画面提示词（用于AI图像生成）

艺术风格要求：{style_desc}

请严格按以下 JSON 格式返回（不要包含任何其他文字）：
{{
  "title": "故事标题",
  "summary": "故事概述（1-2句话）",
  "characters": ["角色1", "角色2"],
  "scenes": [
    {{
      "scene_number": 1,
      "page_source": 1,
      "narration": "这一场景的旁白文字（中文，用于TTS配音，描述发生了什么）",
      "dialogue": ["角色名: 台词内容"],
      "image_prompt": "A detailed English prompt for AI image generation. Describe the scene vividly: characters, action, setting, lighting, composition, atmosphere. Style: {style_desc}. Make it cinematic and visually stunning.",
      "mood": "tense|calm|heroic|tragic|joyful|mysterious|epic",
      "duration": 5
    }}
  ]
}}

注意事项：
- 场景数量必须与输入页数相同，每页严格一个场景；page_source 必须填写该页输入中显示的真实页码
- image_prompt 必须是英文，描述要详细具体，包含人物外貌、动作、场景环境、光影效果
- narration 是中文旁白，用于配音，应当流畅自然，像讲故事一样
- 如果OCR文字不完整，请根据上下文和常识合理推断补充
- duration 根据场景复杂度建议3-8秒"""

    if progress_callback:
        progress_callback(0, 1, "AI正在理解剧情...")

    # 尝试带 response_format 的请求，仅在网关明确不支持该参数时降级。
    try:
        content = _chat(
            client, model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"以下是连环画的OCR识别文字：\n\n{full_text}"},
            ],
            temperature=0.7, json_mode=True,
        )
    except Exception as e:
        if not _unsupported_parameter(e, "response_format"):
            raise RuntimeError(_error_message(e, base_url or OPENAI_BASE_URL, model)) from e
        content = _chat(
            client, model,
            messages=[
                {"role": "system", "content": system_prompt + "\n\n重要：请只返回JSON，不要包含任何其他文字或markdown标记。"},
                {"role": "user", "content": f"以下是连环画的OCR识别文字：\n\n{full_text}"},
            ],
            temperature=0.7,
        )

    if progress_callback:
        progress_callback(1, 1, "AI分析完成")

    content = content.strip()
    # 去掉可能的 markdown 代码块标记
    if content.startswith('```'):
        content = content.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    try:
        result = json.loads(content)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"LLM 返回的场景 JSON 不完整或格式错误（位置 {error.pos}: {error.msg}）。"
            "请减少单次页面数量、改用输出能力更强的模型，或重试当前批次。"
        ) from error

    _normalize_batch_page_sources(
        result.get('scenes', []),
        [int(item['page_num']) for item in ocr_results],
        empty_pages={int(item['page_num']) for item in ocr_results
                     if not str(item.get('text') or '').strip()},
    )
    return result


def refine_scene_prompt(scene, art_style="cinematic", api_key=None, base_url=None,
                        llm_model=None):
    """优化单个场景的画面提示词（可选步骤）。

    Args:
        scene: 场景字典
        art_style: 艺术风格
        api_key: API Key
        base_url: API base URL
        llm_model: LLM 模型名称

    Returns:
        str: 优化后的英文提示词
    """
    client = get_client(api_key, base_url)
    style_desc = ART_STYLES.get(art_style, ART_STYLES["cinematic"])
    model = llm_model or LLM_MODEL

    content = _chat(
        client, model,
        messages=[
            {"role": "system", "content": f"You are an expert at writing image-generation prompts. Enhance the following prompt to be more vivid and specific. Style: {style_desc}. Return only the prompt text."},
            {"role": "user", "content": scene.get('image_prompt', '')},
        ],
        temperature=0.8,
    )

    return content.strip()
