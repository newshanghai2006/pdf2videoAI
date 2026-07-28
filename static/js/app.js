/* ===== AI Film Studio - 前端交互 ===== */

// 全局状态
let state = {
    pdfPath: '',
    pdfName: '',
    pageCount: 0,
    bgmPath: '',
    coverPath: '',
    decisionTimer: null,
    decisionCountdownInterval: null,
    decisionSeconds: 60,
    decisionEditing: false,
    decisionSubmitting: false,
    taskId: null,
    pollTimer: null,
    lastSceneCount: 0,
    hasPrompts: false,
};

// ===== 初始化 =====
document.addEventListener('DOMContentLoaded', async () => {
    await loadConfig();
    setupSettingsStorage();
    loadSavedSettings();
    setupUpload();
    setupStepNavigation();
    setupConfigControls();
    setupBgmUpload();
    setupCoverUpload();
    setupProcessButton();
    setupTestButton();
    document.getElementById('btnRetryAi').addEventListener('click', () => submitDecision('retry'));
    document.getElementById('btnContinueWithoutAi').addEventListener('click', () => submitDecision('continue'));
    document.getElementById('btnAbortTask').addEventListener('click', () => submitDecision('abort'));
});


function setupCoverUpload() {
    const mode = document.getElementById('coverMode');
    const input = document.getElementById('coverInput');
    mode.addEventListener('change', () => {
        document.getElementById('coverUploadGroup').style.display = mode.value === 'upload' ? 'block' : 'none';
    });
    input.addEventListener('change', async () => {
        if (!input.files.length) return;
        const form = new FormData(); form.append('file', input.files[0]);
        const response = await fetch('/api/upload_cover', {method: 'POST', body: form});
        const data = await response.json();
        if (data.error) { alert(data.error); return; }
        state.coverPath = data.cover_path;
        document.getElementById('coverName').textContent = data.filename;
    });
}

async function submitDecision(decision) {
    if (!state.taskId) return;
    if (state.decisionSubmitting) return;
    state.decisionSubmitting = true;
    if (state.decisionTimer) {
        clearTimeout(state.decisionTimer);
        state.decisionTimer = null;
    }
    if (state.decisionCountdownInterval) {
        clearInterval(state.decisionCountdownInterval);
        state.decisionCountdownInterval = null;
    }
    try {
        const res = await fetch(`/api/decision/${state.taskId}`, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({decision, prompt: collectDecisionPrompt()}),
        });
        const data = await res.json();
        // 自动提交与用户点击可能同时到达；任务已恢复时的 409 不应再次弹窗。
        if (!res.ok) {
            if (res.status !== 409) alert(data.error || '提交选择失败');
            return;
        }
        document.getElementById('decisionBox').style.display = 'none';
        document.getElementById('progressMessage').textContent =
            decision === 'retry' ? '正在按服务商限速要求重试当前 AI 步骤...'
                : (decision === 'continue' ? '正在继续生成...' : '正在退出任务...');
    } finally {
        state.decisionSubmitting = false;
        state.decisionEditing = false;
    }
}

function collectDecisionPrompt() {
    const fields = document.querySelectorAll('.decision-scene-prompt');
    if (fields.length) return JSON.stringify(Array.from(fields).map(field => ({
        scene_index: Number(field.dataset.sceneIndex),
        narration: field.disabled ? '' : field.value,
    })));
    return document.getElementById('decisionPrompt').value;
}

const savedSettingFields = [
    'llmApiKey', 'llmBaseUrl', 'llmModel',
    'imageApiKey', 'imageBaseUrl', 'imageModel',
    'videoApiKey', 'videoBaseUrl', 'videoModel',
];

const settingsStorageKey = 'pdf2video_ai_settings_v2';
const settingsStorageModeKey = 'pdf2video_settings_storage_mode';
const allowedSettingsStorageModes = new Set(['session', 'local', 'none']);

