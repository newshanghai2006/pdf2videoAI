/* ===== AI Film Studio - 前端交互 ===== */

// 全局状态
let state = {
    pdfPath: '',
    pdfName: '',
    pageCount: 0,
    bgmPath: '',
    taskId: null,
    pollTimer: null,
    lastSceneCount: 0,
    hasPrompts: false,
};

// ===== 初始化 =====
document.addEventListener('DOMContentLoaded', () => {
    loadConfig();
    loadSavedSettings();
    setupUpload();
    setupStepNavigation();
    setupConfigControls();
    setupBgmUpload();
    setupProcessButton();
    setupTestButton();
});

const savedSettingFields = [
    'llmApiKey', 'llmBaseUrl', 'llmModel',
    'imageApiKey', 'imageBaseUrl', 'imageModel',
    'seedanceKey', 'seedanceBaseUrl', 'seedanceModel',
];

async function loadSavedSettings() {
    try {
        const res = await fetch('/api/settings');
        const data = await res.json();
        const mapping = {
            llmApiKey: 'llm_api_key', llmBaseUrl: 'llm_base_url', llmModel: 'llm_model',
            imageApiKey: 'image_api_key', imageBaseUrl: 'image_base_url', imageModel: 'image_model',
            seedanceKey: 'seedance_api_key', seedanceBaseUrl: 'seedance_base_url', seedanceModel: 'seedance_model',
        };
        for (const [id, key] of Object.entries(mapping)) {
            const element = document.getElementById(id);
            if (element && data[key]) element.value = data[key];
        }
    } catch (e) {
        console.warn('加载本地配置失败:', e);
    }
}

async function saveSettings() {
    const mapping = {
        llmApiKey: 'llm_api_key', llmBaseUrl: 'llm_base_url', llmModel: 'llm_model',
        imageApiKey: 'image_api_key', imageBaseUrl: 'image_base_url', imageModel: 'image_model',
        seedanceKey: 'seedance_api_key', seedanceBaseUrl: 'seedance_base_url', seedanceModel: 'seedance_model',
    };
    const data = {};
    for (const [id, key] of Object.entries(mapping)) {
        const element = document.getElementById(id);
        data[key] = element ? element.value.trim() : '';
    }
    try {
        await fetch('/api/settings', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data),
        });
    } catch (e) {
        console.warn('保存本地配置失败:', e);
    }
}

