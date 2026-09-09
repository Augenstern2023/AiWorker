(function () {
    'use strict';

    const STAGE_LABELS = {
        project: '项目资料',
        brief: '创作任务书',
        characters: '人物',
        relationships: '人物关系',
        outline: '分集大纲',
        beats: '单集节拍',
        script: '场次剧本',
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
        const versions = (stage.versions || [])
            .map((item) => `#${item.id} v${item.version}`).join('、') || '-';

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
        if (payload.project && payload.project.name) {
            activeProject.name = payload.project.name;
            elements.projectName.textContent = activeProject.name;
        }
        elements.issues.replaceChildren();
        elements.stages.replaceChildren();
        elements.result.classList.add('d-none');
        showError(elements.error, '');

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
            setCheckStatus('全部检查通过，可生成锁定版本的生产包。');
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
            const response = await requestUi().requestKey(keyFor('preview'), (signal) =>
                window.aiworkerApi.request(
                    `/api/creative/projects/${project.id}/production-export/preview`, {signal},
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
            scope.textContent = `${record.episode_count} 集 · ${formatSize(record.size_bytes)}`;

            const state = document.createElement('td');
            state.appendChild(historyStateCell(record));

            const actions = document.createElement('td');
            actions.className = 'text-end';
            const pending = record.audit_status === 'pending';
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
            let filename = `${projectName}_生产包_${shortFingerprint(record.fingerprint)}.json`;
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
                        body: JSON.stringify({expected_fingerprint: currentFingerprint}),
                        signal,
                    },
                ));
            if (!activeProject || activeProject.id !== project.id) return;
            if (!response.ok) throw new Error(await readDetail(response, '生成生产包失败，请稍后重试'));
            const payload = await response.json();
            const record = payload.export;
            currentFingerprint = record.fingerprint;
            elements.result.textContent = '';
            elements.result.classList.remove('d-none');
            const done = document.createElement('div');
            done.className = 'fw-semibold';
            done.textContent = payload.created
                ? '生产包已生成并锁定版本。'
                : '该版本已有生产包，直接复用原产物。';
            const detail = document.createElement('div');
            detail.className = 'small mt-1';
            detail.textContent = `指纹 ${shortFingerprint(record.fingerprint)} · ${record.episode_count} 集 · `
                + `${formatSize(record.size_bytes)}。仅导出创作资料，尚未开始生产。`;
            const link = document.createElement('a');
            link.className = 'btn btn-sm btn-success mt-2';
            link.href = record.download_url;
            link.textContent = '下载生产包';
            link.addEventListener('click', (event) => {
                event.preventDefault();
                downloadExport(record);
            });
            elements.result.append(done, detail, document.createElement('br'), link);
            if (requestUi()) {
                requestUi().showToast({
                    kind: 'success',
                    title: '导出成功',
                    message: '已锁定版本的生产包可下载；仅导出创作资料，尚未开始生产。',
                });
            }
            loadHistory(1);
        } catch (reason) {
            if (window.aiworkerApi.isAbortError(reason)) return;
            if (!activeProject || activeProject.id !== project.id) return;
            showError(elements.error, reason.message || '生成生产包失败，请稍后重试');
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

    function open(projectId, options) {
        const id = String(projectId);
        if (activeProject && activeProject.id !== id) cancelAllRequests();
        activeProject = {id, name: (options && options.projectName) || `项目 ${id}`};
        trigger = (options && options.trigger) || null;
        currentFingerprint = null;
        historyPage = 1;
        historyTotal = 0;
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
        };
        elements.generate.addEventListener('click', generatePackage);
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
