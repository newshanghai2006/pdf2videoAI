# -*- coding: utf-8 -*-
"""Flask 后端 - AI 影片生成系统

管线下游：PDF → 页面图片 → OCR文字 → AI剧情分析 → AI画面生成 → TTS配音 → 影片合成
"""
import os
import uuid
import json
import threading
import time
import traceback

from flask import Flask, request, jsonify, send_file, render_template, url_for
from werkzeug.utils import secure_filename

from config import (
    UPLOAD_DIR, OUTPUT_DIR, RESOLUTIONS, ART_STYLES, TTS_VOICES,
    MAX_CONTENT_LENGTH, FFMPEG_BIN, LLM_MODELS, IMAGE_MODELS,
    LLM_MODEL, IMAGE_MODEL,
    VIDEO_ENGINES, DEFAULT_VIDEO_ENGINE,
    DEFAULT_NARRATION_VOICE, DEFAULT_DIALOGUE_VOICE,
    IMAGE_API_KEY, IMAGE_BASE_URL,
    SETTINGS_FILE,
    NVIDIA_LLM_MODELS, NVIDIA_IMAGE_MODELS,
)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)


@app.route('/api/settings', methods=['GET', 'POST'])
def local_settings():
    """读取或保存本机用户配置（仅供本地单用户部署）。"""
    fields = {
        'llm_api_key', 'llm_base_url', 'llm_model',
        'image_api_key', 'image_base_url', 'image_model',
        'seedance_api_key', 'seedance_base_url', 'seedance_model',
    }
    if request.method == 'GET':
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as handle:
                saved = json.load(handle)
            return jsonify({key: str(saved.get(key, '')) for key in fields})
        except (FileNotFoundError, json.JSONDecodeError):
            return jsonify({key: '' for key in fields})
    data = request.json or {}
    saved = {key: str(data.get(key, '') or '').strip() for key in fields}
    tmp = SETTINGS_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(saved, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_FILE)
    return jsonify({'ok': True})


# ===== 全局错误处理器：始终返回 JSON，避免 Flask 默认 HTML 错误页破坏前端 JSON 解析 =====
@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(405)
@app.errorhandler(413)
@app.errorhandler(500)
def _json_error(err):
    code = getattr(err, 'code', 500)
    # 413 表示请求体超过 MAX_CONTENT_LENGTH
    if code == 413:
        return jsonify({'error': f'文件过大，请上传小于 {MAX_CONTENT_LENGTH // 1024 // 1024}MB 的文件'}), 413
    msg = getattr(err, 'description', '服务器内部错误')
    return jsonify({'error': str(msg)}), code

# ===== 全局任务存储 =====
# task_id -> { status, phase, progress, message, result, error, scenes, ... }
_tasks = {}
_tasks_lock = threading.Lock()


def create_task():
    """创建新任务"""
    task_id = str(uuid.uuid4())[:8]
    with _tasks_lock:
        _tasks[task_id] = {
            'id': task_id,
            'status': 'pending',      # pending / running / completed / error
            'phase': '',              # 当前阶段描述
            'progress': 0,            # 0-100
            'message': '',
            'scenes': [],
            'result_path': None,
            'prompts_path': None,
            'subtitle_path': None,
            'error': None,
            'decision': None,
            'decision_event': threading.Event(),
            'created_at': time.time(),
        }
    return task_id


def update_task(task_id, **kwargs):
    """更新任务状态"""
    with _tasks_lock:
        if task_id in _tasks:
            _tasks[task_id].update(kwargs)


def get_task(task_id):
    """获取任务状态"""
    with _tasks_lock:
        return _tasks.get(task_id, {}).copy()


def wait_for_decision(task_id, stage, error, prompt='', timeout=3600):
    """暂停后台任务，等待前端选择 continue 或 abort。"""
    with _tasks_lock:
        task = _tasks[task_id]
        event = task['decision_event']
        event.clear()
        task.update(status='waiting_user', decision=None, decision_stage=stage,
                    decision_prompt=prompt, error=str(error), message=f'{stage}失败，请选择继续或退出')
    if not event.wait(timeout):
        raise RuntimeError(f'{stage}失败且等待用户决定超时: {error}')
    with _tasks_lock:
        decision = _tasks[task_id].get('decision')
        decision_prompt = _tasks[task_id].get('decision_prompt', '')
        _tasks[task_id]['status'] = 'running'
        _tasks[task_id]['error'] = None
    if decision != 'continue':
        raise RuntimeError(f'用户已终止任务（{stage}失败: {error}）')
    return decision_prompt