// ===== 加载配置选项 =====
async function loadConfig() {
    try {
        const res = await fetch('/api/config');
        const data = await res.json();

        // 艺术风格
        const artSelect = document.getElementById('artStyle');
        for (const [key, desc] of Object.entries(data.art_styles)) {
            const opt = document.createElement('option');
            opt.value = key;
            opt.textContent = desc;
            artSelect.appendChild(opt);
        }

        // 语音
        const voiceSelect = document.getElementById('ttsVoice');
        const dlgVoiceSelect = document.getElementById('dialogueVoice');
        for (const [key, desc] of Object.entries(data.tts_voices)) {
            const opt = document.createElement('option');
            opt.value = key;
            opt.textContent = desc;
            voiceSelect.appendChild(opt);

            const opt2 = document.createElement('option');
            opt2.value = key;
            opt2.textContent = desc;
            dlgVoiceSelect.appendChild(opt2);
        }

        // 默认：旁白/对白用不同声音
        voiceSelect.value = data.default_narration_voice || 'zh-CN-YunxiNeural';
        dlgVoiceSelect.value = data.default_dialogue_voice || 'zh-CN-XiaoxiaoNeural';

        // 视频引擎
        if (data.video_engines) {
            const engSelect = document.getElementById('videoEngine');
            const engHint = document.getElementById('videoEngineHint');
            for (const [key, desc] of Object.entries(data.video_engines)) {
                const opt = document.createElement('option');
                opt.value = key;
                opt.textContent = desc;
                engSelect.appendChild(opt);
            }
            engSelect.value = data.default_video_engine || 'kenburns';
            const syncEngineUi = () => {
                const isSeedance = engSelect.value === 'seedance';
                document.getElementById('seedanceConfig').style.display =
                    isSeedance ? 'block' : 'none';
                if (engHint) {
                    engHint.textContent = isSeedance
                        ? '火山 Seedance 生成真实动态视频（需填 API Key；未接入前自动降级为 Ken Burns）'
                        : '本地图像 + 缓动运镜，无需额外 API';
                }
            };
            engSelect.addEventListener('change', syncEngineUi);
            syncEngineUi();
        }

        // LLM 模型预设
        if (data.llm_models) {
            const llmList = document.getElementById('llmModelList');
            const llmPreset = document.getElementById('llmModelPreset');
            for (const [key, desc] of Object.entries(data.llm_models)) {
                const opt = document.createElement('option');
                opt.value = key;
                opt.textContent = desc;
                llmList.appendChild(opt);
                const preset = document.createElement('option');
                preset.value = key;
                preset.textContent = `${key} - ${desc}`;
                llmPreset.appendChild(preset);
            }
            if (data.default_llm_model) {
                document.getElementById('llmModel').value = data.default_llm_model;
            }
        }

        // 图像模型预设
        if (data.image_models) {
            const imgList = document.getElementById('imageModelList');
            const imgPreset = document.getElementById('imageModelPreset');
            for (const [key, desc] of Object.entries(data.image_models)) {
                const opt = document.createElement('option');
                opt.value = key;
                opt.textContent = desc;
                imgList.appendChild(opt);
                const preset = document.createElement('option');
                preset.value = key;
                preset.textContent = `${key} - ${desc}`;
                imgPreset.appendChild(preset);
            }
            if (data.default_image_model) {
                document.getElementById('imageModel').value = data.default_image_model;
            }
        }

        // 根据方向自动调整分辨率选项
        updateResolutionOptions('landscape');

        document.getElementById('llmModelPreset').addEventListener('change', (e) => {
            if (e.target.value) document.getElementById('llmModel').value = e.target.value;
        });
        document.getElementById('imageModelPreset').addEventListener('change', (e) => {
            if (e.target.value) document.getElementById('imageModel').value = e.target.value;
        });
    } catch (e) {
        console.error('加载配置失败:', e);
    }
}

// ===== 上传处理 =====
function setupUpload() {
    const zone = document.getElementById('uploadZone');
    const input = document.getElementById('fileInput');

    zone.addEventListener('click', () => input.click());

    zone.addEventListener('dragover', (e) => {
        e.preventDefault();
        zone.classList.add('dragover');
    });

    zone.addEventListener('dragleave', () => {
        zone.classList.remove('dragover');
    });

    zone.addEventListener('drop', (e) => {
        e.preventDefault();
        zone.classList.remove('dragover');
        if (e.dataTransfer.files.length) {
            input.files = e.dataTransfer.files;
            handleFileUpload(e.dataTransfer.files[0]);
        }
    });

    input.addEventListener('change', () => {
        if (input.files.length) {
            handleFileUpload(input.files[0]);
        }
    });

    document.getElementById('btnReupload').addEventListener('click', () => {
        document.getElementById('uploadInfo').style.display = 'none';
        document.getElementById('uploadZone').style.display = 'block';
        document.getElementById('btnStep1Next').disabled = true;
        state.pdfPath = '';
    });
}

async function handleFileUpload(file) {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
        alert('请上传 PDF 文件');
        return;
    }

    const formData = new FormData();
    formData.append('file', file);

    // 显示上传中
    const zone = document.getElementById('uploadZone');
    zone.innerHTML = '<div class="upload-icon" style="animation:spin 1s linear infinite">⏳</div><h2>上传中...</h2>';

    try {
        const res = await fetch('/api/upload', { method: 'POST', body: formData });
        const data = await res.json();

        if (data.error) {
            alert(data.error);
            location.reload();
            return;
        }

        state.pdfPath = data.path;
        state.pdfName = data.filename;
        state.pageCount = data.page_count;

        // 显示文件信息
        document.getElementById('fileName').textContent = data.filename;
        document.getElementById('fileMeta').textContent = `共 ${data.page_count} 页`;
        document.getElementById('uploadInfo').style.display = 'block';
        zone.style.display = 'none';

        // 恢复上传区内容
        zone.innerHTML = '<div class="upload-icon">📄</div><h2>拖拽 PDF 到此处</h2><p>或点击选择文件</p>';

        document.getElementById('btnStep1Next').disabled = false;

        // 更新页面范围提示
        document.getElementById('pageSelection').placeholder = `例如：1,3,5-${Math.min(10, data.page_count)}`;
        document.getElementById('pageRangeHint').textContent =
            `共 ${data.page_count} 页，建议先选 3-10 页测试效果`;
    } catch (e) {
        alert('上传失败: ' + e.message);
        location.reload();
    }
}

