# -*- coding: utf-8 -*-
"""Flask 后端 - AI 影片生成系统

管线下游：PDF → 页面图片 → OCR文字 → AI剧情分析 → AI画面生成 → TTS配音 → 影片合成
"""
import os
import uuid
import json
import secrets
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from functools import wraps
from PIL import Image

from flask import Flask, request, jsonify, send_file, render_template, url_for, g
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from config import (
    UPLOAD_DIR, OUTPUT_DIR, RESOLUTIONS, ART_STYLES, TTS_VOICES,
    MAX_CONTENT_LENGTH, FFMPEG_BIN, LLM_MODELS, IMAGE_MODELS,
    LLM_MODEL, IMAGE_MODEL,
    VIDEO_ENGINES, DEFAULT_VIDEO_ENGINE,
    DEFAULT_NARRATION_VOICE, DEFAULT_DIALOGUE_VOICE,
    IMAGE_API_KEY, IMAGE_BASE_URL,
    SETTINGS_FILE, ENABLE_SERVER_SETTINGS,
    NVIDIA_LLM_MODELS, NVIDIA_IMAGE_MODELS,
    AGNES_BASE_URL, AGNES_LLM_MODELS, AGNES_IMAGE_MODELS, AGNES_VIDEO_MODELS,
    AGNES_IMAGE_SIZES,
    APP_DATABASE, AUTH_REQUIRED, PUBLIC_BASE_URL, TRUST_PROXY_HOPS, SESSION_TTL_HOURS,
    EMAIL_CODE_TTL_MINUTES, CAPTCHA_TTL_MINUTES, AUTH_CODE_COOLDOWN_SECONDS,
)
from core.auth_service import (
    normalize_email, send_login_code, validate_captcha, validate_captcha_id,
    validate_code,
)
from core.captcha_service import build_captcha_text, render_captcha_data_url
from core.persistence import AppStore

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
if TRUST_PROXY_HOPS:
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=TRUST_PROXY_HOPS,
        x_proto=TRUST_PROXY_HOPS,
        x_host=TRUST_PROXY_HOPS,
    )

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
store = AppStore(APP_DATABASE)
store.mark_interrupted_tasks_paused()

SESSION_COOKIE_NAME = 'pdf2video_session'
_auth_rate_lock = threading.Lock()
_auth_rate = {}


def _client_ip():
    return request.remote_addr or 'unknown'


def _rate_allowed(key, window_seconds, limit):
    now = time.time()
    with _auth_rate_lock:
        recent = [stamp for stamp in _auth_rate.get(key, [])
                  if now - stamp < window_seconds]
        recent.append(now)
        _auth_rate[key] = recent
        return len(recent) <= limit


def _session_cookie(session_id, max_age=None):
    parts = [f'{SESSION_COOKIE_NAME}={session_id}', 'Path=/', 'HttpOnly', 'SameSite=Strict']
    parts.append(f'Max-Age={SESSION_TTL_HOURS * 3600 if max_age is None else max_age}')
    if PUBLIC_BASE_URL.lower().startswith('https://'):
        parts.append('Secure')
    return '; '.join(parts)


@app.before_request
def load_authenticated_user():
    g.auth_session = None
    g.current_user = None
    if not AUTH_REQUIRED:
        user = store.login_user('local@localhost.invalid')
        g.current_user = user
        g.auth_session = {'id': 'auth-disabled', 'user_id': user['id'], 'csrf_token': ''}
        return
    session_id = request.cookies.get(SESSION_COOKIE_NAME, '')
    if session_id:
        session = store.get_session(session_id)
        if session:
            g.auth_session = session
            g.current_user = {'id': session['user_id'], 'email': session['email']}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.current_user:
            return jsonify({'error': '请先使用邮箱验证码登录'}), 401
        return view(*args, **kwargs)
    return wrapped


def csrf_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if AUTH_REQUIRED:
            supplied = request.headers.get('X-CSRF-Token', '')
            expected = (g.auth_session or {}).get('csrf_token', '')
            if not supplied or not secrets.compare_digest(supplied, expected):
                return jsonify({'error': 'CSRF 验证失败，请刷新页面后重试'}), 403
        return view(*args, **kwargs)
    return wrapped


def _owned_task(task_id):
    task = get_task(task_id)
    if not task or int(task.get('user_id') or 0) != int(g.current_user['id']):
        return None
    return task


def _user_upload_dir():
    directory = os.path.join(UPLOAD_DIR, str(g.current_user['id']))
    os.makedirs(directory, exist_ok=True)
    return directory


def _is_owned_upload(path):
    if not path:
        return False
    root = os.path.realpath(_user_upload_dir())
    candidate = os.path.realpath(path)
    try:
        return os.path.commonpath([root, candidate]) == root
    except ValueError:
        return False


@app.after_request
def add_security_headers(response):
    """为公网部署提供基础浏览器安全边界。"""
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "media-src 'self' blob:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'"
    )
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/api/settings', methods=['GET', 'POST'])
@login_required
def local_settings():
    """兼容旧版本地设置文件；公网模式默认禁止服务器端持久化。"""
    if not ENABLE_SERVER_SETTINGS:
        if request.method == 'GET':
            return jsonify({'enabled': False, 'storage': 'browser'})
        return jsonify({
            'error': '服务器端配置存储已禁用，请使用浏览器端存储模式',
            'enabled': False,
        }), 403

    if request.method == 'POST':
        supplied = request.headers.get('X-CSRF-Token', '')
        expected = (g.auth_session or {}).get('csrf_token', '')
        if AUTH_REQUIRED and (not supplied or not secrets.compare_digest(supplied, expected)):
            return jsonify({'error': 'CSRF 验证失败'}), 403

    fields = {
        'llm_api_key', 'llm_base_url', 'llm_model',
        'image_api_key', 'image_base_url', 'image_model',
        'seedance_api_key', 'seedance_base_url', 'seedance_model',
        'video_api_key', 'video_base_url', 'video_model',
    }
    if request.method == 'GET':
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as handle:
                saved = json.load(handle)
            result = {key: str(saved.get(key, '')) for key in fields}
            result['enabled'] = True
            result['storage'] = 'server'
            return jsonify(result)
        except (FileNotFoundError, json.JSONDecodeError):
            result = {key: '' for key in fields}
            result['enabled'] = True
            result['storage'] = 'server'
            return jsonify(result)
    data = request.json or {}
    saved = {key: str(data.get(key, '') or '').strip() for key in fields}
    tmp = SETTINGS_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(saved, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_FILE)
    return jsonify({'ok': True, 'enabled': True, 'storage': 'server'})


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
_running_threads = {}


class TaskPaused(Exception):
    """Cooperative stop raised after a task reaches a safe checkpoint."""


def _runtime_task(record):
    record = dict(record)
    record.setdefault('decision', None)
    record.setdefault('decision_prompt', '')
    record.setdefault('checkpoint', {})
    record.setdefault('scenes', [])
    record.setdefault('pause_requested', False)
    record['decision_event'] = threading.Event()
    return record


def create_task(user_id, pdf_path, config):
    """创建新任务"""
    task_id = uuid.uuid4().hex[:20]
    store.create_task(task_id, user_id, pdf_path, config)
    record = store.load_task(task_id)
    with _tasks_lock:
        _tasks[task_id] = _runtime_task(record)
    return task_id


def update_task(task_id, **kwargs):
    """更新任务状态"""
    snapshot = None
    with _tasks_lock:
        if task_id in _tasks:
            _tasks[task_id].update(kwargs)
            snapshot = _tasks[task_id].copy()
    if snapshot:
        store.save_task(snapshot)


def get_task(task_id):
    """获取任务状态"""
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task:
            return task.copy()
    record = store.load_task(task_id)
    if not record:
        return {}
    with _tasks_lock:
        task = _tasks.setdefault(task_id, _runtime_task(record))
        return task.copy()