def _manual_scenes(ocr_results, pages_per_segment=1, duration=5.0,
                   narration_lines=None, segment_durations=None):
    """无 AI 模式：按页分段，将 OCR 文本直接作为伴读内容。"""
    pages_per_segment = max(1, int(pages_per_segment or 1))
    narration_lines = narration_lines or []
    scenes = []
    for start in range(0, len(ocr_results), pages_per_segment):
        group = ocr_results[start:start + pages_per_segment]
        text = "\n".join(x.get('text', '').strip() for x in group).strip()
        idx = len(scenes)
        narration = narration_lines[idx].strip() if idx < len(narration_lines) and narration_lines[idx].strip() else text
        scene_duration = float(segment_durations[idx]) if segment_durations and idx < len(segment_durations) and segment_durations[idx] else float(duration)
        scenes.append({'scene_number': idx + 1, 'page_source': group[0]['page_num'],
                       'narration': narration, 'dialogue': [], 'image_prompt': '',
                       'mood': 'calm', 'duration': max(1.0, scene_duration)})
    return scenes


# ===== 核心管线 =====
def run_pipeline(task_id, pdf_path, config):
    """在后台线程中运行完整 AI 影片生成管线。

    config keys:
        api_key, base_url, art_style, resolution, orientation,
        start_page, end_page, use_tts, tts_voice, tts_rate,
        bgm_path, bgm_volume
    """
    try:
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

        from core.pdf_processor import extract_pages
        from core.ocr_processor import ocr_pages
        from core.story_analyzer import analyze_story
        from core.image_generator import generate_all_scenes
        from core.image_generator import colorize_pages
        from core.tts_engine import generate_scene_narrations
        from core.video_builder import build_film
        from core.video_prompt import build_prompts_document
        from core.subtitle_builder import build_srt
        from config import ART_STYLES

        api_key = config.get('llm_api_key') or config.get('api_key', '')
        base_url = config.get('llm_base_url') or config.get('base_url', '')
        image_api_key = config.get('image_api_key') or IMAGE_API_KEY or api_key
        image_base_url = config.get('image_base_url') or IMAGE_BASE_URL or base_url
        art_style = config.get('art_style', 'cinematic')
        resolution = config.get('resolution', '1080p_land')
        orientation = config.get('orientation', 'landscape')
        start_page = config.get('start_page', 1)
        end_page = config.get('end_page', None)
        page_selection = config.get('page_selection', '')
        use_ai_analysis = config.get('use_ai_analysis', True)
        ocr_language = config.get('ocr_language', 'ch')
        if ocr_language == 'chinese_cht':
            # RapidOCR 中文模型同时覆盖繁体字；避免传入不支持的语言标识。
            ocr_language = 'ch'
        use_tts = config.get('use_tts', True)
        use_image_generation = config.get('use_image_generation', False)
        colorize_pages_enabled = config.get('colorize_pages', False)
        tts_voice = config.get('tts_voice', 'zh-CN-YunxiNeural')
        dialogue_voice = config.get('dialogue_voice', DEFAULT_DIALOGUE_VOICE)
        tts_rate = config.get('tts_rate', '+0%')
        bgm_path = config.get('bgm_path', '')
        bgm_volume = config.get('bgm_volume', 0.15)
        llm_model = config.get('llm_model', '')
        image_model = config.get('image_model', '')
        video_engine = config.get('video_engine', DEFAULT_VIDEO_ENGINE)
        export_prompts = config.get('export_prompts', False)
        cover_mode = config.get('cover_mode', 'none')
        cover_path = config.get('cover_path', '')
        cover_duration = max(1.0, float(config.get('cover_duration', 3) or 3))

        width, height = RESOLUTIONS.get(resolution, (1920, 1080))

        # 工作目录
        work_dir = os.path.join(OUTPUT_DIR, task_id)
        os.makedirs(work_dir, exist_ok=True)

        # ===== 阶段1: 提取PDF页面 =====
        update_task(task_id, status='running', phase='extract', progress=2,
                    message='正在提取PDF页面...')
        pages_dir = os.path.join(work_dir, 'pages')
        page_images = extract_pages(
            pdf_path, pages_dir, dpi=200,
            start_page=start_page, end_page=end_page,
            page_selection=page_selection,
            progress_callback=lambda c, t, m: update_task(
                task_id, progress=2 + int(c / t * 8), message=m)
        )

        # ===== 阶段2: OCR 文字识别 =====
        update_task(task_id, phase='ocr', progress=12,
                    message='正在OCR识别文字...')
        ocr_results = ocr_pages(
            page_images,
            language=ocr_language,
            progress_callback=lambda c, t, m: update_task(
                task_id, progress=12 + int(c / t * 13), message=m)
        )

        # ===== 阶段3: AI 剧情分析或手动分段 =====
        update_task(task_id, phase='analyze', progress=28,
                    message='AI正在理解剧情和台词...' if use_ai_analysis else '正在按手动参数组织场景...')
        if use_ai_analysis:
            try:
                story = analyze_story(ocr_results, art_style=art_style, api_key=api_key,
                                      base_url=base_url, llm_model=llm_model or None,
                                      progress_callback=lambda c, t, m: update_task(
                                          task_id, progress=28 + int(c / (t or 1) * 7), message=m))
            except Exception as error:
                wait_for_decision(task_id, 'AI 理解', error)
                story = {'title': 'AI 分析降级', 'scenes': _manual_scenes(
                    ocr_results, config.get('pages_per_segment', 1),
                    config.get('manual_duration', 5))}
        else:
            lines = config.get('manual_narration', '').splitlines()
            durations = [x.strip() for x in config.get('manual_durations', '').split(',') if x.strip()]
            story = {'title': '手动分段', 'scenes': _manual_scenes(
                ocr_results, config.get('pages_per_segment', 1),
                config.get('manual_duration', 5), lines, durations)}

        scenes = story.get('scenes', [])
        update_task(task_id, scenes=scenes, progress=36,
                    message=f'剧情分析完成，共{len(scenes)}个场景')

        # ===== 可选：导出 AI 视频工具提示词（本地拼装，零额外成本）=====
        # 放在画面生成之前：即使后续步骤失败，提示词也已产出可下载。
        if export_prompts and scenes:
            try:
                doc = build_prompts_document(
                    scenes,
                    title=story.get('title', ''),
                    art_style_desc=ART_STYLES.get(art_style, ''),
                    engine_hint='火山 Seedance / 即梦 / 可灵 / Runway 等',
                )
                prompts_path = os.path.join(work_dir, 'video_prompts.txt')
                with open(prompts_path, 'w', encoding='utf-8') as f:
                    f.write(doc)
                update_task(task_id, prompts_path=prompts_path,
                            message='已生成AI视频提示词，可在完成后下载')
            except Exception as _pe:
                # 提示词导出失败不应影响主流程
                update_task(task_id, message=f'提示词导出跳过: {_pe}')

        # ===== 阶段4: AI 画面生成（可选；关闭时直接使用 PDF 原页面） =====
        update_task(task_id, phase='generate', progress=38,
                    message='正在准备视频画面...')
        for scene_index, scene in enumerate(scenes):
            source_index = int(scene.get('page_source') or 0) - 1
            if source_index < 0 or source_index >= len(page_images):
                source_index = scene_index % len(page_images)
            scene['source_image_path'] = page_images[source_index]
        if colorize_pages_enabled:
            color_dir = os.path.join(work_dir, 'color_pages')
            colored_pages = colorize_pages(
                page_images, color_dir, api_key=image_api_key,
                base_url=image_base_url, image_model=image_model or None,
                progress_callback=lambda c, t, m, img: update_task(
                    task_id, progress=38 + int(c / (t or 1) * 35),
                    message=m, scenes=scenes)
            )
            page_images = colored_pages
        elif use_image_generation:
            scenes_dir = os.path.join(work_dir, 'scenes')
            try:
                    scenes = generate_all_scenes(
                    scenes, scenes_dir, orientation=orientation,
                    api_key=image_api_key, base_url=image_base_url,
                    image_model=image_model or None,
                        progress_callback=lambda c, t, m, img: update_task(
                            task_id, progress=38 + int(c / (t or 1) * 35),
                            message=m, scenes=scenes),
                        content_filter_callback=lambda scene, prompt, error: wait_for_decision(
                            task_id, 'AI 生图提示词被过滤', error, prompt)
                    )
            except Exception as error:
                wait_for_decision(task_id, 'AI 生图', error)
                for scene in scenes:
                    scene['image_path'] = scene.get('image_path') or scene.get('source_image_path')
                update_task(task_id, message='AI 生图已降级，未完成场景使用 PDF 原页面')
        else:
            page_by_num = {
                int(os.path.basename(path).split('_')[-1].split('.')[0]): path
                for path in page_images
            }
            source_numbers = [int(s.get('page_source') or 0) for s in scenes]
            repeated_single_source = (
                len(scenes) > 1 and len(set(source_numbers)) == 1
                and len(page_images) > 1
            )
            # LLM 可能省略 page_source 或错误地重复返回同一页；
            # 原页面模式下检测到这种情况时按场景顺序分配。
            for scene_index, scene in enumerate(scenes):
                page_num = int(scene.get('page_source') or 0)
                sequential_path = page_images[scene_index % len(page_images)]
                scene['image_path'] = (
                    sequential_path if repeated_single_source
                    else page_by_num.get(page_num) or sequential_path
                )
            update_task(task_id, message='已跳过 AI 画面生成，使用 PDF 原页面')

        # ===== 可选片头封面 =====
        if cover_mode in ('ai', 'upload'):
            cover_image = cover_path
            if cover_mode == 'ai':
                from core.image_generator import generate_scene_image
                cover_image = os.path.join(work_dir, 'cover.png')
                cover_title = str(story.get('title') or '历史连环画').strip()
                cover_prompt = (
                    "A striking cinematic cover image for a Chinese historical comic video, "
                    "high visual impact, dramatic lighting, clear central characters, bold composition, "
                    "rich traditional color, intriguing mystery, polished theatrical poster art. "
                    f"Add a large, elegant, highly legible Chinese title text: '{cover_title}'. "
                    "Use strong title contrast and professional poster typography, with a small subtitle area. "
                    "No graphic violence, suitable for a general audience."
                )
                try:
                    generate_scene_image(cover_prompt, cover_image, orientation=orientation,
                                         api_key=image_api_key, base_url=image_base_url,
                                         image_model=image_model or None)
                except Exception as cover_error:
                    if 'CONTENT_FILTERED' in str(cover_error).upper():
                        cover_image = ''
                        update_task(task_id, message='AI 封面被内容过滤，已跳过封面并继续正文视频')
                    else:
                        raise
            if cover_image and os.path.exists(cover_image):
                scenes.insert(0, {'scene_number': 0, 'page_source': 0,
                                  'narration': '', 'dialogue': [], 'duration': cover_duration,
                                  'image_path': cover_image, 'is_cover': True})
                update_task(task_id, scenes=scenes, message='片头封面已加入影片')
        update_task(task_id, scenes=scenes, progress=73,
                    message='全部画面生成完成')

        # ===== 阶段5: TTS 配音 =====
        tts_ok = False
        if use_tts:
            update_task(task_id, phase='tts', progress=75,
                        message='正在生成旁白配音...')
            audio_dir = os.path.join(work_dir, 'audio')
            scenes = generate_scene_narrations(
                scenes, audio_dir, voice=tts_voice,
                dialogue_voice=dialogue_voice, rate=tts_rate,
                progress_callback=lambda c, t, m: update_task(
                    task_id, progress=75 + int(c / (t or 1) * 10),
                    message=m, scenes=scenes)
            )
            # 检查 TTS 是否成功
            tts_ok = any(s.get('audio_path') for s in scenes)
            if tts_ok:
                update_task(task_id, scenes=scenes, progress=85,
                            message='配音生成完成')
            else:
                update_task(task_id, scenes=scenes, progress=85,
                            message='TTS不可用，将生成无声影片')
        else:
            update_task(task_id, progress=85, message='跳过配音')

        # 字幕时间轴与影片使用相同的场景时长规则。
        subtitle_path = os.path.join(work_dir, 'subtitles.srt')
        build_srt(scenes, subtitle_path, use_tts=tts_ok)
        update_task(task_id, subtitle_path=subtitle_path)

        # ===== 阶段6: 影片合成 =====
        update_task(task_id, phase='build', progress=87,
                    message='正在合成影片...')
        video_output = os.path.join(work_dir, 'final_film.mp4')

        # 处理 BGM
        bgm_file = bgm_path if bgm_path and os.path.exists(bgm_path) else None

        build_film(
            scenes, video_output, width, height,
            bgm_path=bgm_file, bgm_volume=bgm_volume,
            use_tts=tts_ok, video_engine=video_engine,
            engine_opts={
                'api_key': config.get('seedance_api_key', ''),
                'base_url': config.get('seedance_base_url', ''),
                'model': config.get('seedance_model', ''),
            },
            progress_callback=lambda pct, msg: update_task(
                task_id, progress=87 + int(pct / 100 * 13),
                message=msg)
        )

        # 完成
        update_task(
            task_id, status='completed', phase='done', progress=100,
            message='影片生成完成！', result_path=video_output,
            scenes=scenes
        )

    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        update_task(task_id, status='error', error=error_msg,
                    message=f'处理失败: {error_msg}')
        traceback.print_exc()