// ===== 步骤导航 =====
function setupStepNavigation() {
    document.getElementById('btnStep1Next').addEventListener('click', () => goToStep(2));
    document.getElementById('btnStep2Prev').addEventListener('click', () => goToStep(1));
    document.getElementById('btnStep2Next').addEventListener('click', startProcessing);
    document.getElementById('btnStep4Prev').addEventListener('click', () => {
        goToStep(2);
        // 重置状态
        if (state.pollTimer) clearInterval(state.pollTimer);
        resetProgress();
    });
    document.getElementById('btnRetry').addEventListener('click', () => {
        goToStep(2);
        resetProgress();
    });
}

function goToStep(stepNum) {
    // 隐藏所有面板
    document.querySelectorAll('.step-panel').forEach(p => p.classList.remove('active'));
    // 显示目标面板
    document.getElementById('step' + stepNum).classList.add('active');

    // 更新导航
    document.querySelectorAll('.step-item').forEach((item, i) => {
        item.classList.remove('active', 'completed');
        if (i + 1 < stepNum) {
            item.classList.add('completed');
        } else if (i + 1 === stepNum) {
            item.classList.add('active');
        }
    });
}

// ===== 配置控件 =====
function setupConfigControls() {
    const aiToggle = document.getElementById('useAiAnalysis');
    const manualConfig = document.getElementById('manualConfig');
    const syncAnalysisMode = () => { manualConfig.style.display = aiToggle.checked ? 'none' : 'block'; };
    aiToggle.addEventListener('change', syncAnalysisMode);
    syncAnalysisMode();
    document.getElementById('srtInput').addEventListener('change', async (event) => {
        const file = event.target.files[0];
        if (!file) return;
        const text = await file.text();
        const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/);
        const entries = [];
        let current = [];
        for (const line of lines) {
            if (!line.trim()) { if (current.length) entries.push(current.join('\n')); current = []; continue; }
            if (/^\d+$/.test(line.trim()) || line.includes('-->')) continue;
            current.push(line.trim());
        }
        if (current.length) entries.push(current.join('\n'));
        document.getElementById('manualNarration').value = entries.join('\n');
    });
    // 方向切换
    document.querySelectorAll('#orientationToggle .toggle-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#orientationToggle .toggle-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            updateResolutionOptions(btn.dataset.value);
        });
    });

    // TTS 开关
    document.getElementById('useTts').addEventListener('change', (e) => {
        const on = e.target.checked;
        document.getElementById('ttsConfig').style.opacity = on ? '1' : '0.5';
        document.getElementById('dialogueVoiceConfig').style.opacity = on ? '1' : '0.5';
        document.getElementById('ttsVoice').disabled = !on;
        document.getElementById('dialogueVoice').disabled = !on;
    });

    document.getElementById('useImageGeneration').addEventListener('change', (e) => {
        if (e.target.checked) document.getElementById('colorizePages').checked = false;
    });
    document.getElementById('colorizePages').addEventListener('change', (e) => {
        if (e.target.checked) document.getElementById('useImageGeneration').checked = false;
    });

    // BGM 音量
    document.getElementById('bgmVolume').addEventListener('input', (e) => {
        document.getElementById('bgmVolLabel').textContent = e.target.value + '%';
    });
}

function updateResolutionOptions(orientation) {
    const select = document.getElementById('resolution');
    select.innerHTML = '';
    if (orientation === 'landscape') {
        select.innerHTML = `
            <option value="1080p_land">1080p 横屏 (1920×1080)</option>
            <option value="720p_land">720p 横屏 (1280×720)</option>
        `;
    } else {
        select.innerHTML = `
            <option value="1080p_port">1080p 竖屏 (1080×1920)</option>
            <option value="720p_port">720p 竖屏 (720×1280)</option>
        `;
    }
}