def _check_pause(task_id, message='任务已暂停，可稍后继续'):
    task = get_task(task_id)
    if not task.get('pause_requested'):
        return
    update_task(task_id, status='paused', pause_requested=False,
                message=message, error=None)
    raise TaskPaused(message)


def _pipeline_progress(task_id, **kwargs):
    update_task(task_id, **kwargs)
    _check_pause(task_id)


def _set_checkpoint(task_id, name, **values):
    task = get_task(task_id)
    checkpoint = dict(task.get('checkpoint') or {})
    checkpoint[name] = True
    checkpoint.update(values)
    update_task(task_id, checkpoint=checkpoint)
    _check_pause(task_id)


def _merge_scene_artifacts(scenes, persisted_scenes):
    """Merge already generated image/audio paths into a restored scene list."""
    for index, scene in enumerate(scenes):
        if index >= len(persisted_scenes or []):
            break
        previous = persisted_scenes[index]
        for key in ('image_path', 'source_image_path', 'image_generation_warning',
                    'image_prompt_safe', 'audio_path', 'duration'):
            value = previous.get(key)
            if value not in (None, ''):
                scene[key] = value
    return scenes


def _write_json_atomic(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + '.tmp'
    with open(temporary, 'w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def _read_json(path, fallback=None):
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return fallback


def wait_for_decision(task_id, stage, error, prompt='', timeout=3600,
                      allow_retry=False, return_decision=False):
    """暂停后台任务等待用户决定；TTS 文本确认超时后自动继续。"""
    with _tasks_lock:
        task = _tasks[task_id]
        event = task['decision_event']
        event.clear()
        confirmation = stage.startswith('TTS')
        task.update(status='waiting_user', decision=None, decision_stage=stage,
                    decision_prompt=prompt,
                    decision_can_retry=bool(allow_retry),
                    error='' if confirmation else str(error),
                    message='请确认每个场景的伴读文字' if confirmation else f'{stage}失败，请选择继续或退出')
        snapshot = task.copy()
    store.save_task(snapshot)
    signaled = event.wait(timeout)
    with _tasks_lock:
        task = _tasks[task_id]
        # 浏览器可能进入后台、断网或定时器被节流。TTS 确认属于可选编辑步骤，
        # 后端超时必须以原有文字自动继续，不能让已完成的 OCR/生图工作作废。
        if not signaled and confirmation and task.get('decision') is None:
            task['decision'] = 'continue'
            task['decision_auto'] = True
            task['message'] = '伴读文本确认等待超时，已自动采用当前文字继续生成配音'
        decision = task.get('decision')
        decision_prompt = task.get('decision_prompt', '')
        if decision == 'pause':
            task['status'] = 'paused'
            task['pause_requested'] = False
            task['message'] = '任务已暂停，可稍后登录继续'
        else:
            task['status'] = 'running'
            task['error'] = None
        snapshot = task.copy()
    store.save_task(snapshot)
    if decision == 'pause':
        raise TaskPaused('任务已暂停，可稍后登录继续')
    if not signaled and not confirmation:
        raise RuntimeError(f'{stage}失败且等待用户决定超时: {error}')
    if decision != 'continue':
        if decision != 'retry' or not allow_retry:
            raise RuntimeError(f'用户已终止任务（{stage}失败: {error}）')
    if return_decision:
        return decision, decision_prompt
    return decision_prompt


def _manual_scenes(ocr_results, pages_per_segment=1, duration=5.0,
                   narration_lines=None, segment_durations=None):
    """无 AI 模式：按页分段，将 OCR 文本直接作为伴读内容。"""
    pages_per_segment = max(1, int(pages_per_segment or 1))
    narration_lines = narration_lines or []
    scenes = []
    for start in range(0, len(ocr_results), pages_per_segment):
        group = ocr_results[start:start + pages_per_segment]
        text = "".join(x.get('text', '').replace('\r', '').replace('\n', '') for x in group).strip()
        idx = len(scenes)
        narration = narration_lines[idx].strip() if idx < len(narration_lines) and narration_lines[idx].strip() else text
        narration = narration.replace('\r', '').replace('\n', '').strip()
        scene_duration = float(segment_durations[idx]) if segment_durations and idx < len(segment_durations) and segment_durations[idx] else float(duration)
        scenes.append({'scene_number': idx + 1, 'page_source': group[0]['page_num'],
                       'page_sources': [x['page_num'] for x in group],
                       'narration': narration, 'dialogue': [], 'image_prompt': '',
                       'mood': 'calm', 'duration': max(1.0, scene_duration)})
    return scenes


def _ensure_ai_page_coverage(scenes, ocr_results):
    """补齐 AI 分析偶尔漏掉的 PDF 页，并保持场景顺序。

    LLM 分批返回 JSON 时，某一批可能少生成一个场景，导致相邻场景之间
    直接从第 8 页跳到第 10 页。每个缺失页都新增独立场景，禁止把两页
    合并到同一张场景图片。
    """
    if not scenes or not ocr_results:
        return scenes, []
    page_records = {
        int(item.get('page_num')): item
        for item in ocr_results
        if str(item.get('page_num', '')).isdigit()
    }
    page_numbers = sorted(page_records)
    if not page_numbers:
        return scenes, []

    covered = set()
    for scene in scenes:
        refs = scene.get('page_sources') or [scene.get('page_source')]
        for value in refs:
            try:
                page = int(value)
            except (TypeError, ValueError):
                continue
            if page in page_records:
                covered.add(page)
    missing = [page for page in page_numbers if page not in covered]
    if not missing:
        return scenes, []

    result = list(scenes)
    for page in missing:
        record = page_records[page]
        text = str(record.get('text') or '').replace('\r', '').replace('\n', '').strip()
        fallback = {
            'scene_number': 0,
            'page_source': page,
            'page_sources': [page],
            'narration': text,
            'dialogue': [],
            'image_prompt': (
                f"A cinematic scene based on Chinese comic page {page}. "
                "Preserve the exact period, country, character nationalities, clothing or "
                "uniforms, equipment, setting and action described by the source page. "
                "Never convert a modern event into an ancient costume scene. "
                "No readable text, subtitles, logos or watermarks."
            ),
            'mood': 'calm',
            'duration': 5,
        }
        insert_at = len(result)
        for index, current in enumerate(result):
            refs = current.get('page_sources') or [current.get('page_source')]
            valid_refs = []
            for value in refs:
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    continue
                if value in page_records:
                    valid_refs.append(value)
            if valid_refs and min(valid_refs) > page:
                insert_at = index
                break
        result.insert(insert_at, fallback)

    for index, scene in enumerate(result, 1):
        scene['scene_number'] = index
    return result, missing


def _force_pdf_cover_scene(scenes, cover_record, duration=3.0):
    """把所选第一页强制变为独立、无旁白的原 PDF 封面场景。"""
    page = int(cover_record['page_num'])
    cover_scene = None
    insert_at = 0
    for index, scene in enumerate(scenes):
        refs = scene.get('page_sources') or [scene.get('page_source')]
        normalized = []
        for value in refs:
            try:
                normalized.append(int(value))
            except (TypeError, ValueError):
                continue
        if page not in normalized:
            continue
        insert_at = index
        if len(normalized) == 1:
            cover_scene = scene
        else:
            remaining = [value for value in normalized if value != page]
            scene['page_sources'] = remaining
            scene['page_source'] = remaining[0]
        break

    if cover_scene is None:
        cover_scene = {
            'scene_number': 0,
            'page_source': page,
            'page_sources': [page],
            'narration': '',
            'dialogue': [],
            'image_prompt': '',
            'mood': 'calm',
            'duration': max(1.0, float(duration or 3)),
        }
        scenes.insert(insert_at, cover_scene)

    cover_scene.update({
        'page_source': page,
        'page_sources': [page],
        'narration': '',
        'dialogue': [],
        'image_prompt': '',
        'is_pdf_cover': True,
        'duration': max(1.0, float(duration or cover_scene.get('duration', 3) or 3)),
    })
    for index, scene in enumerate(scenes, 1):
        scene['scene_number'] = index
    return scenes


def _apply_confirmed_narrations(scenes, confirmed_text):
    """按显式场景索引写回伴读文字，封面始终保持无旁白。"""
    if confirmed_text:
        try:
            edited_texts = json.loads(confirmed_text)
            if not isinstance(edited_texts, list):
                edited_texts = [confirmed_text]
        except (TypeError, json.JSONDecodeError):
            edited_texts = confirmed_text.splitlines()

        if edited_texts and all(isinstance(item, dict) for item in edited_texts):
            for item in edited_texts:
                try:
                    index = int(item.get('scene_index'))
                except (TypeError, ValueError):
                    continue
                if (0 <= index < len(scenes)
                        and not scenes[index].get('is_cover')
                        and not scenes[index].get('is_pdf_cover')):
                    scenes[index]['narration'] = str(item.get('narration') or '').strip()
        elif len(edited_texts) == len(scenes):
            for index, scene in enumerate(scenes):
                if not scene.get('is_cover') and not scene.get('is_pdf_cover'):
                    scene['narration'] = str(edited_texts[index]).strip()
        else:
            # Legacy clients may omit the leading blank cover entry.
            content_scenes = [scene for scene in scenes
                              if not scene.get('is_cover') and not scene.get('is_pdf_cover')]
            for index, scene in enumerate(content_scenes):
                if index < len(edited_texts):
                    scene['narration'] = str(edited_texts[index]).strip()

    for scene in scenes:
        if scene.get('is_cover') or scene.get('is_pdf_cover'):
            scene['narration'] = ''
    return scenes


def _combine_page_images(paths, output_path, layout='vertical'):
    """将同一视频段的多页按横向或纵向拼接为一张完整画面。"""
    images = [Image.open(path).convert('RGB') for path in paths if os.path.exists(path)]
    if not images:
        return None
    horizontal = layout == 'horizontal'
    base_size = max(image.height if horizontal else image.width for image in images)
    normalized = []
    for image in images:
        current = image.height if horizontal else image.width
        if current != base_size:
            if horizontal:
                image = image.resize((max(1, round(image.width * base_size / image.height)), base_size), Image.Resampling.LANCZOS)
            else:
                image = image.resize((base_size, max(1, round(image.height * base_size / image.width))), Image.Resampling.LANCZOS)
        normalized.append(image)
    if horizontal:
        canvas = Image.new('RGB', (sum(image.width for image in normalized), base_size), 'white')
    else:
        canvas = Image.new('RGB', (base_size, sum(image.height for image in normalized)), 'white')
    cursor = 0
    for image in normalized:
        canvas.paste(image, (cursor, 0) if horizontal else (0, cursor))
        cursor += image.width if horizontal else image.height
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    canvas.save(output_path, 'PNG')
    for image in images:
        image.close()
    return output_path


# ===== 核心管线 =====
def run_pipeline(task_id, pdf_path, config):
    """在后台线程中运行完整 AI 影片生成管线。

    config keys:
        api_key, base_url, art_style, resolution, orientation,
        start_page, end_page, use_tts, tts_voice, tts_rate,
        bgm_path, bgm_volume
    """
    try:
        task_record = get_task(task_id)
        checkpoint = dict(task_record.get('checkpoint') or {})
        persisted_scenes = list(task_record.get('scenes') or [])
        _check_pause(task_id)
        # Report module initialization before importing OCR/ONNXRuntime. On
        # incompatible Python runtimes that import can take a long time, and
        # leaving the task at 0% makes it look as though the request failed.
        update_task(task_id, status='running', phase='init', progress=1,
                    pause_requested=False, error=None,
                    message='正在加载处理组件（首次加载 OCR/ONNXRuntime 可能需要一些时间）...')
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

        from core.pdf_processor import extract_pages
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
        image_base_url = config.get('image_base_url') or IMAGE_BASE_URL or base_url
        configured_image_key = config.get('image_api_key', '')
        if configured_image_key:
            image_api_key = configured_image_key
        elif any(host in image_base_url.lower() for host in ('agnes-ai.com', 'nvidia.com')):
            image_api_key = api_key
        else:
            image_api_key = IMAGE_API_KEY or api_key
        art_style = config.get('art_style', 'cinematic')
        resolution = config.get('resolution', '1080p_land')
        orientation = config.get('orientation', 'landscape')
        start_page = config.get('start_page', 1)
        end_page = config.get('end_page', None)
        page_selection = config.get('page_selection', '')
        page_layout = config.get('page_layout', 'auto')
        if page_layout == 'auto':
            page_layout = 'horizontal' if orientation == 'landscape' else 'vertical'
        use_ai_analysis = config.get('use_ai_analysis', True)
        ocr_language = config.get('ocr_language', 'ch')
        ocr_engine = config.get('ocr_engine', 'rapidocr').strip().lower()
        if ocr_engine not in ('rapidocr', 'easyocr'):
            ocr_engine = 'rapidocr'
        if ocr_language == 'chinese_cht':
            # RapidOCR 中文模型同时覆盖繁体字；避免传入不支持的语言标识。
            ocr_language = 'ch'
        use_tts = config.get('use_tts', True)
        use_image_generation = config.get('use_image_generation', False)
        colorize_pages_enabled = config.get('colorize_pages', False)
        # 手动模式必须由页数组合控制场景边界，避免服务商预设自动开启 AI 生图
        # 后重新按单个 scene 生成画面，导致 N 页设置看起来失效。
        if not use_ai_analysis:
            use_image_generation = False
            colorize_pages_enabled = False
        tts_voice = config.get('tts_voice', 'zh-CN-YunxiNeural')
        dialogue_voice = config.get('dialogue_voice', DEFAULT_DIALOGUE_VOICE)
        tts_rate = config.get('tts_rate', '+0%')
        bgm_path = config.get('bgm_path', '')
        bgm_volume = config.get('bgm_volume', 0.15)
        llm_model = config.get('llm_model', '')
        image_model = config.get('image_model', '')
        image_size_tier = config.get('image_size_tier', '1K')
        video_engine = config.get('video_engine', DEFAULT_VIDEO_ENGINE)
        export_prompts = config.get('export_prompts', False)
        cover_mode = config.get('cover_mode', 'none')
        cover_path = config.get('cover_path', '')
        cover_duration = max(1.0, float(config.get('cover_duration', 3) or 3))
        first_page_is_cover = bool(config.get('first_page_is_cover', True))
        auto_duration_tts = config.get('auto_duration_tts', True)

        width, height = RESOLUTIONS.get(resolution, (1920, 1080))

        # 工作目录
        work_dir = os.path.join(OUTPUT_DIR, task_id)
        os.makedirs(work_dir, exist_ok=True)

        # ===== 阶段1: 提取PDF页面 =====
        update_task(task_id, status='running', phase='extract', progress=2,
                    message='正在提取PDF页面...')
        pages_dir = os.path.join(work_dir, 'pages')
        page_images = checkpoint.get('page_images') or []
        if checkpoint.get('extract') and page_images and all(
                os.path.exists(path) for path in page_images):
            update_task(task_id, progress=10, message='已从检查点复用 PDF 页面')
        else:
            page_images = extract_pages(
                pdf_path, pages_dir, dpi=200,
                start_page=start_page, end_page=end_page,
                page_selection=page_selection,
                progress_callback=lambda c, t, m: _pipeline_progress(
                    task_id, progress=2 + int(c / (t or 1) * 8), message=m)
            )
            checkpoint['extract'] = True
            checkpoint['page_images'] = page_images
            _set_checkpoint(task_id, 'extract', page_images=page_images)

        # ===== 阶段2: OCR 文字识别 =====
        update_task(task_id, phase='ocr', progress=12,
                    message='正在加载 OCR/ONNXRuntime 并识别文字...')
        ocr_checkpoint_path = os.path.join(work_dir, 'ocr_results.json')
        cached_ocr = config.get('ocr_results')
        saved_ocr = _read_json(ocr_checkpoint_path) if checkpoint.get('ocr') else None
        if saved_ocr and isinstance(saved_ocr, list):
            ocr_results = saved_ocr
            update_task(task_id, progress=25, message='已从检查点复用 OCR 结果')
        elif cached_ocr and isinstance(cached_ocr, list):
            ocr_results = cached_ocr
            update_task(task_id, progress=25, message='已复用预览阶段 OCR 结果')
        else:
            # Load RapidOCR only when OCR is actually needed. This keeps
            # manual runs with cached preview text from importing ONNXRuntime.
            from core.ocr_processor import ocr_pages
            ocr_results = ocr_pages(
                page_images,
                language=ocr_language,
                engine=ocr_engine,
                progress_callback=lambda c, t, m: _pipeline_progress(
                    task_id, progress=12 + int(c / t * 13), message=m)
            )
        if not checkpoint.get('ocr') or not os.path.exists(ocr_checkpoint_path):
            _write_json_atomic(ocr_checkpoint_path, ocr_results)
            checkpoint['ocr'] = True
            _set_checkpoint(task_id, 'ocr')

        # OCR can recognize a book title on the scanned PDF cover. When the
        # user marks the first selected page as a cover, hide that OCR text
        # from story analysis so page 2 narration cannot slide onto page 1.
        analysis_ocr_results = [dict(item) for item in ocr_results]
        if first_page_is_cover and analysis_ocr_results:
            analysis_ocr_results[0]['text'] = ''
            analysis_ocr_results[0]['blocks'] = []
        story_ocr_results = (
            analysis_ocr_results[1:]
            if first_page_is_cover and analysis_ocr_results
            else analysis_ocr_results
        )

        # ===== 阶段3: AI 剧情分析或手动分段 =====
        update_task(task_id, phase='analyze', progress=28,
                    message='AI正在理解剧情和台词...' if use_ai_analysis else '正在按手动参数组织场景...')
        story_checkpoint_path = os.path.join(work_dir, 'story.json')
        story = _read_json(story_checkpoint_path) if checkpoint.get('analyze') else None
        if story and isinstance(story.get('scenes'), list):
            restored_artifacts = [scene for scene in persisted_scenes
                                  if not scene.get('is_cover')]
            scenes = _merge_scene_artifacts(story['scenes'], restored_artifacts)
            story['scenes'] = scenes
            update_task(task_id, message='已从检查点复用剧情分析结果')
        else:
            if use_ai_analysis:
                while True:
                    try:
                        if story_ocr_results:
                            story = analyze_story(
                                story_ocr_results, art_style=art_style, api_key=api_key,
                                base_url=base_url, llm_model=llm_model or None,
                                progress_callback=lambda c, t, m: _pipeline_progress(
                                    task_id, progress=28 + int(c / (t or 1) * 7), message=m),
                            )
                        else:
                            story = {'title': 'PDF 封面', 'scenes': []}
                        break
                    except TaskPaused:
                        raise
                    except Exception as error:
                        decision, _ = wait_for_decision(
                            task_id, 'AI 理解', error,
                            allow_retry=True, return_decision=True,
                        )
                        if decision == 'retry':
                            update_task(task_id, status='running', phase='analyze', progress=28,
                                        message='正在按模型额度限制等待并重试 AI 理解...')
                            continue
                        story = {'title': 'AI 分析降级', 'scenes': _manual_scenes(
                            story_ocr_results, config.get('pages_per_segment', 1),
                            config.get('manual_duration', 5))}
                        break
            else:
                lines = config.get('manual_narration', '').splitlines()
                durations = [x.strip() for x in config.get('manual_durations', '').split(',') if x.strip()]
                story = {'title': '手动分段', 'scenes': _manual_scenes(
                    story_ocr_results, config.get('pages_per_segment', 1),
                    config.get('manual_duration', 5), lines, durations)}
                expected_segments = (len(page_images) + max(1, int(config.get('pages_per_segment', 1) or 1)) - 1) // max(1, int(config.get('pages_per_segment', 1) or 1))
                if len(story['scenes']) != expected_segments:
                    story['scenes'] = _manual_scenes(
                        story_ocr_results[:len(page_images)], config.get('pages_per_segment', 1),
                        config.get('manual_duration', 5), lines, durations)
                update_task(task_id, message=(
                    f"手动分段完成：实际提取 {len(page_images)} 页，生成 {len(story['scenes'])} 个视频段，"
                    f"每段 {max(1, int(config.get('pages_per_segment', 1) or 1))} 页"))

            scenes = story.get('scenes', [])
            if use_ai_analysis:
                scenes, missing_pages = _ensure_ai_page_coverage(scenes, story_ocr_results)
                if missing_pages:
                    story['scenes'] = scenes
                    update_task(
                        task_id,
                        message=(
                            f"AI 分析检测到缺少 PDF 页 {','.join(map(str, missing_pages))}，"
                            "已自动补齐对应场景"
                        ),
                    )
            if first_page_is_cover and analysis_ocr_results:
                _force_pdf_cover_scene(
                    scenes, analysis_ocr_results[0], duration=cover_duration,
                )
            story['scenes'] = scenes
            _write_json_atomic(story_checkpoint_path, story)
            checkpoint['analyze'] = True
            _set_checkpoint(task_id, 'analyze')
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
        page_by_num = {
            int(os.path.basename(path).split('_')[-1].split('.')[0]): path
            for path in page_images
        }
        for scene_index, scene in enumerate(scenes):
            page_num = int(scene.get('page_source') or 0)
            scene['source_image_path'] = (
                page_by_num.get(page_num)
                or page_images[scene_index % len(page_images)]
            )
        if colorize_pages_enabled:
            color_dir = os.path.join(work_dir, 'color_pages')
            while True:
                try:
                    colored_pages = colorize_pages(
                        page_images, color_dir, api_key=image_api_key,
                        base_url=image_base_url, image_model=image_model or None,
                        image_size_tier=image_size_tier,
                        progress_callback=lambda c, t, m, img: _pipeline_progress(
                            task_id, progress=38 + int(c / (t or 1) * 35),
                            message=m, scenes=scenes),
                    )
                    page_images = colored_pages
                    break
                except TaskPaused:
                    raise
                except Exception as error:
                    decision, _ = wait_for_decision(
                        task_id, 'AI 彩色美化', error,
                        allow_retry=True, return_decision=True,
                    )
                    if decision == 'retry':
                        update_task(task_id, status='running', phase='generate', progress=38,
                                    message='正在按模型额度限制等待并重试彩色美化...')
                        continue
                    update_task(task_id, message='彩色美化已跳过，继续使用 PDF 原页面')
                    break
        elif use_image_generation:
            scenes_dir = os.path.join(work_dir, 'scenes')
            while True:
                try:
                    scenes = generate_all_scenes(
                        scenes, scenes_dir, orientation=orientation,
                        api_key=image_api_key, base_url=image_base_url,
                        image_model=image_model or None,
                        image_size_tier=image_size_tier,
                        progress_callback=lambda c, t, m, img: _pipeline_progress(
                            task_id, progress=38 + int(c / (t or 1) * 35),
                            message=m, scenes=scenes),
                        content_filter_callback=lambda scene, prompt, error: wait_for_decision(
                            task_id, 'AI 生图提示词被过滤', error, prompt)
                    )
                    break
                except TaskPaused:
                    raise
                except Exception as error:
                    decision, _ = wait_for_decision(
                        task_id, 'AI 生图', error,
                        allow_retry=True, return_decision=True,
                    )
                    if decision == 'retry':
                        update_task(task_id, status='running', phase='generate', progress=38,
                                    message='正在按模型额度限制等待并重试未完成的 AI 画面...')
                        continue
                    for scene in scenes:
                        scene['image_path'] = scene.get('image_path') or scene.get('source_image_path')
                    update_task(task_id, message='AI 生图已降级，未完成场景使用 PDF 原页面')
                    break
        else:
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
                source_numbers_for_scene = scene.get('page_sources') or [page_num]
                source_paths = [page_by_num.get(n) for n in source_numbers_for_scene
                                if isinstance(n, int) and page_by_num.get(n)]
                if len(source_paths) > 1:
                    combined = os.path.join(work_dir, 'manual_pages', f'segment_{scene_index + 1:04d}.png')
                    scene['image_path'] = _combine_page_images(source_paths, combined, page_layout)
                else:
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
                cover_context = " ".join(filter(None, [
                    str(story.get('title') or '').strip(),
                    str(story.get('summary') or '').strip(),
                    *[str(item.get('image_prompt') or '').strip()
                      for item in scenes[:3]],
                ]))[:3000]
                cover_prompt = (
                    "A striking cinematic cover image for a Chinese comic video, "
                    "high visual impact, dramatic lighting, clear central characters, bold composition, "
                    "intriguing mystery, polished theatrical poster art. "
                    "Preserve the source story's exact year or period, countries, locations, character "
                    "nationalities, clothing or military uniforms, equipment and architecture. Never "
                    "convert a modern or twentieth-century story into an ancient costume drama. "
                    f"Source story context: {cover_context}. "
                    "clean poster composition with intentional empty space for a title to be added later. "
                    "Do not generate any readable text, letters, logos, captions or symbols. "
                    "No graphic violence, suitable for a general audience."
                )
                try:
                    if not os.path.exists(cover_image) or os.path.getsize(cover_image) == 0:
                        generate_scene_image(cover_prompt, cover_image, orientation=orientation,
                                             api_key=image_api_key, base_url=image_base_url,
                                             image_model=image_model or None,
                                             image_size_tier=image_size_tier)
                    _check_pause(task_id)
                except TaskPaused:
                    raise
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
            else:
                update_task(
                    task_id,
                    message=(
                        '已选择片头封面，但封面文件未生成或不存在；'
                        '本次将从 PDF 第 1 页开始，未插入封面'
                    ),
                )
        update_task(task_id, scenes=scenes, progress=73,
                    message='全部画面生成完成')
        visual_checkpoint_path = os.path.join(work_dir, 'scenes_visual.json')
        _write_json_atomic(visual_checkpoint_path, scenes)
        checkpoint['generate'] = True
        _set_checkpoint(task_id, 'generate')

        # ===== 阶段5: TTS 配音 =====
        tts_ok = False
        tts_checkpoint_path = os.path.join(work_dir, 'scenes_tts.json')
        restored_tts_scenes = _read_json(tts_checkpoint_path) if checkpoint.get('tts') else None
        if restored_tts_scenes and isinstance(restored_tts_scenes, list):
            scenes = restored_tts_scenes
            tts_ok = any(scene.get('audio_path') and os.path.exists(scene['audio_path'])
                         for scene in scenes)
            update_task(task_id, scenes=scenes, progress=85,
                        message='已从检查点复用 TTS 配音')
        elif use_tts:
            narration_prompt = '\n'.join(str(scene.get('narration') or '') for scene in scenes)
            confirmed_text = wait_for_decision(
                task_id, 'TTS 伴读文本确认', '请检查并确认每个场景的伴读文字',
                prompt=narration_prompt, timeout=180
            )
            _apply_confirmed_narrations(scenes, confirmed_text)
            update_task(task_id, phase='tts', progress=75,
                        message='正在生成旁白配音...')
            audio_dir = os.path.join(work_dir, 'audio')
            scenes = generate_scene_narrations(
                scenes, audio_dir, voice=tts_voice,
                dialogue_voice=dialogue_voice, rate=tts_rate,
                progress_callback=lambda c, t, m: _pipeline_progress(
                    task_id, progress=75 + int(c / (t or 1) * 10),
                    message=m, scenes=scenes)
            )
            # 检查 TTS 是否成功
            tts_ok = any(s.get('audio_path') for s in scenes)
            if tts_ok:
                if auto_duration_tts:
                    from core.tts_engine import get_audio_duration
                    for scene in scenes:
                        audio_path = scene.get('audio_path')
                        if audio_path and os.path.exists(audio_path):
                            audio_duration = get_audio_duration(audio_path)
                            scene['duration'] = max(float(scene.get('duration', 5) or 5), audio_duration)
                update_task(task_id, scenes=scenes, progress=85,
                            message='配音生成完成')
            else:
                update_task(task_id, scenes=scenes, progress=85,
                            message='TTS不可用，将生成无声影片')
        else:
            update_task(task_id, progress=85, message='跳过配音')
        if not restored_tts_scenes:
            _write_json_atomic(tts_checkpoint_path, scenes)
            checkpoint['tts'] = True
            _set_checkpoint(task_id, 'tts')

        # 字幕时间轴与影片使用相同的场景时长规则。
        subtitle_path = os.path.join(work_dir, 'subtitles.srt')
        build_srt(scenes, subtitle_path, use_tts=tts_ok,
                  auto_duration_tts=auto_duration_tts)
        update_task(task_id, subtitle_path=subtitle_path)
        _check_pause(task_id)

        # ===== 阶段6: 影片合成 =====
        update_task(task_id, phase='build', progress=87,
                    message='正在合成影片...')
        video_output = os.path.join(work_dir, 'final_film.mp4')

        # 处理 BGM
        bgm_file = bgm_path if bgm_path and os.path.exists(bgm_path) else None

        if checkpoint.get('build') and os.path.exists(video_output):
            update_task(task_id, progress=99, message='已从检查点复用合成影片')
        else:
            build_film(
                scenes, video_output, width, height,
                bgm_path=bgm_file, bgm_volume=bgm_volume,
                use_tts=tts_ok, video_engine=video_engine,
                engine_opts={
                    'api_key': (
                        config.get('video_api_key', '')
                        or config.get('seedance_api_key', '')
                        or (api_key if video_engine == 'agnes' else '')
                    ),
                    'base_url': (
                        config.get('video_base_url', '')
                        or config.get('seedance_base_url', '')
                        or (AGNES_BASE_URL if video_engine == 'agnes' else '')
                    ),
                    'model': (
                        config.get('video_model', '')
                        or config.get('seedance_model', '')
                        or ('agnes-video-v2.0' if video_engine == 'agnes' else '')
                    ),
                    'resolution_tier': config.get('video_resolution_tier', '720p'),
                    'frame_rate': config.get('video_frame_rate', 24),
                },
                progress_callback=lambda pct, msg: update_task(
                    task_id, progress=87 + int(pct / 100 * 13),
                    message=msg),
                auto_duration_tts=auto_duration_tts,
            )
            checkpoint['build'] = True
            _set_checkpoint(task_id, 'build')

        # 完成
        update_task(
            task_id, status='completed', phase='done', progress=100,
            message='影片生成完成！', result_path=video_output,
            scenes=scenes
        )

    except TaskPaused:
        # _check_pause / wait_for_decision already persisted the paused state.
        pass
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        update_task(task_id, status='error', error=error_msg,
                    message=f'处理失败: {error_msg}')
        traceback.print_exc()


# ===== API 路由 =====

def _launch_task(task_id, pdf_path, config, task_updates=None):
    snapshot = None
    with _tasks_lock:
        existing = _running_threads.get(task_id)
        if existing:
            return False

        if task_updates and task_id in _tasks:
            _tasks[task_id].update(task_updates)
            snapshot = _tasks[task_id].copy()

        def runner():
            try:
                run_pipeline(task_id, pdf_path, config)
            finally:
                with _tasks_lock:
                    _running_threads.pop(task_id, None)

        thread = threading.Thread(target=runner, name=f'film-{task_id}', daemon=True)
        _running_threads[task_id] = thread
    if snapshot:
        store.save_task(snapshot)
    try:
        thread.start()
    except Exception:
        with _tasks_lock:
            if _running_threads.get(task_id) is thread:
                _running_threads.pop(task_id, None)
        if task_updates:
            update_task(task_id, status='paused', pause_requested=False,
                        message='任务线程启动失败，请重试')
        raise
    return True


def _task_summary(task):
    return {
        'id': task.get('id'),
        'pdf_name': task.get('pdf_name') or os.path.basename(task.get('pdf_path', '')),
        'status': task.get('status'),
        'phase': task.get('phase'),
        'progress': task.get('progress', 0),
        'message': task.get('message', ''),
        'error': task.get('error'),
        'created_at': task.get('created_at'),
        'updated_at': task.get('updated_at'),
        'has_video': bool(task.get('result_path') and os.path.exists(task['result_path'])),
        'has_subtitles': bool(task.get('subtitle_path') and os.path.exists(task['subtitle_path'])),
        'has_prompts': bool(task.get('prompts_path') and os.path.exists(task['prompts_path'])),
    }


@app.route('/api/auth/session')
def auth_session():
    if not g.current_user:
        return jsonify({'authenticated': False, 'auth_required': AUTH_REQUIRED})
    return jsonify({
        'authenticated': True,
        'auth_required': AUTH_REQUIRED,
        'email': g.current_user['email'],
        'csrf_token': (g.auth_session or {}).get('csrf_token', ''),
    })


@app.route('/api/captcha')
def auth_captcha():
    ip = _client_ip()
    if not _rate_allowed(f'captcha-ip:{ip}', 600, 40):
        return jsonify({'error': '图形验证码刷新过于频繁，请稍后再试'}), 429
    text = build_captcha_text()
    captcha_id = secrets.token_hex(10)
    expires = (datetime.now(timezone.utc)
               + timedelta(minutes=CAPTCHA_TTL_MINUTES)).isoformat()
    store.create_captcha(captcha_id, text, expires)
    return jsonify({
        'id': captcha_id,
        'image': render_captcha_data_url(text),
        'expires_at': expires,
    })


@app.route('/api/auth/send-code', methods=['POST'])
def auth_send_code():
    data = request.json or {}
    try:
        email = normalize_email(data.get('email'))
        captcha_id = validate_captcha_id(data.get('captcha_id'))
        captcha_text = validate_captcha(data.get('captcha_text'))
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    ip = _client_ip()
    if not _rate_allowed(f'code-ip:{ip}', 600, 8):
        return jsonify({'error': '该网络请求验证码过于频繁，请稍后再试'}), 429
    if not _rate_allowed(f'code-email:{email}', 600, 6):
        return jsonify({'error': '该邮箱请求验证码过于频繁，请稍后再试'}), 429
    if not store.verify_captcha(captcha_id, captcha_text, consume=False):
        return jsonify({'error': '图形验证码错误或已过期，请刷新后重试'}), 400
    latest = store.latest_code_created_at(email, 'login')
    if latest:
        try:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(latest)).total_seconds()
        except (TypeError, ValueError):
            elapsed = AUTH_CODE_COOLDOWN_SECONDS
        if elapsed < AUTH_CODE_COOLDOWN_SECONDS:
            wait_seconds = max(1, int(AUTH_CODE_COOLDOWN_SECONDS - elapsed))
            return jsonify({'error': f'验证码刚刚已发送，请等待 {wait_seconds} 秒'}), 429

    code = f'{secrets.randbelow(1_000_000):06d}'
    expires = (datetime.now(timezone.utc)
               + timedelta(minutes=EMAIL_CODE_TTL_MINUTES)).isoformat()
    store.create_verification_code(email, 'login', code, expires)
    try:
        mail_result = send_login_code(email, code)
    except Exception as error:
        return jsonify({'error': f'验证码邮件发送失败: {error}'}), 502
    return jsonify({
        'ok': True,
        'message': '验证码已发送，请检查邮箱',
        'preview_code': mail_result.get('preview_code'),
        'cooldown_seconds': AUTH_CODE_COOLDOWN_SECONDS,
    })


@app.route('/api/auth/verify', methods=['POST'])
def auth_verify():
    ip = _client_ip()
    if not _rate_allowed(f'verify-ip:{ip}', 600, 12):
        return jsonify({'error': '验证码尝试次数过多，请稍后再试'}), 429
    data = request.json or {}
    try:
        email = normalize_email(data.get('email'))
        code = validate_code(data.get('code'))
        captcha_id = validate_captcha_id(data.get('captcha_id'))
        captcha_text = validate_captcha(data.get('captcha_text'))
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    if not store.verify_captcha(captcha_id, captcha_text, consume=True):
        return jsonify({'error': '图形验证码错误或已过期，请刷新后重试'}), 400
    if not store.consume_verification_code(email, 'login', code):
        return jsonify({'error': '验证码错误、已使用或已过期'}), 400

    user = store.login_user(email)
    session_id = secrets.token_hex(24)
    csrf_token = secrets.token_hex(24)
    expires = (datetime.now(timezone.utc)
               + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
    store.create_session(session_id, user['id'], csrf_token, expires)
    response = jsonify({
        'ok': True, 'authenticated': True, 'email': email,
        'csrf_token': csrf_token,
    })
    response.headers.add('Set-Cookie', _session_cookie(session_id))
    return response


@app.route('/api/auth/logout', methods=['POST'])
@login_required
@csrf_required
def auth_logout():
    session_id = (g.auth_session or {}).get('id')
    if AUTH_REQUIRED and session_id:
        store.delete_session(session_id)
    response = jsonify({'ok': True})
    response.headers.add('Set-Cookie', _session_cookie('', max_age=0))
    return response


@app.route('/api/tasks')
@login_required
def list_user_tasks():
    tasks = store.list_tasks(g.current_user['id'])
    return jsonify({'tasks': [_task_summary(task) for task in tasks]})


@app.route('/api/tasks/<task_id>/pause', methods=['POST'])
@login_required
@csrf_required
def pause_task(task_id):
    task = _owned_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    if task.get('status') not in ('pending', 'running', 'waiting_user', 'pausing'):
        return jsonify({'error': '当前任务状态不能暂停'}), 409
    update_task(task_id, pause_requested=True, status='pausing',
                message='正在等待当前操作完成后暂停...')
    with _tasks_lock:
        runtime = _tasks.get(task_id)
        if runtime and task.get('status') == 'waiting_user':
            runtime['decision'] = 'pause'
            runtime['decision_event'].set()
    return jsonify({'ok': True, 'status': 'pausing'})


@app.route('/api/tasks/<task_id>/resume', methods=['POST'])
@login_required
@csrf_required
def resume_task(task_id):
    task = _owned_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    if task.get('status') != 'paused':
        return jsonify({'error': '只有已暂停任务可以继续'}), 409
    data = request.json or {}
    config = dict(task.get('config') or {})
    for key in ('api_key', 'llm_api_key', 'image_api_key', 'video_api_key',
                'seedance_api_key'):
        config[key] = str(data.get(key, '') or '').strip()
    config['api_key'] = config.get('llm_api_key') or config.get('api_key', '')
    if config.get('use_ai_analysis', True) and not config.get('llm_api_key'):
        return jsonify({'error': '继续 AI 任务前请重新提供 LLM API Key'}), 400
    needs_image = (config.get('use_image_generation') or config.get('colorize_pages')
                   or config.get('cover_mode') == 'ai')
    if needs_image and not (config.get('image_api_key') or config.get('llm_api_key')):
        return jsonify({'error': '继续图像任务前请重新提供图像或 LLM API Key'}), 400
    if not _launch_task(
            task_id, task['pdf_path'], config,
            task_updates={
                'status': 'pending', 'pause_requested': False, 'error': None,
                'message': '正在从最近检查点继续任务...',
            }):
        return jsonify({'error': '任务线程仍在运行，请稍后重试'}), 409
    return jsonify({'ok': True, 'task_id': task_id})

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/config')
@login_required
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
        'agnes_base_url': AGNES_BASE_URL,
        'agnes_llm_models': AGNES_LLM_MODELS,
        'agnes_image_models': AGNES_IMAGE_MODELS,
        'agnes_video_models': AGNES_VIDEO_MODELS,
        'agnes_image_sizes': AGNES_IMAGE_SIZES,
        'default_llm_model': LLM_MODEL,
        'default_image_model': IMAGE_MODEL,
        'video_engines': VIDEO_ENGINES,
        'default_video_engine': DEFAULT_VIDEO_ENGINE,
        'default_narration_voice': DEFAULT_NARRATION_VOICE,
        'default_dialogue_voice': DEFAULT_DIALOGUE_VOICE,
    })