function getBrowserStorage(type) {
    try {
        return type === 'local' ? window.localStorage : window.sessionStorage;
    } catch (e) {
        console.warn(`浏览器 ${type}Storage 不可用:`, e);
        return null;
    }
}

function getSettingsStorageMode() {
    const local = getBrowserStorage('local');
    try {
        const savedMode = local ? local.getItem(settingsStorageModeKey) : '';
        return allowedSettingsStorageModes.has(savedMode) ? savedMode : 'session';
    } catch (e) {
        console.warn('读取配置存储方式失败:', e);
        return 'session';
    }
}

function rememberSettingsStorageMode(mode) {
    const local = getBrowserStorage('local');
    if (!local) return;
    try {
        local.setItem(settingsStorageModeKey, mode);
    } catch (e) {
        console.warn('保存配置存储方式失败:', e);
    }
}

function clearSavedSettings() {
    for (const type of ['session', 'local']) {
        const storage = getBrowserStorage(type);
        try {
            if (storage) storage.removeItem(settingsStorageKey);
        } catch (e) {
            console.warn(`清除 ${type}Storage 配置失败:`, e);
        }
    }
}

function setSettingsStorageHint(message = '') {
    const hint = document.getElementById('settingsStorageHint');
    if (!hint) return;
    if (message) {
        hint.textContent = message;
        return;
    }
    const mode = document.getElementById('settingsStorageMode')?.value || 'session';
    const messages = {
        session: 'Key 仅保存在当前标签页的 sessionStorage；关闭标签页后自动清除（公网推荐）。',
        local: 'Key 将明文保存在此浏览器的 localStorage。同源脚本可以读取，请勿在共享设备使用。',
        none: 'Key 不会保存；刷新页面或关闭标签页后需要重新输入。',
    };
    hint.textContent = messages[mode];
}

function setupSettingsStorage() {
    const modeSelect = document.getElementById('settingsStorageMode');
    const clearButton = document.getElementById('btnClearBrowserSettings');
    const mode = getSettingsStorageMode();
    modeSelect.value = mode;
    setSettingsStorageHint();

    modeSelect.addEventListener('change', () => {
        const nextMode = allowedSettingsStorageModes.has(modeSelect.value)
            ? modeSelect.value : 'session';
        rememberSettingsStorageMode(nextMode);
        if (nextMode === 'none') {
            clearSavedSettings();
        } else {
            saveSettings(nextMode);
        }
        setSettingsStorageHint();
    });

    clearButton.addEventListener('click', () => {
        clearSavedSettings();
        for (const id of ['llmApiKey', 'imageApiKey', 'videoApiKey']) {
            const element = document.getElementById(id);
            if (element) element.value = '';
        }
        setSettingsStorageHint('已清除浏览器中保存的 AI 配置和当前页面内的 API Key。');
    });
}

function loadSavedSettings() {
    const mode = getSettingsStorageMode();
    if (mode === 'none') return;
    const storage = getBrowserStorage(mode);
    if (!storage) {
        setSettingsStorageHint('浏览器存储不可用，本次配置不会保存。');
        return;
    }
    try {
        const raw = storage.getItem(settingsStorageKey);
        if (!raw) return;
        const data = JSON.parse(raw);
        if (!data || typeof data !== 'object' || Array.isArray(data)) {
            throw new Error('配置内容不是对象');
        }
        for (const id of savedSettingFields) {
            const element = document.getElementById(id);
            if (element && typeof data[id] === 'string') element.value = data[id];
        }
    } catch (e) {
        try { storage.removeItem(settingsStorageKey); } catch (_) { /* ignore */ }
        console.warn('浏览器中的 AI 配置已损坏并被清除:', e);
        setSettingsStorageHint('浏览器中的旧配置无法读取，已清除；请重新填写。');
    }
}