# ===== API 路由 =====

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/config')
def get_config():
    """返回可用配置选项"""
    return jsonify({
        'resolutions': {k: list(v) for k, v in RESOLUTIONS.items()},
        'art_styles': ART_STYLES,
        'tts_voices': TTS_VOICES,
        'llm_models': LLM_MODELS,
        'image_models': IMAGE_MODELS,
        'nvidia_llm_models': NVIDIA_LLM_MODELS,
        'nvidia_image_models': NVIDIA_IMAGE_MODELS,
        'default_llm_model': LLM_MODEL,
        'default_image_model': IMAGE_MODEL,
        'video_engines': VIDEO_ENGINES,
        'default_video_engine': DEFAULT_VIDEO_ENGINE,
        'default_narration_voice': DEFAULT_NARRATION_VOICE,
        'default_dialogue_voice': DEFAULT_DIALOGUE_VOICE,
    })


@app.route('/api/test', methods=['POST'])
def test_connection():
    """连接测试：快速验证 LLM 与图像模型是否可用，避免跑完整流程才发现模型名不对。"""
    data = request.json or {}
    api_key = data.get('llm_api_key', '').strip() or data.get('api_key', '').strip()
    base_url = data.get('llm_base_url', '').strip() or data.get('base_url', '').strip()
    image_api_key = data.get('image_api_key', '').strip() or IMAGE_API_KEY or api_key
    image_base_url = data.get('image_base_url', '').strip() or IMAGE_BASE_URL or base_url
    llm_model = data.get('llm_model', '').strip() or LLM_MODEL
    image_model = data.get('image_model', '').strip() or IMAGE_MODEL
    use_image_generation = bool(data.get('use_image_generation', False))
    colorize_pages_enabled = bool(data.get('colorize_pages', False))

    result = {'llm': None, 'image': None}

    # ===== 测试 LLM（可选） =====
    if not bool(data.get('use_ai_analysis', True)):
        result['llm'] = {'ok': True, 'skipped': True, 'model': llm_model,
                         'reply': '已跳过 AI 分析'}
    else:
      try:
        from core.story_analyzer import get_client, _chat
        client = get_client(api_key, base_url)
        reply = _chat(
            client, llm_model,
            messages=[{"role": "user", "content": "请用一句话回复：连接正常。"}],
            temperature=0.3, max_tokens=20,
        )
        reply = reply.strip()
        result['llm'] = {'ok': True, 'model': llm_model, 'reply': reply}
      except Exception as e:
        result['llm'] = {'ok': False, 'model': llm_model, 'error': str(e)}

    # ===== 测试图像模型（可选） =====
    if not use_image_generation and not colorize_pages_enabled:
        result['image'] = {'ok': True, 'skipped': True, 'model': image_model,
                           'info': {'message': '已跳过图像模型测试'}}
    else:
      try:
        from core.image_generator import test_image_model
        info = test_image_model(image_api_key, image_base_url, image_model)
        result['image'] = {'ok': True, 'model': image_model, 'info': info}
      except Exception as e:
        result['image'] = {'ok': False, 'model': image_model, 'error': str(e)}

    return jsonify(result)