@app.route('/api/test', methods=['POST'])
@login_required
@csrf_required
def test_connection():
    """连接测试：快速验证 LLM 与图像模型是否可用，避免跑完整流程才发现模型名不对。"""
    data = request.json or {}
    api_key = data.get('llm_api_key', '').strip() or data.get('api_key', '').strip()
    base_url = data.get('llm_base_url', '').strip() or data.get('base_url', '').strip()
    image_base_url = data.get('image_base_url', '').strip() or IMAGE_BASE_URL or base_url
    explicit_image_key = data.get('image_api_key', '').strip()
    if explicit_image_key:
        image_api_key = explicit_image_key
    elif any(host in image_base_url.lower() for host in ('agnes-ai.com', 'nvidia.com')):
        image_api_key = api_key
    else:
        image_api_key = IMAGE_API_KEY or api_key
    llm_model = data.get('llm_model', '').strip() or LLM_MODEL
    image_model = data.get('image_model', '').strip() or IMAGE_MODEL
    image_size_tier = data.get('image_size_tier', '1K').strip().upper()
    use_image_generation = bool(data.get('use_image_generation', False))
    colorize_pages_enabled = bool(data.get('colorize_pages', False))

    result = {'llm': None, 'image': None, 'video': None}

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
        info = test_image_model(image_api_key, image_base_url, image_model,
                                image_size_tier=image_size_tier)
        result['image'] = {'ok': True, 'model': image_model, 'info': info}
      except Exception as e:
        result['image'] = {'ok': False, 'model': image_model, 'error': str(e)}

    video_engine = data.get('video_engine', '').strip()
    if video_engine == 'agnes':
        video_key = data.get('video_api_key', '').strip() or api_key
        video_base = data.get('video_base_url', '').strip() or AGNES_BASE_URL
        video_model = data.get('video_model', '').strip() or 'agnes-video-v2.0'
        if not video_key:
            result['video'] = {'ok': False, 'model': video_model,
                               'error': '未填写 Agnes 视频 API Key，也没有可复用的 LLM Key'}
        elif not video_base.startswith(('http://', 'https://')):
            result['video'] = {'ok': False, 'model': video_model,
                               'error': '视频 Base URL 必须以 http:// 或 https:// 开头'}
        else:
            # 官方没有无消耗的健康检查端点。避免连接测试创建收费/耗时的视频任务，
            # 此处只验证配置，真实鉴权在首次提交场景时完成。
            result['video'] = {
                'ok': True, 'configured': True, 'model': video_model,
                'message': '配置格式有效；未创建视频任务，实际鉴权将在生成时验证',
            }
    else:
        result['video'] = {'ok': True, 'skipped': True, 'model': '',
                           'message': '未选择 Agnes 视频引擎'}

    return jsonify(result)


