(function () {
    'use strict';

    const STAGE_LABELS = {
        project: '项目资料',
        breakdown: '剧目拆解',
        brief: '创作任务书',
        characters: '人物',
        relationships: '人物关系',
        outline: '分集大纲',
        beats: '单集节拍',
        script: '场次剧本',
        video_parameters: '视频生成规格',
    };
    const PAGE_SIZE = 20;

    let modal = null;
    let elements = null;
    let trigger = null;
    let activeProject = null; // {id, name}
    let currentFingerprint = null;
    let generateInFlight = false;
    let historyPage = 1;
    let historyTotal = 0;
    let selectedEpisodeNumbers = null;
    let availableEpisodeNumbers = [];
    let projectTotalEpisodes = 0;
    let selectedVersionManifest = null;
    let currentVersionManifest = null;
    let versionCandidates = {};

    function requestUi() { return window.aiworkerUi; }

    function keyFor(kind) { return `production-export-${kind}:${activeProject ? activeProject.id : ''}`; }

    function cancelAllRequests() {
        ['preview', 'history', 'create', 'download'].forEach((kind) => {
            if (requestUi()) requestUi().cancelRequest(keyFor(kind));
        });
    }

    async function readDetail(response, fallback) {
        try {
            return await window.aiworkerApi.parseErrorDetail(response);
        } catch (ignored) {
            return fallback;
        }
    }

    function formatTime(seconds) {
        if (!seconds) return '-';
        return new Date(seconds * 1000).toLocaleString('zh-CN', {hour12: false});
    }

    function formatSize(bytes) {
        if (!Number.isFinite(bytes)) return '-';
        if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
        if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
        return `${bytes} B`;
    }

    function shortFingerprint(value) {
        return String(value || '').slice(0, 12);
    }

    function allEpisodes(total) {
        return Array.from({length: Math.max(0, total)}, (_, index) => index + 1);
    }

    function compactEpisodes(numbers) {
        if (!numbers || !numbers.length) return '无';
        const ranges = [];
        let start = numbers[0];
        let previous = numbers[0];
        numbers.slice(1).forEach((number) => {
            if (number === previous + 1) {
                previous = number;
                return;
            }
            ranges.push(start === previous ? `${start}` : `${start}-${previous}`);
            start = number;
            previous = number;
        });
        ranges.push(start === previous ? `${start}` : `${start}-${previous}`);
        return ranges.join('、');
    }

    function scopeLabel(record) {
        if (!record.export_scope || record.export_scope === 'full') {
            return `全剧 ${record.episode_count} 集`;
        }
        return `部分集：第 ${compactEpisodes(record.episode_numbers || [])} 集`;
    }

    function deriveAvailableEpisodes(payload) {
        const stages = payload.stages || [];
        const globalReady = stages
            .filter((stage) => !stage.episode_number)
            .every((stage) => stage.state === 'ready');
        if (!globalReady) return [];
        const states = new Map();
        stages.filter((stage) => stage.episode_number).forEach((stage) => {
            const number = Number(stage.episode_number);
            if (!states.has(number)) states.set(number, new Map());
            states.get(number).set(stage.stage, stage.state);
        });
        return allEpisodes(projectTotalEpisodes).filter((number) => {
            const episode = states.get(number);
            return episode && ['outline', 'beats', 'script'].every(
                (stage) => episode.get(stage) === 'ready',
            );
        });
    }

    function updateAvailableSummary() {
        const hasAvailable = availableEpisodeNumbers.length > 0;
        elements.availableOption.disabled = !hasAvailable;
        elements.availableSummary.textContent = hasAvailable
            ? `当前可用集：第 ${compactEpisodes(availableEpisodeNumbers)} 集（大纲、Beats、剧本均已锁定）`
            : '当前没有同时完成大纲、Beats 和剧本锁定的集数。';
    }

    function updateScopeFields() {
        const mode = elements.scope.value;
        elements.rangeFields.classList.toggle('d-none', mode !== 'range');
        elements.customFields.classList.toggle('d-none', mode !== 'custom');
    }

    function invalidateScopeConfirmation() {
        currentFingerprint = null;
        elements.generate.disabled = true;
        if (projectTotalEpisodes) {
            showError(elements.scopeError, '导出范围已修改，请点击“检查此范围”重新确认。');
        }
    }

    function handleScopeChange() {
        updateScopeFields();
        invalidateScopeConfirmation();
    }

    function parseCustomEpisodes(raw) {
        const values = new Set();
        const parts = String(raw || '').trim().split(/[、,，\s]+/).filter(Boolean);
        if (!parts.length) throw new Error('请输入需要导出的集数');
        parts.forEach((part) => {
            const match = part.match(/^(\d+)(?:\s*[-至~～]\s*(\d+))?$/);
            if (!match) throw new Error(`无法识别集数“${part}”`);
            const start = Number(match[1]);
            const end = Number(match[2] || match[1]);
            if (start < 1 || end < start || end > projectTotalEpisodes) {
                throw new Error(`集数必须在 1-${projectTotalEpisodes} 范围内`);
            }
            for (let number = start; number <= end; number += 1) values.add(number);
        });
        return Array.from(values).sort((left, right) => left - right);
    }

    function requestedEpisodesFromControls() {
        const mode = elements.scope.value;
        if (mode === 'all') return null;
        if (mode === 'available') {
            if (!availableEpisodeNumbers.length) throw new Error('当前没有可导出的完整锁定集');
            return [...availableEpisodeNumbers];
        }
        if (mode === 'custom') return parseCustomEpisodes(elements.customEpisodes.value);
        const start = Number(elements.rangeStart.value);
        const end = Number(elements.rangeEnd.value);
        if (!Number.isInteger(start) || !Number.isInteger(end)
            || start < 1 || end < start || end > projectTotalEpisodes) {
            throw new Error(`连续区间必须在 1-${projectTotalEpisodes} 范围内`);
        }
        return Array.from({length: end - start + 1}, (_, index) => start + index);
    }

    function previewUrl(projectId) {
        const base = `/api/creative/projects/${projectId}/production-export/preview`;
        if (!selectedEpisodeNumbers) return base;
        const query = new URLSearchParams();
        selectedEpisodeNumbers.forEach((number) => query.append('episode_numbers', number));
        return `${base}?${query.toString()}`;
    }

    function cloneValue(value) {
        return JSON.parse(JSON.stringify(value || {}));
    }

    function manifestHasValues(value) {
        return value && typeof value === 'object' && Object.keys(value).length > 0;
    }

    function readableVersion(value) {
        const version = Number(value);
        return Number.isInteger(version) && version > 0 ? `第 ${version} 版` : '未标版本';
    }

    function fillVersionSelect(select, options, selectedRef) {
        select.replaceChildren();
        (options || []).forEach((item) => {
            const option = document.createElement('option');
            option.value = String(item.resource_ref);
            const isSelected = String(item.resource_ref) === String(selectedRef || '');
            option.textContent = `${readableVersion(item.version)}`
                + (isSelected ? '（当前选择）' : '')
                + (!item.compatible ? '（不兼容）' : '');
            option.disabled = !item.compatible;
            option.dataset.version = String(item.version || 0);
            option.title = item.disabled_reason || '';
            option.selected = isSelected;
            select.appendChild(option);
        });
        select.disabled = !(options || []).some((item) => item.compatible);
    }

    function selectedManifestRef(stage, number) {
        if (!selectedVersionManifest) return '';
        if (!number) return selectedVersionManifest[stage]?.resource_ref || '';
        return selectedVersionManifest.episodes?.[String(number)]?.[stage]?.resource_ref || '';
    }

    function setManifestItem(stage, option, number) {
        if (!selectedVersionManifest) selectedVersionManifest = {};
        const item = {
            resource_ref: String(option.value),
            version: Number(option.dataset.version || 0),
        };
        if (!number) {
            selectedVersionManifest[stage] = item;
            if (stage === 'brief') {
                delete selectedVersionManifest.characters;
                delete selectedVersionManifest.outline;
                selectedVersionManifest.episodes = {};
            } else if (stage === 'characters') {
                delete selectedVersionManifest.outline;
                selectedVersionManifest.episodes = {};
            } else if (stage === 'outline') {
                selectedVersionManifest.episodes = {};
            }
            return;
        }
        if (!selectedVersionManifest.episodes) selectedVersionManifest.episodes = {};
        if (!selectedVersionManifest.episodes[String(number)]) {
            selectedVersionManifest.episodes[String(number)] = {};
        }
        selectedVersionManifest.episodes[String(number)][stage] = item;
        if (stage === 'beats') {
            delete selectedVersionManifest.episodes[String(number)].script;
        }
    }

    function requestVersionChange(stage, select, number) {
        const option = select.selectedOptions[0];
        if (!option) return;
        setManifestItem(stage, option, number);
        currentFingerprint = null;
        elements.generate.disabled = true;
        loadPreview();
    }

    function renderVersionControls(selection) {
        const strategy = elements.versionStrategy.value;
        elements.versionControls.classList.toggle('d-none', strategy !== 'custom');
        elements.versionSummary.textContent = strategy === 'custom'
            ? '自定义组合会在导出前重新校验并固化。'
            : '默认使用项目当前锁定的完整版本链。';
        const warnings = selection?.warnings || [];
        showError(elements.versionWarning, warnings.join(' '));
        if (strategy !== 'custom') return;

        const candidates = versionCandidates || {};
        fillVersionSelect(
            elements.versionBrief, candidates.brief || [],
            selectedManifestRef('brief'),
        );
        fillVersionSelect(
            elements.versionCharacters, candidates.characters || [],
            selectedManifestRef('characters'),
        );
        fillVersionSelect(
            elements.versionOutline, candidates.outline || [],
            selectedManifestRef('outline'),
        );
        elements.episodeVersionControls.replaceChildren();
        const episodeCandidates = candidates.episodes || {};
        const numbers = Object.keys(episodeCandidates).map(Number).sort((left, right) => left - right);
        if (!numbers.length) return;
        const table = document.createElement('table');
        table.className = 'table table-sm align-middle mb-0';
        table.innerHTML = '<thead><tr><th>集数</th><th>Beats</th><th>场次剧本</th></tr></thead>';
        const body = document.createElement('tbody');
        numbers.forEach((number) => {
            const row = document.createElement('tr');
            const numberCell = document.createElement('td');
            numberCell.textContent = `第 ${number} 集`;
            const beatsCell = document.createElement('td');
            const beatsSelect = document.createElement('select');
            beatsSelect.className = 'form-select form-select-sm';
            fillVersionSelect(
                beatsSelect, episodeCandidates[String(number)].beats || [],
                selectedManifestRef('beats', number),
            );
            beatsSelect.addEventListener('change', () => requestVersionChange('beats', beatsSelect, number));
            beatsCell.appendChild(beatsSelect);
            const scriptCell = document.createElement('td');
            const scriptSelect = document.createElement('select');
            scriptSelect.className = 'form-select form-select-sm';
            fillVersionSelect(
                scriptSelect, episodeCandidates[String(number)].script || [],
                selectedManifestRef('script', number),
            );
            scriptSelect.addEventListener('change', () => requestVersionChange('script', scriptSelect, number));
            scriptCell.appendChild(scriptSelect);
            row.append(numberCell, beatsCell, scriptCell);
            body.appendChild(row);
        });
        table.appendChild(body);
        elements.episodeVersionControls.appendChild(table);
    }

    function handleVersionStrategyChange() {
        if (elements.versionStrategy.value === 'custom') {
            selectedVersionManifest = cloneValue(currentVersionManifest);
        } else {
            selectedVersionManifest = null;
        }
        currentFingerprint = null;
        elements.generate.disabled = true;
        renderVersionControls({warnings: []});
        loadPreview();
    }

    function showError(box, message) {
        box.textContent = message || '';
        box.classList.toggle('d-none', !message);
    }

    function setCheckStatus(message) {
        elements.checkStatus.textContent = message;
        elements.checkStatus.classList.toggle('d-none', !message);
    }

    function stageRow(stage) {
        const row = document.createElement('tr');
        const icon = stage.state === 'ready'
            ? '<i class="bi bi-check-circle-fill text-success" aria-hidden="true"></i>'
            : stage.state === 'blocked'
                ? '<i class="bi bi-dash-circle text-muted" aria-hidden="true"></i>'
                : '<i class="bi bi-exclamation-triangle-fill text-danger" aria-hidden="true"></i>';
        const versions = Array.from(new Set(
            (stage.versions || []).map((item) => Number(item.version)),
        )).filter((version) => Number.isInteger(version) && version > 0)
            .map(readableVersion).join('、') || '-';

        const stageCell = document.createElement('td');
        stageCell.innerHTML = `${icon} <span></span>`;
        stageCell.querySelector('span').textContent = STAGE_LABELS[stage.stage] || stage.stage;

        const scopeCell = document.createElement('td');
        scopeCell.textContent = stage.episode_number ? `第 ${stage.episode_number} 集` : '全剧';

        const versionCell = document.createElement('td');
        versionCell.textContent = stage.state === 'ready' ? versions : '—';

        const messageCell = document.createElement('td');
        messageCell.textContent = stage.message || '';

        row.append(stageCell, scopeCell, versionCell, messageCell);
        return row;
    }

    function renderPreview(payload) {
        currentFingerprint = payload.fingerprint || null;
        const versionSelection = payload.version_selection || {};
        if (manifestHasValues(versionSelection.manifest)) {
            currentVersionManifest = cloneValue(versionSelection.manifest);
            versionCandidates = cloneValue(versionSelection.candidates);
            if (elements.versionStrategy.value === 'custom') {
                selectedVersionManifest = cloneValue(versionSelection.manifest);
            }
        }
        renderVersionControls(versionSelection);
        if (payload.project && payload.project.name) {
            activeProject.name = payload.project.name;
            elements.projectName.textContent = activeProject.name;
        }
        projectTotalEpisodes = Number(
            (payload.selection && payload.selection.project_total_episodes)
            || (payload.project && payload.project.target_episode_count)
            || 0,
        );
        elements.rangeStart.max = projectTotalEpisodes;
        elements.rangeEnd.max = projectTotalEpisodes;
        if (elements.scope.value === 'all') {
            elements.rangeStart.value = 1;
            elements.rangeEnd.value = projectTotalEpisodes;
        }
        const selection = payload.selection || {};
        selectedEpisodeNumbers = Array.isArray(selection.episode_numbers)
            ? selection.episode_numbers.map(Number)
            : allEpisodes(projectTotalEpisodes);
        if (selectedEpisodeNumbers.length === projectTotalEpisodes
            && selectedEpisodeNumbers.every((number, index) => number === index + 1)) {
            availableEpisodeNumbers = deriveAvailableEpisodes(payload);
            updateAvailableSummary();
        }
        elements.issues.replaceChildren();
        elements.stages.replaceChildren();
        elements.result.classList.add('d-none');
        showError(elements.error, '');
        showError(elements.scopeError, '');

        (payload.issues || []).forEach((issue) => {
            const item = document.createElement('div');
            item.className = 'alert alert-warning small py-2 mb-2 d-flex flex-wrap justify-content-between align-items-center gap-2';
            const text = document.createElement('span');
            text.textContent = `${STAGE_LABELS[issue.stage] || issue.stage}`
                + (issue.episode_number ? ` 第 ${issue.episode_number} 集` : '')
                + `：${issue.message || ''}`;
            item.appendChild(text);
            if (issue.edit_url) {
                const link = document.createElement('a');
                link.className = 'btn btn-sm btn-outline-warning flex-shrink-0';
                link.href = issue.edit_url;
                link.textContent = '前往处理';
                item.appendChild(link);
            }
            elements.issues.appendChild(item);
        });

        const table = document.createElement('table');
        table.className = 'table table-sm align-middle mb-0';
        const headRow = document.createElement('tr');
        ['阶段', '范围', '锁定版本', '检查结果'].forEach((label) => {
            const th = document.createElement('th');
            th.textContent = label;
            headRow.appendChild(th);
        });
        const thead = document.createElement('thead');
        thead.appendChild(headRow);
        table.appendChild(thead);
        const tbody = document.createElement('tbody');
        (payload.stages || []).forEach((stage) => tbody.appendChild(stageRow(stage)));
        table.appendChild(tbody);
        elements.stages.appendChild(table);

        if (payload.ready && payload.fingerprint) {
            setCheckStatus('全部检查通过，可生成锁定版本的项目文档包。');
            elements.generate.disabled = generateInFlight;
        } else {
            setCheckStatus('存在阻塞项，请先在对应页面完成并锁定内容。');
            elements.generate.disabled = true;
        }
    }

    async function loadPreview() {
        if (!activeProject) return;
        const project = activeProject;
        setCheckStatus('正在执行完整性检查…');
        elements.generate.disabled = true;
        try {
            const custom = elements.versionStrategy.value === 'custom';
            const response = await requestUi().requestKey(keyFor('preview'), (signal) =>
                window.aiworkerApi.request(
                    previewUrl(project.id), custom ? {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            episode_numbers: selectedEpisodeNumbers,
                            version_strategy: 'custom',
                            version_manifest: selectedVersionManifest || {},
                        }),
                        signal,
                    } : {signal},
                ));
            if (!activeProject || activeProject.id !== project.id) return;
            if (!response.ok) throw new Error(await readDetail(response, '导出检查失败'));
            renderPreview(await response.json());
        } catch (reason) {
            if (window.aiworkerApi.isAbortError(reason)) return;
            if (!activeProject || activeProject.id !== project.id) return;
            currentFingerprint = null;
            elements.generate.disabled = true;
            setCheckStatus('');
            showError(elements.error, reason.message || '导出检查失败');
        }
    }

    function historyStateCell(record) {
        const wrap = document.createElement('div');
        wrap.className = 'd-flex flex-column gap-1 align-items-start';
        if (record.audit_status === 'pending') {
            const pending = document.createElement('span');
            pending.className = 'badge text-bg-warning';
            pending.textContent = '审计待补记';
            wrap.appendChild(pending);
        }
        const tag = document.createElement('span');
        if (currentFingerprint && record.fingerprint === currentFingerprint) {
            tag.className = 'badge text-bg-success';
            tag.textContent = '当前版本';
        } else if (currentFingerprint) {
            tag.className = 'badge text-bg-secondary';
            tag.textContent = '历史版本';
        } else {
            tag.className = 'badge text-bg-warning';
            tag.textContent = '当前版本无法验证';
        }
        wrap.appendChild(tag);
        return wrap;
    }

    function renderHistory(payload) {
        elements.historyStatus.classList.add('d-none');
        historyTotal = payload.total || 0;
        elements.historyTotal.textContent = historyTotal
            ? `共 ${historyTotal} 条，第 ${historyPage} 页`
            : '';
        elements.historyPrev.disabled = historyPage <= 1;
        elements.historyNext.disabled = historyPage * PAGE_SIZE >= historyTotal;
        elements.historyList.replaceChildren();

        const items = payload.items || [];
        if (!items.length) {
            const row = document.createElement('tr');
            const cell = document.createElement('td');
            cell.colSpan = 5;
            cell.className = 'text-center text-muted py-4';
            cell.textContent = '暂无历史导出记录。';
            row.appendChild(cell);
            elements.historyList.appendChild(row);
            return;
        }
        items.forEach((record) => {
            const row = document.createElement('tr');

            const created = document.createElement('td');
            const who = document.createElement('div');
            who.textContent = record.created_by_name || record.created_by;
            const when = document.createElement('div');
            when.className = 'small text-muted';
            when.textContent = formatTime(record.created_at);
            created.append(who, when);

            const version = document.createElement('td');
            const code = document.createElement('code');
            code.textContent = shortFingerprint(record.fingerprint);
            code.title = record.fingerprint;
            version.appendChild(code);

            const scope = document.createElement('td');
            const format = String(record.artifact_format || 'json').toUpperCase();
            scope.textContent = `${scopeLabel(record)} · ${format} · ${formatSize(record.size_bytes)}`;

            const state = document.createElement('td');
            state.appendChild(historyStateCell(record));

            const actions = document.createElement('td');
            actions.className = 'text-end';
            const pending = record.audit_status === 'pending';
            if (manifestHasValues(record.version_manifest)) {
                const reuse = document.createElement('button');
                reuse.type = 'button';
                reuse.className = 'btn btn-sm btn-outline-primary me-1';
                reuse.textContent = '复用版本';
                reuse.addEventListener('click', () => reuseHistoryVersion(record));
                actions.appendChild(reuse);
            }
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn btn-sm btn-outline-success';
            button.textContent = pending ? '暂不可下载' : '下载';
            button.disabled = pending;
            if (!pending) button.addEventListener('click', () => downloadExport(record));
            actions.appendChild(button);

            row.append(created, version, scope, state, actions);
            elements.historyList.appendChild(row);
        });
    }

    function reuseHistoryVersion(record) {
        const numbers = (record.episode_numbers || []).map(Number);
        selectedEpisodeNumbers = record.export_scope === 'full' ? null : numbers;
        elements.scope.value = record.export_scope === 'full' ? 'all' : 'custom';
        elements.customEpisodes.value = compactEpisodes(numbers);
        updateScopeFields();
        elements.versionStrategy.value = 'custom';
        selectedVersionManifest = cloneValue(record.version_manifest);
        currentFingerprint = null;
        elements.generate.disabled = true;
        const checkTab = document.getElementById('production-export-tab-check');
        if (checkTab && window.bootstrap.Tab?.getOrCreateInstance) {
            window.bootstrap.Tab.getOrCreateInstance(checkTab).show();
        } else if (checkTab) {
            checkTab.click();
        }
        renderVersionControls({warnings: []});
        loadPreview();
    }

    async function loadHistory(page) {
        if (!activeProject) return;
        const project = activeProject;
        historyPage = Math.max(1, page);
        elements.historyStatus.textContent = '正在加载历史导出…';
        elements.historyStatus.classList.remove('d-none');
        showError(elements.historyError, '');
        try {
            const response = await requestUi().requestKey(keyFor('history'), (signal) =>
                window.aiworkerApi.request(
                    `/api/creative/projects/${project.id}/production-exports`
                    + `?page=${historyPage}&page_size=${PAGE_SIZE}`,
                    {signal},
                ));
            if (!activeProject || activeProject.id !== project.id) return;
            if (!response.ok) throw new Error(await readDetail(response, '读取历史导出失败'));
            const payload = await response.json();
            if (!activeProject || activeProject.id !== project.id) return;
            renderHistory(payload);
        } catch (reason) {
            if (window.aiworkerApi.isAbortError(reason)) return;
            if (!activeProject || activeProject.id !== project.id) return;
            elements.historyStatus.classList.add('d-none');
            elements.historyList.replaceChildren();
            showError(elements.historyError, reason.message || '读取历史导出失败');
        }
    }

    async function downloadExport(record) {
        const projectId = activeProject ? activeProject.id : null;
        const projectName = elements.projectName.textContent || (activeProject ? activeProject.name : '项目');
        try {
            const response = await requestUi().requestKey(keyFor('download'), (signal) =>
                window.aiworkerApi.request(record.download_url, {signal}));
            if (!response.ok) {
                throw new Error(await readDetail(response, '下载失败，请稍后重试'));
            }
            const artifactFormat = record.artifact_format || 'zip';
            const packageLabel = artifactFormat === 'zip' ? '项目文档包' : '生产包';
            let filename = `${projectName}_${scopeLabel(record)}_${packageLabel}_`
                + `${shortFingerprint(record.fingerprint)}.${artifactFormat}`;
            const disposition = response.headers.get('content-disposition') || '';
            const match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
            if (match) filename = decodeURIComponent(match[1]);
            const url = URL.createObjectURL(await response.blob());
            const anchor = document.createElement('a');
            anchor.href = url;
            anchor.download = filename;
            document.body.appendChild(anchor);
            anchor.click();
            anchor.remove();
            URL.revokeObjectURL(url);
        } catch (reason) {
            if (window.aiworkerApi.isAbortError(reason)) return;
            if (requestUi()) {
                requestUi().showToast({
                    kind: 'danger',
                    title: '下载失败',
                    message: reason.message || '下载失败，请稍后重试',
                });
            }
            if (activeProject && activeProject.id === projectId) loadHistory(historyPage);
        }
    }

    async function generatePackage() {
        if (!activeProject || !currentFingerprint || generateInFlight) return;
        const project = activeProject;
        generateInFlight = true;
        if (requestUi()) requestUi().setButtonLoading(elements.generate, {loading: true, label: '正在生成…'});
        showError(elements.error, '');
        try {
            const response = await requestUi().requestKey(keyFor('create'), (signal) =>
                window.aiworkerApi.request(
                    `/api/creative/projects/${project.id}/production-exports`,
                    {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            expected_fingerprint: currentFingerprint,
                            episode_numbers: selectedEpisodeNumbers,
                            version_strategy: elements.versionStrategy.value,
                            version_manifest: elements.versionStrategy.value === 'custom'
                                ? selectedVersionManifest
                                : undefined,
                        }),
                        signal,
                    },
                ));
            if (!activeProject || activeProject.id !== project.id) return;
            if (!response.ok) throw new Error(await readDetail(response, '生成项目文档包失败，请稍后重试'));
            const payload = await response.json();
            const record = payload.export;
            currentFingerprint = record.fingerprint;
            elements.result.textContent = '';
            elements.result.classList.remove('d-none');
            const done = document.createElement('div');
            done.className = 'fw-semibold';
            done.textContent = payload.created
                ? '项目文档包已生成并锁定版本。'
                : '该版本已有项目文档包，直接复用原产物。';
            const detail = document.createElement('div');
            detail.className = 'small mt-1';
            detail.textContent = `指纹 ${shortFingerprint(record.fingerprint)} · ${scopeLabel(record)} · `
                + `ZIP · ${formatSize(record.size_bytes)}。仅导出创作资料，尚未开始生产。`;
            const link = document.createElement('a');
            link.className = 'btn btn-sm btn-success mt-2';
            link.href = record.download_url;
            link.textContent = '下载 ZIP 文档包';
            link.addEventListener('click', (event) => {
                event.preventDefault();
                downloadExport(record);
            });
            elements.result.append(done, detail, document.createElement('br'), link);
            if (requestUi()) {
                requestUi().showToast({
                    kind: 'success',
                    title: '导出成功',
                    message: '已锁定版本的 ZIP 文档包可下载；仅导出创作资料，尚未开始生产。',
                });
            }
            loadHistory(1);
        } catch (reason) {
            if (window.aiworkerApi.isAbortError(reason)) return;
            if (!activeProject || activeProject.id !== project.id) return;
            currentFingerprint = null;
            elements.generate.disabled = true;
            showError(elements.error, reason.message || '生成项目文档包失败，请稍后重试');
            // A version conflict means the earlier preview no longer matches, so
            // require a fresh confirmation instead of exporting an unreviewed version.
            loadPreview();
        } finally {
            generateInFlight = false;
            if (requestUi()) requestUi().setButtonLoading(elements.generate, {loading: false});
            if (activeProject && activeProject.id === project.id && currentFingerprint) {
                elements.generate.disabled = false;
            }
        }
    }

    function applyScopeSelection() {
        try {
            selectedEpisodeNumbers = requestedEpisodesFromControls();
            currentFingerprint = null;
            elements.generate.disabled = true;
            showError(elements.scopeError, '');
            loadPreview();
        } catch (reason) {
            currentFingerprint = null;
            elements.generate.disabled = true;
            showError(elements.scopeError, reason.message || '导出范围无效');
        }
    }

    function open(projectId, options) {
        const id = String(projectId);
        if (activeProject && activeProject.id !== id) cancelAllRequests();
        activeProject = {id, name: (options && options.projectName) || `项目 ${id}`};
        trigger = (options && options.trigger) || null;
        currentFingerprint = null;
        historyPage = 1;
        historyTotal = 0;
        selectedEpisodeNumbers = null;
        availableEpisodeNumbers = [];
        projectTotalEpisodes = 0;
        selectedVersionManifest = null;
        currentVersionManifest = null;
        versionCandidates = {};
        elements.versionStrategy.value = 'current';
        elements.scope.value = 'all';
        elements.rangeStart.value = 1;
        elements.rangeEnd.value = 1;
        elements.customEpisodes.value = '';
        elements.availableOption.disabled = true;
        elements.availableSummary.textContent = '正在检查当前可用集…';
        updateScopeFields();
        elements.projectName.textContent = activeProject.name;
        elements.result.classList.add('d-none');
        showError(elements.error, '');
        showError(elements.historyError, '');
        elements.historyList.replaceChildren();
        elements.generate.disabled = true;
        modal.show();
        loadPreview();
        loadHistory(1);
    }

    function mount() {
        if (modal) return true;
        const modalElement = document.getElementById('productionExportModal');
        if (!modalElement) return false;
        modal = window.bootstrap.Modal.getOrCreateInstance(modalElement);
        elements = {
            projectName: document.getElementById('production-export-project-name'),
            checkStatus: document.getElementById('production-export-check-status'),
            error: document.getElementById('production-export-error'),
            issues: document.getElementById('production-export-issues'),
            stages: document.getElementById('production-export-stages'),
            result: document.getElementById('production-export-result'),
            generate: document.getElementById('production-export-generate'),
            historyStatus: document.getElementById('production-export-history-status'),
            historyError: document.getElementById('production-export-history-error'),
            historyList: document.getElementById('production-export-history-list'),
            historyTotal: document.getElementById('production-export-history-total'),
            historyPrev: document.getElementById('production-export-history-prev'),
            historyNext: document.getElementById('production-export-history-next'),
            scope: document.getElementById('production-export-scope'),
            rangeFields: document.getElementById('production-export-range-fields'),
            rangeStart: document.getElementById('production-export-range-start'),
            rangeEnd: document.getElementById('production-export-range-end'),
            customFields: document.getElementById('production-export-custom-fields'),
            customEpisodes: document.getElementById('production-export-custom-episodes'),
            availableOption: document.getElementById('production-export-available-option'),
            availableSummary: document.getElementById('production-export-available-summary'),
            scopeApply: document.getElementById('production-export-scope-apply'),
            scopeError: document.getElementById('production-export-scope-error'),
            versionStrategy: document.getElementById('production-export-version-strategy'),
            versionSummary: document.getElementById('production-export-version-summary'),
            versionWarning: document.getElementById('production-export-version-warning'),
            versionControls: document.getElementById('production-export-version-controls'),
            versionBrief: document.getElementById('production-export-version-brief'),
            versionCharacters: document.getElementById('production-export-version-characters'),
            versionOutline: document.getElementById('production-export-version-outline'),
            episodeVersionControls: document.getElementById('production-export-episode-version-controls'),
        };
        elements.generate.addEventListener('click', generatePackage);
        elements.scope.addEventListener('change', handleScopeChange);
        elements.rangeStart.addEventListener('input', invalidateScopeConfirmation);
        elements.rangeEnd.addEventListener('input', invalidateScopeConfirmation);
        elements.customEpisodes.addEventListener('input', invalidateScopeConfirmation);
        elements.scopeApply.addEventListener('click', applyScopeSelection);
        elements.versionStrategy.addEventListener('change', handleVersionStrategyChange);
        elements.versionBrief.addEventListener('change', () => requestVersionChange('brief', elements.versionBrief));
        elements.versionCharacters.addEventListener('change', () => requestVersionChange('characters', elements.versionCharacters));
        elements.versionOutline.addEventListener('change', () => requestVersionChange('outline', elements.versionOutline));
        elements.historyPrev.addEventListener('click', () => loadHistory(historyPage - 1));
        elements.historyNext.addEventListener('click', () => loadHistory(historyPage + 1));
        modalElement.addEventListener('hidden.bs.modal', () => {
            cancelAllRequests();
            activeProject = null;
            currentFingerprint = null;
            if (trigger && trigger.isConnected) trigger.focus();
        });
        return true;
    }

    window.aiworkerProductionExport = {
        open(projectId, options) {
            if (!mount()) return;
            open(projectId, options);
        },
    };
})();