@app.route('/api/upload', methods=['POST'])
def upload_pdf():
    """上传 PDF 文件"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': '没有文件'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': '未选择文件'}), 400

        if not file.filename.lower().endswith('.pdf'):
            return jsonify({'error': '仅支持 PDF 文件'}), 400

        filename = secure_filename(file.filename)
        if not filename:
            filename = 'upload.pdf'
        file_id = str(uuid.uuid4())[:8]
        save_path = os.path.join(UPLOAD_DIR, f"{file_id}_{filename}")
        file.save(save_path)

        # 获取页数
        from core.pdf_processor import get_page_count
        page_count = get_page_count(save_path)

        return jsonify({
            'file_id': file_id,
            'filename': filename,
            'path': save_path,
            'page_count': page_count,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': f'上传处理失败: {e}'}), 500


@app.route('/api/process', methods=['POST'])
def start_process():
    """启动影片生成管线"""
    data = request.json or {}

    pdf_path = data.get('pdf_path', '')
    if not pdf_path or not os.path.exists(pdf_path):
        return jsonify({'error': 'PDF文件不存在'}), 400

    api_key = data.get('llm_api_key', '').strip() or data.get('api_key', '').strip()
    use_ai_analysis = bool(data.get('use_ai_analysis', True))
    if use_ai_analysis and not api_key:
        return jsonify({'error': '请填写 OpenAI API Key'}), 400

    task_id = create_task()

    config = {
        'api_key': api_key,
        'base_url': data.get('base_url', '').strip(),
        'llm_api_key': api_key,
        'llm_base_url': data.get('llm_base_url', '').strip() or data.get('base_url', '').strip(),
        'image_api_key': data.get('image_api_key', '').strip(),
        'image_base_url': data.get('image_base_url', '').strip(),
        'llm_model': data.get('llm_model', '').strip(),
        'image_model': data.get('image_model', '').strip(),
        'use_image_generation': bool(data.get('use_image_generation', False)),
        'colorize_pages': bool(data.get('colorize_pages', False)),
        'art_style': data.get('art_style', 'cinematic'),
        'resolution': data.get('resolution', '1080p_land'),
        'orientation': data.get('orientation', 'landscape'),
        'start_page': int(data.get('start_page', 1)),
        'end_page': int(data.get('end_page', 0)) or None,
        'page_selection': data.get('page_selection', '').strip(),
        'use_ai_analysis': use_ai_analysis,
        'ocr_language': data.get('ocr_language', 'ch').strip(),
        'pages_per_segment': int(data.get('pages_per_segment', 1) or 1),
        'manual_duration': float(data.get('manual_duration', 5) or 5),
        'manual_durations': data.get('manual_durations', '').strip(),
        'manual_narration': data.get('manual_narration', ''),
        'cover_mode': data.get('cover_mode', 'none').strip(),
        'cover_path': data.get('cover_path', '').strip(),
        'cover_duration': float(data.get('cover_duration', 3) or 3),
        'use_tts': data.get('use_tts', True),
        'tts_voice': data.get('tts_voice', 'zh-CN-YunxiNeural'),
        'dialogue_voice': data.get('dialogue_voice', '').strip() or DEFAULT_DIALOGUE_VOICE,
        'tts_rate': data.get('tts_rate', '+0%'),
        'bgm_path': data.get('bgm_path', ''),
        'bgm_volume': float(data.get('bgm_volume', 0.15)),
        'video_engine': data.get('video_engine', '').strip() or DEFAULT_VIDEO_ENGINE,
        'export_prompts': bool(data.get('export_prompts', False)),
        'seedance_api_key': data.get('seedance_api_key', '').strip(),
        'seedance_base_url': data.get('seedance_base_url', '').strip(),
        'seedance_model': data.get('seedance_model', '').strip(),
    }

    # 启动后台线程
    thread = threading.Thread(target=run_pipeline, args=(task_id, pdf_path, config))
    thread.daemon = True
    thread.start()

    return jsonify({'task_id': task_id})


@app.route('/api/progress/<task_id>')
def get_progress(task_id):
    """获取任务进度"""
    task = get_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    return jsonify({
        'id': task.get('id'),
        'status': task.get('status'),
        'phase': task.get('phase'),
        'progress': task.get('progress', 0),
        'message': task.get('message', ''),
        'error': task.get('error'),
        'scenes': task.get('scenes', []),
        'has_prompts': bool(task.get('prompts_path')),
        'has_subtitles': bool(task.get('subtitle_path')),
        'decision_stage': task.get('decision_stage'),
        'decision_prompt': task.get('decision_prompt', ''),
    })


@app.route('/api/decision/<task_id>', methods=['POST'])
def task_decision(task_id):
    decision = (request.json or {}).get('decision')
    if decision not in ('continue', 'abort'):
        return jsonify({'error': 'decision 必须是 continue 或 abort'}), 400
    with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            return jsonify({'error': '任务不存在'}), 404
        if task.get('status') != 'waiting_user':
            return jsonify({'error': '任务当前不需要用户决定'}), 409
        task['decision'] = decision
        task['decision_prompt'] = (request.json or {}).get('prompt', '').strip()
        task['decision_event'].set()
    return jsonify({'ok': True})


@app.route('/api/scene_image/<task_id>/<int:scene_idx>')
def get_scene_image(task_id, scene_idx):
    """获取场景生成的 AI 图片"""
    task = get_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    scenes = task.get('scenes', [])
    if scene_idx < 0 or scene_idx >= len(scenes):
        return jsonify({'error': '场景不存在'}), 404

    image_path = scenes[scene_idx].get('image_path')
    if not image_path or not os.path.exists(image_path):
        return jsonify({'error': '图片不存在'}), 404

    return send_file(image_path, mimetype='image/png')


@app.route('/api/download/<task_id>')
def download_video(task_id):
    """下载生成的影片"""
    task = get_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    video_path = task.get('result_path')
    if not video_path or not os.path.exists(video_path):
        return jsonify({'error': '影片不存在'}), 404

    return send_file(video_path, as_attachment=True,
                     download_name=f'ai_film_{task_id}.mp4')


@app.route('/api/preview/<task_id>')
def preview_video(task_id):
    """预览影片（在线播放）"""
    task = get_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    video_path = task.get('result_path')
    if not video_path or not os.path.exists(video_path):
        return jsonify({'error': '影片不存在'}), 404

    return send_file(video_path, mimetype='video/mp4')


@app.route('/api/download_prompts/<task_id>')
def download_prompts(task_id):
    """下载生成的 AI 视频工具提示词（.txt）"""
    task = get_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    prompts_path = task.get('prompts_path')
    if not prompts_path or not os.path.exists(prompts_path):
        return jsonify({'error': '提示词不存在（未开启该功能或尚未生成）'}), 404

    return send_file(prompts_path, as_attachment=True,
                     download_name=f'video_prompts_{task_id}.txt',
                     mimetype='text/plain; charset=utf-8')


@app.route('/api/download_subtitles/<task_id>')
def download_subtitles(task_id):
    """下载与影片时间轴对应的 SRT 字幕。"""
    task = get_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    subtitle_path = task.get('subtitle_path')
    if not subtitle_path or not os.path.exists(subtitle_path):
        return jsonify({'error': '字幕文件不存在'}), 404
    return send_file(subtitle_path, as_attachment=True,
                     download_name=f'subtitles_{task_id}.srt',
                     mimetype='application/x-subrip; charset=utf-8')


@app.route('/api/upload_bgm', methods=['POST'])
def upload_bgm():
    """上传背景音乐"""
    if 'file' not in request.files:
        return jsonify({'error': '没有文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '未选择文件'}), 400

    filename = secure_filename(file.filename)
    file_id = str(uuid.uuid4())[:8]
    save_path = os.path.join(UPLOAD_DIR, f"bgm_{file_id}_{filename}")
    file.save(save_path)

    return jsonify({'bgm_path': save_path, 'filename': filename})


@app.route('/api/upload_srt', methods=['POST'])
def upload_srt():
    """上传外部 SRT；前端/后续任务可使用其文本作为手动伴读输入。"""
    if 'file' not in request.files or not request.files['file'].filename.lower().endswith('.srt'):
        return jsonify({'error': '请上传 SRT 字幕文件'}), 400
    file = request.files['file']
    filename = secure_filename(file.filename) or 'subtitles.srt'
    path = os.path.join(UPLOAD_DIR, f"srt_{uuid.uuid4().hex[:8]}_{filename}")
    file.save(path)
    return jsonify({'srt_path': path, 'filename': filename})


@app.route('/api/upload_cover', methods=['POST'])
def upload_cover():
    if 'file' not in request.files:
        return jsonify({'error': '没有封面图片'}), 400
    file = request.files['file']
    if not file.filename or not file.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
        return jsonify({'error': '封面仅支持 PNG/JPG/JPEG/WEBP'}), 400
    filename = secure_filename(file.filename) or 'cover.png'
    path = os.path.join(UPLOAD_DIR, f"cover_{uuid.uuid4().hex[:8]}_{filename}")
    file.save(path)
    return jsonify({'cover_path': path, 'filename': filename})


if __name__ == '__main__':
    print("=" * 60)
    print("  AI 影片生成系统")
    print("  PDF → OCR → AI理解 → AI画面生成 → TTS配音 → 影片")
    print("=" * 60)
    # debug=False + use_reloader=False 避免子进程重复占用 5000 端口
    app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)