@app.route('/api/upload', methods=['POST'])
@login_required
@csrf_required
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
        save_path = os.path.join(_user_upload_dir(), f"{file_id}_{filename}")
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


@app.route('/api/ocr_preview', methods=['POST'])
@login_required
@csrf_required
def ocr_preview():
    """手动模式预览 OCR 文本，供用户修改后作为伴读内容。"""
    data = request.json or {}
    pdf_path = data.get('pdf_path', '')
    if not _is_owned_upload(pdf_path) or not os.path.exists(pdf_path):
        return jsonify({'error': 'PDF文件不存在'}), 400
    try:
        import tempfile
        from core.pdf_processor import extract_pages
        from core.ocr_processor import ocr_pages
        from core.pdf_processor import parse_page_selection
        from core.pdf_processor import get_page_count
        total = get_page_count(pdf_path)
        selection = data.get('page_selection', '').strip()
        pages = parse_page_selection(selection, total) if selection else list(range(1, total + 1))
        work = tempfile.mkdtemp(prefix='ocr_preview_')
        try:
            images = extract_pages(pdf_path, work, start_page=min(pages), end_page=max(pages))
            wanted = {p: os.path.join(work, f'page_{p:04d}.png') for p in pages}
            images = [wanted[p] for p in pages if os.path.exists(wanted[p])]
            results = ocr_pages(
                images,
                language=data.get('ocr_language', 'ch'),
                engine=data.get('ocr_engine', 'rapidocr'),
            )
            size = max(1, int(data.get('pages_per_segment', 1) or 1))
            segments = ['\n'.join(r.get('text', '').strip() for r in results[i:i + size]).strip()
                        for i in range(0, len(results), size)]
            return jsonify({'segments': segments, 'pages': results, 'page_count': len(pages)})
        finally:
            import shutil
            shutil.rmtree(work, ignore_errors=True)
    except Exception as error:
        return jsonify({'error': f'OCR预览失败: {error}'}), 500