function saveSettings(forcedMode = '') {
    const mode = forcedMode || getSettingsStorageMode();
    if (mode === 'none') {
        clearSavedSettings();
        return;
    }
    const storage = getBrowserStorage(mode);
    if (!storage) {
        setSettingsStorageHint('浏览器存储不可用，本次配置不会保存。');
        return;
    }
    const data = {};
    for (const id of savedSettingFields) {
        const element = document.getElementById(id);
        data[id] = element ? element.value.trim() : '';
    }
    try {
        storage.setItem(settingsStorageKey, JSON.stringify(data));
        const otherStorage = getBrowserStorage(mode === 'local' ? 'session' : 'local');
        if (otherStorage) otherStorage.removeItem(settingsStorageKey);
    } catch (e) {
        console.warn('保存浏览器配置失败:', e);
        setSettingsStorageHint('浏览器拒绝保存配置，本次仍可继续使用。');
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
                const isAgnes = engSelect.value === 'agnes';
                document.getElementById('videoServiceConfig').style.display =
                    (isSeedance || isAgnes) ? 'block' : 'none';
                if (isAgnes) {
                    if (!document.getElementById('videoBaseUrl').value) {
                        document.getElementById('videoBaseUrl').value = data.agnes_base_url || 'https://apihub.agnes-ai.com/v1';
                    }
                    if (!document.getElementById('videoModel').value) {
                        document.getElementById('videoModel').value = 'agnes-video-v2.0';
                    }
                }
                if (engHint) {
                    engHint.textContent = isAgnes
                        ? 'Agnes 异步生成真实动态视频；免费默认 1 RPM，单个场景可能需要数分钟'
                        : (isSeedance
                            ? '火山 Seedance 生成真实动态视频（需填 API Key；未接入前自动降级为 Ken Burns）'
                            : '本地图像 + 缓动运镜，无需额外 API');
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

        const videoPreset = document.getElementById('videoModelPreset');
        for (const [key, desc] of Object.entries(data.agnes_video_models || {})) {
            const option = document.createElement('option');
            option.value = key;
            option.textContent = `${key} - ${desc}`;
            videoPreset.appendChild(option);
        }

        // 根据方向自动调整分辨率选项
        updateResolutionOptions('landscape');

        document.getElementById('llmModelPreset').addEventListener('change', (e) => {
            if (e.target.value) document.getElementById('llmModel').value = e.target.value;
        });
        document.getElementById('imageModelPreset').addEventListener('change', (e) => {
            if (e.target.value) document.getElementById('imageModel').value = e.target.value;
        });
        document.getElementById('videoModelPreset').addEventListener('change', (e) => {
            if (e.target.value) document.getElementById('videoModel').value = e.target.value;
        });
        document.getElementById('providerPreset').addEventListener('change', (e) => {
            if (e.target.value === 'agnes') {
                const baseUrl = data.agnes_base_url || 'https://apihub.agnes-ai.com/v1';
                document.getElementById('llmBaseUrl').value = baseUrl;
                document.getElementById('imageBaseUrl').value = baseUrl;
                document.getElementById('videoBaseUrl').value = baseUrl;
                const llmPreset = document.getElementById('llmModelPreset');
                const imagePreset = document.getElementById('imageModelPreset');
                const agnesVideoPreset = document.getElementById('videoModelPreset');
                llmPreset.innerHTML = '<option value="">选择 Agnes 文本模型或手动输入</option>';
                imagePreset.innerHTML = '<option value="">选择 Agnes 图像模型或手动输入</option>';
                agnesVideoPreset.innerHTML = '<option value="">选择 Agnes 视频模型或手动输入</option>';
                for (const [key, desc] of Object.entries(data.agnes_llm_models || {})) {
                    const option = document.createElement('option'); option.value = key; option.textContent = `${key} - ${desc}`; llmPreset.appendChild(option);
                }
                for (const [key, desc] of Object.entries(data.agnes_image_models || {})) {
                    const option = document.createElement('option'); option.value = key; option.textContent = `${key} - ${desc}`; imagePreset.appendChild(option);
                }
                for (const [key, desc] of Object.entries(data.agnes_video_models || {})) {
                    const option = document.createElement('option'); option.value = key; option.textContent = `${key} - ${desc}`; agnesVideoPreset.appendChild(option);
                }
                const llm = Object.keys(data.agnes_llm_models || {})[0] || 'agnes-2.0-flash';
                const image = Object.keys(data.agnes_image_models || {})[0] || 'agnes-image-2.0-flash';
                const video = Object.keys(data.agnes_video_models || {})[0] || 'agnes-video-v2.0';
                llmPreset.value = llm; imagePreset.value = image; agnesVideoPreset.value = video;
                document.getElementById('llmModel').value = llm;
                document.getElementById('imageModel').value = image;
                document.getElementById('videoModel').value = video;
                document.getElementById('imageApiKey').value = '';
                document.getElementById('videoApiKey').value = '';
                document.getElementById('useImageGeneration').checked = true;
                document.getElementById('colorizePages').checked = false;
                return;
            }
            if (e.target.value !== 'nvidia') return;
            document.getElementById('llmBaseUrl').value = 'https://integrate.api.nvidia.com/v1';
            const llmPreset = document.getElementById('llmModelPreset');
            const imagePreset = document.getElementById('imageModelPreset');
            llmPreset.innerHTML = '<option value="">选择 NVIDIA LLM 模型或手动输入</option>';
            imagePreset.innerHTML = '<option value="">选择 NVIDIA 图像模型或手动输入</option>';
            for (const [key, desc] of Object.entries(data.nvidia_llm_models || {})) {
                const option = document.createElement('option'); option.value = key; option.textContent = `${key} - ${desc}`; llmPreset.appendChild(option);
            }
            for (const [key, desc] of Object.entries(data.nvidia_image_models || {})) {
                const option = document.createElement('option'); option.value = key; option.textContent = `${key} - ${desc}`; imagePreset.appendChild(option);
            }
            const firstLlm = Object.keys(data.nvidia_llm_models || {})[0] || '';
            const preferredImage = 'black-forest-labs/flux.2-klein-4b';
            if (firstLlm) {
                llmPreset.value = firstLlm;
                document.getElementById('llmModel').value = firstLlm;
            }
            if ((data.nvidia_image_models || {})[preferredImage]) {
                imagePreset.value = preferredImage;
                document.getElementById('imageModel').value = preferredImage;
            }
            // 图像 Key/Base URL 留空时，后端自动沿用 NVIDIA LLM Key；
            // NVIDIA 图像生成使用 ai.api.nvidia.com 的专用 NIM 端点。
            document.getElementById('imageApiKey').value = '';
            document.getElementById('imageBaseUrl').value = '';
            document.getElementById('useImageGeneration').checked = true;
            document.getElementById('colorizePages').checked = false;
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
        image_size_tier: document.getElementById('imageSizeTier').value,
        use_ai_analysis: document.getElementById('useAiAnalysis').checked,
        video_engine: document.getElementById('videoEngine').value,
        video_api_key: document.getElementById('videoApiKey').value.trim(),
        video_base_url: document.getElementById('videoBaseUrl').value.trim(),
        video_model: document.getElementById('videoModel').value.trim(),
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
                html += `<div class="test-ok">✅ 图像模型 <b>${escapeHtml(data.image.model)}</b> 可用（返回类型: ${escapeHtml(info.response_type)}）</div>`;
            }
        } else if (data.image) {
            html += `<div class="test-err">❌ 图像模型 <b>${escapeHtml(data.image.model)}</b> 失败<br><span class="test-reply">${escapeHtml(data.image.error)}</span></div>`;
        }

        if (data.video && data.video.ok && data.video.configured) {
            html += `<div class="test-ok">✅ 视频模型 <b>${escapeHtml(data.video.model)}</b> 配置有效<br><span class="test-reply">${escapeHtml(data.video.message)}</span></div>`;
        } else if (data.video && !data.video.ok) {
            html += `<div class="test-err">❌ 视频模型 <b>${escapeHtml(data.video.model)}</b> 配置失败<br><span class="test-reply">${escapeHtml(data.video.error)}</span></div>`;
        }

        html += '<p class="test-hint">图像测试会真实生成一张测试图；Agnes 视频测试只检查配置格式，不会创建耗时的视频任务。</p>';

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
        alert('请填写 LLM API Key');
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
        image_size_tier: document.getElementById('imageSizeTier').value,
        art_style: artStyle,
        resolution: resolution,
        orientation: orientation,
        page_selection: pageSelection,
        use_ai_analysis: document.getElementById('useAiAnalysis').checked,
        ocr_engine: document.getElementById('ocrEngine').value,
        ocr_language: document.getElementById('ocrLanguage').value,
        pages_per_segment: parseInt(document.getElementById('pagesPerSegment').value) || 1,
        page_layout: document.getElementById('pageLayout').value,
        manual_duration: parseFloat(document.getElementById('manualDuration').value) || 5,
        manual_durations: document.getElementById('manualDurations').value.trim(),
        manual_narration: '',
        cover_mode: document.getElementById('coverMode').value,
        cover_path: state.coverPath || '',
        cover_duration: parseFloat(document.getElementById('coverDuration').value) || 3,
        first_page_is_cover: document.getElementById('firstPageIsCover').checked,
        use_tts: document.getElementById('useTts').checked,
        auto_duration_tts: document.getElementById('autoDurationTts').checked,
        tts_voice: document.getElementById('ttsVoice').value,
        dialogue_voice: document.getElementById('dialogueVoice').value,
        video_engine: document.getElementById('videoEngine').value,
        video_api_key: document.getElementById('videoApiKey').value.trim(),
        video_base_url: document.getElementById('videoBaseUrl').value.trim(),
        video_model: document.getElementById('videoModel').value.trim(),
        video_resolution_tier: document.getElementById('videoResolutionTier').value,
        video_frame_rate: parseInt(document.getElementById('videoFrameRate').value) || 24,
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

        if (res.status === 404) {
            if (state.pollTimer) clearInterval(state.pollTimer);
            state.pollTimer = null;
            state.taskId = null;
            showError('任务不存在或服务已重启。请返回配置页面重新开始生成。');
            return;
        }
        if (!res.ok) {
            throw new Error(data.error || `查询任务失败（HTTP ${res.status}）`);
        }

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
        if (data.status === 'waiting_user') {
            const box = document.getElementById('decisionBox');
            box.style.display = 'block';
            const isTtsConfirmation = (data.decision_stage || '').includes('TTS');
            const retryButton = document.getElementById('btnRetryAi');
            retryButton.style.display = (!isTtsConfirmation && data.decision_can_retry)
                ? 'inline-flex' : 'none';
            document.getElementById('decisionTitle').textContent = isTtsConfirmation
                ? '请确认伴读文字' : `${data.decision_stage || 'AI 处理'}失败`;
            document.getElementById('decisionMsg').textContent = isTtsConfirmation
                ? '请检查每个场景下方的文字，修改后确认；60 秒后将自动继续。'
                : (data.error || data.message);
            const promptBox = document.getElementById('decisionPrompt');
            const sceneEditor = document.getElementById('decisionScenes');
            const promptMode = (data.decision_stage || '').includes('提示词');
            const ttsMode = (data.decision_stage || '').includes('TTS');
            promptBox.style.display = (promptMode || ttsMode) ? 'block' : 'none';
            // 轮询每秒执行一次；用户获得焦点后不能再用服务端旧值覆盖正在编辑的内容。
            if (document.activeElement !== promptBox) {
            promptBox.value = data.decision_prompt || '';
            if (ttsMode) {
                promptBox.style.display = 'none';
                sceneEditor.style.display = 'grid';
                if (!sceneEditor.children.length || sceneEditor.dataset.taskId !== state.taskId) {
                    sceneEditor.dataset.taskId = state.taskId;
                    state.decisionEditing = false;
                    sceneEditor.innerHTML = (data.scenes || []).map((scene, index) => `
                        <div class="decision-scene-item">
                            <div class="decision-scene-title">${scene.is_cover
                                ? '片头封面（无旁白）'
                                : scene.is_pdf_cover
                                    ? `PDF 封面（第 ${escapeHtml(scene.page_source)} 页，无旁白）`
                                : `场景 ${escapeHtml(scene.scene_number || index + 1)}（PDF 第 ${escapeHtml((scene.page_sources || [scene.page_source]).join(','))} 页）`}</div>
                            <img class="decision-scene-image" src="/api/scene_image/${state.taskId}/${index}" alt="场景 ${index + 1}">
                            <textarea class="input decision-scene-prompt" data-scene-index="${index}" rows="4"
                                ${(scene.is_cover || scene.is_pdf_cover) ? 'disabled aria-label="封面无旁白"' : ''}>${escapeHtml((scene.is_cover || scene.is_pdf_cover) ? '' : (scene.narration || ''))}</textarea>
                        </div>`).join('');
                    sceneEditor.querySelectorAll('.decision-scene-prompt').forEach(field => {
                        const stopCountdown = () => {
                            state.decisionEditing = true;
                            if (state.decisionTimer) { clearTimeout(state.decisionTimer); state.decisionTimer = null; }
                            if (state.decisionCountdownInterval) { clearInterval(state.decisionCountdownInterval); state.decisionCountdownInterval = null; }
                            document.getElementById('decisionMsg').textContent = '已开始编辑，倒计时已停止。修改完成后请点击确认。';
                            document.getElementById('btnContinueWithoutAi').textContent = '确认文本并生成配音';
                        };
                        field.addEventListener('focus', stopCountdown);
                        field.addEventListener('input', stopCountdown);
                    });
                }
            } else {
                sceneEditor.style.display = 'none';
                sceneEditor.innerHTML = '';
            }
            }
            document.getElementById('btnContinueWithoutAi').textContent = promptMode ? '修改后重新提交' : (ttsMode ? '确认文本并生成配音' : '无 AI 继续');
            if (ttsMode && !state.decisionTimer
                    && !state.decisionEditing && !state.decisionSubmitting) {
                state.decisionSeconds = 60;
                state.decisionTimer = setTimeout(() => submitDecision('continue'), 60000);
                state.decisionCountdownInterval = setInterval(() => {
                    state.decisionSeconds = Math.max(0, state.decisionSeconds - 1);
                    const countdownText = `请检查每个场景下方的文字，修改后确认；${state.decisionSeconds} 秒后将自动继续。`;
                    document.getElementById('decisionMsg').textContent = countdownText;
                    document.getElementById('btnContinueWithoutAi').textContent = `确认文本并生成配音（${state.decisionSeconds}秒）`;
                }, 1000);
            }
        } else if (data.status === 'completed') {
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
            <p class="gallery-item-num">${scene.is_cover
                ? '片头封面'
                : scene.is_pdf_cover
                    ? `PDF 封面 · 第 ${escapeHtml(scene.page_source)} 页`
                : `场景 ${escapeHtml(scene.scene_number || i + 1)} · PDF 第 ${escapeHtml((scene.page_sources || [scene.page_source]).join(','))} 页`}</p>
            <p class="gallery-item-mood">${escapeHtml(scene.mood || '')}</p>
            <p class="gallery-item-narration">${escapeHtml((scene.narration || '').substring(0, 80))}...</p>
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
    document.getElementById('decisionBox').style.display = 'none';
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