// ===== BGM 上传 =====
function setupBgmUpload() {
    const input = document.getElementById('bgmInput');
    const btn = document.getElementById('btnUploadBgm');

    btn.addEventListener('click', () => input.click());

    input.addEventListener('change', async () => {
        if (!input.files.length) return;

        const formData = new FormData();
        formData.append('file', input.files[0]);

        btn.textContent = '上传中...';
        btn.disabled = true;

        try {
            const res = await fetch('/api/upload_bgm', { method: 'POST', body: formData });
            const data = await res.json();

            if (data.error) {
                alert(data.error);
            } else {
                state.bgmPath = data.bgm_path;
                document.getElementById('bgmName').textContent = data.filename;
            }
        } catch (e) {
            alert('上传失败: ' + e.message);
        } finally {
            btn.textContent = '🎵 选择音乐';
            btn.disabled = false;
        }
    });
}

// ===== 连接测试 =====
function setupTestButton() {
    document.getElementById('btnTestConn').addEventListener('click', runTestConnection);
}

async function runTestConnection() {
    const btn = document.getElementById('btnTestConn');
    const resultEl = document.getElementById('testResult');
    const apiKey = document.getElementById('llmApiKey').value.trim();

    if (document.getElementById('useAiAnalysis').checked && !apiKey) {
        alert('请先填写 API Key');
        return;
    }

    btn.disabled = true;
    await saveSettings();
    btn.textContent = '⏳ 测试中...';
    resultEl.style.display = 'block';
    resultEl.className = 'test-result';
    resultEl.innerHTML = '<p>正在测试 LLM 与图像模型连通性...</p>';

    const payload = {
        api_key: apiKey,
        base_url: document.getElementById('llmBaseUrl').value.trim(),
        llm_api_key: apiKey,
        llm_base_url: document.getElementById('llmBaseUrl').value.trim(),
        image_api_key: document.getElementById('imageApiKey').value.trim(),
        image_base_url: document.getElementById('imageBaseUrl').value.trim(),
        use_image_generation: document.getElementById('useImageGeneration').checked,
        colorize_pages: document.getElementById('colorizePages').checked,
        llm_model: document.getElementById('llmModel').value.trim(),
        image_model: document.getElementById('imageModel').value.trim(),
        use_ai_analysis: document.getElementById('useAiAnalysis').checked,
    };

    try {
        const res = await fetch('/api/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json();

        let html = '';

        // LLM 结果
        if (data.llm && data.llm.ok) {
            html += `<div class="test-ok">✅ LLM 模型 <b>${escapeHtml(data.llm.model)}</b> 可用<br><span class="test-reply">回复: ${escapeHtml(data.llm.reply)}</span></div>`;
        } else if (data.llm) {
            html += `<div class="test-err">❌ LLM 模型 <b>${escapeHtml(data.llm.model)}</b> 失败<br><span class="test-reply">${escapeHtml(data.llm.error)}</span></div>`;
        }

        // 图像结果
        if (data.image && data.image.ok) {
            if (data.image.skipped) {
                html += '<div class="test-hint">⏭️ 已跳过图像模型测试（未启用 AI 画面生成）</div>';
            } else {
                const info = data.image.info;
                html += `<div class="test-ok">✅ 图像模型 <b>${escapeHtml(data.image.model)}</b> 可用（返回类型: ${info.response_type}）</div>`;
            }
        } else if (data.image) {
            html += `<div class="test-err">❌ 图像模型 <b>${escapeHtml(data.image.model)}</b> 失败<br><span class="test-reply">${escapeHtml(data.image.error)}</span></div>`;
        }

        html += '<p class="test-hint">两项均✅即可开始生成。若图像模型失败，请在上方改用你的代理支持的模型名（如 gpt-image-1 / dall-e-3）。</p>';

        resultEl.innerHTML = html;
    } catch (e) {
        resultEl.className = 'test-result test-err';
        resultEl.innerHTML = `<div class="test-err">请求失败: ${escapeHtml(e.message)}</div>`;
    } finally {
        btn.disabled = false;
        btn.textContent = '🔌 连接测试（验证 Key 与模型是否可用）';
    }
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// ===== 启动处理 =====
function setupProcessButton() {
    // 已在 setupStepNavigation 中处理
}

async function startProcessing() {
    const apiKey = document.getElementById('llmApiKey').value.trim();
    if (document.getElementById('useAiAnalysis').checked && !apiKey) {
        alert('请填写 OpenAI API Key');
        return;
    }

    const orientation = document.querySelector('#orientationToggle .toggle-btn.active').dataset.value;
    const resolution = document.getElementById('resolution').value;
    const artStyle = document.getElementById('artStyle').value;

    const pageSelection = document.getElementById('pageSelection').value.trim();
    if (pageSelection && !/^\s*\d+(?:\s*-\s*\d+)?(?:\s*[,，;；\s]+\s*\d+(?:\s*-\s*\d+)?)*\s*$/.test(pageSelection)) {
        alert('页面选择格式错误，请使用例如：1,3,5-10,12-20');
        return;
    }

    const payload = {
        pdf_path: state.pdfPath,
        api_key: apiKey,
        base_url: document.getElementById('llmBaseUrl').value.trim(),
        llm_api_key: apiKey,
        llm_base_url: document.getElementById('llmBaseUrl').value.trim(),
        image_api_key: document.getElementById('imageApiKey').value.trim(),
        image_base_url: document.getElementById('imageBaseUrl').value.trim(),
        use_image_generation: document.getElementById('useImageGeneration').checked,
        colorize_pages: document.getElementById('colorizePages').checked,
        llm_model: document.getElementById('llmModel').value.trim(),
        image_model: document.getElementById('imageModel').value.trim(),
        art_style: artStyle,
        resolution: resolution,
        orientation: orientation,
        page_selection: pageSelection,
        use_ai_analysis: document.getElementById('useAiAnalysis').checked,
        ocr_language: document.getElementById('ocrLanguage').value,
        pages_per_segment: parseInt(document.getElementById('pagesPerSegment').value) || 1,
        manual_duration: parseFloat(document.getElementById('manualDuration').value) || 5,
        manual_durations: document.getElementById('manualDurations').value.trim(),
        manual_narration: document.getElementById('manualNarration').value,
        use_tts: document.getElementById('useTts').checked,
        tts_voice: document.getElementById('ttsVoice').value,
        dialogue_voice: document.getElementById('dialogueVoice').value,
        video_engine: document.getElementById('videoEngine').value,
        seedance_api_key: document.getElementById('seedanceKey').value.trim(),
        seedance_base_url: document.getElementById('seedanceBaseUrl').value.trim(),
        seedance_model: document.getElementById('seedanceModel').value.trim(),
        export_prompts: document.getElementById('exportPrompts').checked,
        bgm_path: state.bgmPath,
        bgm_volume: parseInt(document.getElementById('bgmVolume').value) / 100,
    };

    await saveSettings();

    goToStep(3);

    try {
        const res = await fetch('/api/process', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json();

        if (data.error) {
            showError(data.error);
            return;
        }

        state.taskId = data.task_id;
        state.lastSceneCount = 0;
        startPolling();
    } catch (e) {
        showError(e.message);
    }
}

// ===== 轮询进度 =====
function startPolling() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = setInterval(pollProgress, 1000);
    pollProgress(); // 立即查询一次
}

async function pollProgress() {
    if (!state.taskId) return;

    try {
        const res = await fetch(`/api/progress/${state.taskId}`);
        const data = await res.json();

        // 更新进度条
        document.getElementById('progressFill').style.width = data.progress + '%';
        document.getElementById('progressText').textContent = data.progress + '%';
        document.getElementById('progressMessage').textContent = data.message || '';

        // 更新阶段指示器
        updatePhases(data.phase, data.status);

        // 提示词就绪标记
        if (data.has_prompts) state.hasPrompts = true;

        // 更新场景画廊
        if (data.scenes && data.scenes.length > state.lastSceneCount) {
            updateGallery(data.task_id || state.taskId, data.scenes);
            state.lastSceneCount = data.scenes.length;
        }

        // 检查完成/错误
        if (data.status === 'completed') {
            clearInterval(state.pollTimer);
            state.pollTimer = null;
            setTimeout(() => showResult(state.taskId), 500);
        } else if (data.status === 'error') {
            clearInterval(state.pollTimer);
            state.pollTimer = null;
            showError(data.error || '未知错误');
        }
    } catch (e) {
        console.error('轮询失败:', e);
    }
}

function updatePhases(currentPhase, status) {
    const phases = ['extract', 'ocr', 'analyze', 'generate', 'tts', 'build'];
    const currentIdx = phases.indexOf(currentPhase);

    document.querySelectorAll('.phase-item').forEach(item => {
        const phase = item.dataset.phase;
        const idx = phases.indexOf(phase);

        item.classList.remove('active', 'done', 'error');
        const statusEl = item.querySelector('.phase-status');

        if (status === 'error' && phase === currentPhase) {
            item.classList.add('error');
            statusEl.textContent = '❌';
        } else if (currentIdx === -1) {
            // 未开始
            statusEl.textContent = '⏳';
        } else if (idx < currentIdx) {
            item.classList.add('done');
            statusEl.textContent = '✅';
        } else if (idx === currentIdx) {
            item.classList.add('active');
            statusEl.textContent = '⚙️';
        } else {
            statusEl.textContent = '⏳';
        }
    });

    // 如果 tts 阶段被跳过
    if (currentPhase === 'build' && !document.getElementById('useTts').checked) {
        const ttsItem = document.querySelector('.phase-item[data-phase="tts"]');
        ttsItem.classList.remove('active', 'done', 'error');
        ttsItem.querySelector('.phase-status').textContent = '⏭️';
    }
}

function updateGallery(taskId, scenes) {
    const gallery = document.getElementById('sceneGallery');
    const grid = document.getElementById('galleryGrid');

    gallery.style.display = 'block';

    // 只更新新增的场景
    grid.innerHTML = '';
    scenes.forEach((scene, i) => {
        if (!scene.image_path) return;

        const item = document.createElement('div');
        item.className = 'gallery-item';

        const img = document.createElement('img');
        img.src = `/api/scene_image/${taskId}/${i}`;
        img.alt = `场景 ${i + 1}`;
        img.loading = 'lazy';

        const info = document.createElement('div');
        info.className = 'gallery-item-info';
        info.innerHTML = `
            <p class="gallery-item-num">场景 ${i + 1}</p>
            <p class="gallery-item-mood">${scene.mood || ''}</p>
            <p class="gallery-item-narration">${(scene.narration || '').substring(0, 80)}...</p>
        `;

        item.appendChild(img);
        item.appendChild(info);
        grid.appendChild(item);
    });
}

function showError(msg) {
    document.getElementById('errorBox').style.display = 'block';
    document.getElementById('errorMsg').textContent = msg;
    document.querySelector('.progress-container > h2').textContent = '处理失败';
}

function resetProgress() {
    document.getElementById('progressFill').style.width = '0%';
    document.getElementById('progressText').textContent = '0%';
    document.getElementById('progressMessage').textContent = '准备中...';
    document.getElementById('errorBox').style.display = 'none';
    document.getElementById('sceneGallery').style.display = 'none';
    document.getElementById('galleryGrid').innerHTML = '';
    document.querySelector('.progress-container > h2').textContent = '正在生成影片...';
    state.lastSceneCount = 0;
    state.hasPrompts = false;

    // 重置阶段状态
    document.querySelectorAll('.phase-item').forEach(item => {
        item.classList.remove('active', 'done', 'error');
        item.querySelector('.phase-status').textContent = '⏳';
    });
}

function showResult(taskId) {
    goToStep(4);

    const video = document.getElementById('videoPreview');
    video.src = `/api/preview/${taskId}`;

    const downloadBtn = document.getElementById('btnDownload');
    downloadBtn.href = `/api/download/${taskId}`;
    document.getElementById('btnDownloadSubtitles').href = `/api/download_subtitles/${taskId}`;

    // 提示词下载按钮（仅在勾选并成功生成时显示）
    const promptsBtn = document.getElementById('btnDownloadPrompts');
    if (state.hasPrompts) {
        promptsBtn.href = `/api/download_prompts/${taskId}`;
        promptsBtn.style.display = '';
    } else {
        promptsBtn.style.display = 'none';
    }
}