@app.route('/api/process', methods=['POST'])
@login_required
@csrf_required
def start_process():
    """启动影片生成管线"""
    data = request.json or {}

    pdf_path = data.get('pdf_path', '')
    if not _is_owned_upload(pdf_path) or not os.path.exists(pdf_path):
        return jsonify({'error': 'PDF文件不存在'}), 400

    api_key = data.get('llm_api_key', '').strip() or data.get('api_key', '').strip()
    use_ai_analysis = bool(data.get('use_ai_analysis', True))
    if use_ai_analysis and not api_key:
        return jsonify({'error': '请填写 LLM API Key'}), 400

    cover_path = data.get('cover_path', '').strip()
    bgm_path = data.get('bgm_path', '').strip()
    if cover_path and (not _is_owned_upload(cover_path) or not os.path.isfile(cover_path)):
        return jsonify({'error': '封面文件不存在或不属于当前用户'}), 400
    if bgm_path and (not _is_owned_upload(bgm_path) or not os.path.isfile(bgm_path)):
        return jsonify({'error': '背景音乐不存在或不属于当前用户'}), 400

    config = {
        'api_key': api_key,
        'base_url': data.get('base_url', '').strip(),
        'llm_api_key': api_key,
        'llm_base_url': data.get('llm_base_url', '').strip() or data.get('base_url', '').strip(),
        'image_api_key': data.get('image_api_key', '').strip(),
        'image_base_url': data.get('image_base_url', '').strip(),
        'llm_model': data.get('llm_model', '').strip(),
        'image_model': data.get('image_model', '').strip(),
        'image_size_tier': data.get('image_size_tier', '1K').strip().upper(),
        'use_image_generation': bool(data.get('use_image_generation', False)),
        'colorize_pages': bool(data.get('colorize_pages', False)),
        'art_style': data.get('art_style', 'cinematic'),
        'resolution': data.get('resolution', '1080p_land'),
        'orientation': data.get('orientation', 'landscape'),
        'start_page': int(data.get('start_page', 1)),
        'end_page': int(data.get('end_page', 0)) or None,
        'page_selection': data.get('page_selection', '').strip(),
        'page_layout': data.get('page_layout', 'auto').strip(),
        'use_ai_analysis': use_ai_analysis,
        'ocr_language': data.get('ocr_language', 'ch').strip(),
        'ocr_engine': data.get('ocr_engine', 'rapidocr').strip().lower(),
        'pages_per_segment': int(data.get('pages_per_segment', 1) or 1),
        'manual_duration': float(data.get('manual_duration', 5) or 5),
        'manual_durations': data.get('manual_durations', '').strip(),
        'manual_narration': data.get('manual_narration', ''),
        'ocr_results': data.get('ocr_results') if isinstance(data.get('ocr_results'), list) else None,
        'cover_mode': data.get('cover_mode', 'none').strip(),
        'cover_path': cover_path,
        'cover_duration': float(data.get('cover_duration', 3) or 3),
        'first_page_is_cover': bool(data.get('first_page_is_cover', True)),
        'auto_duration_tts': bool(data.get('auto_duration_tts', True)),
        'use_tts': data.get('use_tts', True),
        'tts_voice': data.get('tts_voice', 'zh-CN-YunxiNeural'),
        'dialogue_voice': data.get('dialogue_voice', '').strip() or DEFAULT_DIALOGUE_VOICE,
        'tts_rate': data.get('tts_rate', '+0%'),
        'bgm_path': bgm_path,
        'bgm_volume': float(data.get('bgm_volume', 0.15)),
        'video_engine': data.get('video_engine', '').strip() or DEFAULT_VIDEO_ENGINE,
        'export_prompts': bool(data.get('export_prompts', False)),
        'seedance_api_key': data.get('seedance_api_key', '').strip(),
        'seedance_base_url': data.get('seedance_base_url', '').strip(),
        'seedance_model': data.get('seedance_model', '').strip(),
        'video_api_key': data.get('video_api_key', '').strip(),
        'video_base_url': data.get('video_base_url', '').strip(),
        'video_model': data.get('video_model', '').strip(),
        'video_resolution_tier': data.get('video_resolution_tier', '720p').strip().lower(),
        'video_frame_rate': int(data.get('video_frame_rate', 24) or 24),
    }

    task_id = create_task(g.current_user['id'], pdf_path, config)

    # 启动后台线程
    _launch_task(task_id, pdf_path, config)

    return jsonify({'task_id': task_id})


@app.route('/api/progress/<task_id>')
@login_required
def get_progress(task_id):
    """获取任务进度"""
    task = _owned_task(task_id)
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
        'decision_can_retry': bool(task.get('decision_can_retry')),
    })


@app.route('/api/decision/<task_id>', methods=['POST'])
@login_required
@csrf_required
def task_decision(task_id):
    decision = (request.json or {}).get('decision')
    if decision not in ('continue', 'retry', 'abort'):
        return jsonify({'error': 'decision 必须是 continue、retry 或 abort'}), 400
    if not _owned_task(task_id):
        return jsonify({'error': '任务不存在'}), 404
    with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            return jsonify({'error': '任务不存在'}), 404
        if task.get('status') != 'waiting_user':
            return jsonify({'error': '任务当前不需要用户决定'}), 409
        if decision == 'retry' and not task.get('decision_can_retry'):
            return jsonify({'error': '当前步骤不支持重试'}), 400
        task['decision'] = decision
        task['decision_prompt'] = (request.json or {}).get('prompt', '').strip()
        task['decision_event'].set()
    return jsonify({'ok': True})


@app.route('/api/scene_image/<task_id>/<int:scene_idx>')
@login_required
def get_scene_image(task_id, scene_idx):
    """获取场景生成的 AI 图片"""
    task = _owned_task(task_id)
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
@login_required
def download_video(task_id):
    """下载生成的影片"""
    task = _owned_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    video_path = task.get('result_path')
    if not video_path or not os.path.exists(video_path):
        return jsonify({'error': '影片不存在'}), 404

    return send_file(video_path, as_attachment=True,
                     download_name=f'ai_film_{task_id}.mp4')


@app.route('/api/preview/<task_id>')
@login_required
def preview_video(task_id):
    """预览影片（在线播放）"""
    task = _owned_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    video_path = task.get('result_path')
    if not video_path or not os.path.exists(video_path):
        return jsonify({'error': '影片不存在'}), 404

    return send_file(video_path, mimetype='video/mp4')


@app.route('/api/download_prompts/<task_id>')
@login_required
def download_prompts(task_id):
    """下载生成的 AI 视频工具提示词（.txt）"""
    task = _owned_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    prompts_path = task.get('prompts_path')
    if not prompts_path or not os.path.exists(prompts_path):
        return jsonify({'error': '提示词不存在（未开启该功能或尚未生成）'}), 404

    return send_file(prompts_path, as_attachment=True,
                     download_name=f'video_prompts_{task_id}.txt',
                     mimetype='text/plain; charset=utf-8')


@app.route('/api/download_subtitles/<task_id>')
@login_required
def download_subtitles(task_id):
    """下载与影片时间轴对应的 SRT 字幕。"""
    task = _owned_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    subtitle_path = task.get('subtitle_path')
    if not subtitle_path or not os.path.exists(subtitle_path):
        return jsonify({'error': '字幕文件不存在'}), 404
    return send_file(subtitle_path, as_attachment=True,
                     download_name=f'subtitles_{task_id}.srt',
                     mimetype='application/x-subrip; charset=utf-8')


@app.route('/api/upload_bgm', methods=['POST'])
@login_required
@csrf_required
def upload_bgm():
    """上传背景音乐"""
    if 'file' not in request.files:
        return jsonify({'error': '没有文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '未选择文件'}), 400

    filename = secure_filename(file.filename)
    file_id = str(uuid.uuid4())[:8]
    save_path = os.path.join(_user_upload_dir(), f"bgm_{file_id}_{filename}")
    file.save(save_path)

    return jsonify({'bgm_path': save_path, 'filename': filename})


@app.route('/api/upload_srt', methods=['POST'])
@login_required
@csrf_required
def upload_srt():
    """上传外部 SRT；前端/后续任务可使用其文本作为手动伴读输入。"""
    if 'file' not in request.files or not request.files['file'].filename.lower().endswith('.srt'):
        return jsonify({'error': '请上传 SRT 字幕文件'}), 400
    file = request.files['file']
    filename = secure_filename(file.filename) or 'subtitles.srt'
    path = os.path.join(_user_upload_dir(), f"srt_{uuid.uuid4().hex[:8]}_{filename}")
    file.save(path)
    return jsonify({'srt_path': path, 'filename': filename})


@app.route('/api/upload_cover', methods=['POST'])
@login_required
@csrf_required
def upload_cover():
    if 'file' not in request.files:
        return jsonify({'error': '没有封面图片'}), 400
    file = request.files['file']
    if not file.filename or not file.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
        return jsonify({'error': '封面仅支持 PNG/JPG/JPEG/WEBP'}), 400
    filename = secure_filename(file.filename) or 'cover.png'
    path = os.path.join(_user_upload_dir(), f"cover_{uuid.uuid4().hex[:8]}_{filename}")
    file.save(path)
    return jsonify({'cover_path': path, 'filename': filename})


if __name__ == '__main__':
    print("=" * 60)
    print("  AI 影片生成系统")
    print("  PDF → OCR → AI理解 → AI画面生成 → TTS配音 → 影片")
    print("=" * 60)
    # debug=False + use_reloader=False 避免子进程重复占用 5000 端口
    port = int(os.environ.get('PORT', '5000'))
    app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)
