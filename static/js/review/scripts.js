(function () {
    'use strict';

    const source = document.getElementById('script-review-source');
    const mode = document.getElementById('script-review-mode');
    const project = document.getElementById('script-review-project');
    const projectWrap = document.getElementById('script-review-project-wrap');
    const fileWrap = document.getElementById('script-review-file-wrap');
    const scriptFile = document.getElementById('script-review-file');
    const episodes = document.getElementById('script-review-episodes');
    const reviewPromptWrap = document.getElementById('script-review-prompt-wrap');
    const reviewPromptTemplate = document.getElementById('script-review-prompt-template');
    const reviewPromptRequirements = document.getElementById('script-review-prompt-requirements');
    const reviewPromptDescription = document.getElementById('script-review-prompt-description');
    const submit = document.getElementById('script-review-submit');
    const result = document.getElementById('script-review-result');
    const history = document.getElementById('script-review-history');
    const error = document.getElementById('script-review-error');
    const reviewProgressElement = document.getElementById('scriptReviewProgressModal');
    const reviewProgressScope = document.getElementById('script-review-progress-scope');
    const reviewProgressStatus = document.getElementById('script-review-progress-status');
    const reviewProgressValue = document.getElementById('script-review-progress-value');
    const reviewProgressBar = document.getElementById('script-review-progress-bar');
    const reviewProgressElapsed = document.getElementById('script-review-progress-elapsed');
    const reviewProgressError = document.getElementById('script-review-progress-error');
    const reviewProgressViewReport = document.getElementById('script-review-progress-view-report');
    const optimizationProgressElement = document.getElementById('scriptOptimizationProgressModal');
    const optimizationProgressScope = document.getElementById('script-optimization-progress-scope');
    const optimizationProgressStatus = document.getElementById('script-optimization-progress-status');
    const optimizationProgressValue = document.getElementById('script-optimization-progress-value');
    const optimizationProgressBar = document.getElementById('script-optimization-progress-bar');
    const optimizationProgressElapsed = document.getElementById('script-optimization-progress-elapsed');
    const optimizationProgressError = document.getElementById('script-optimization-progress-error');
    const optimizationProgressViewDrafts = document.getElementById('script-optimization-progress-view-drafts');
    const returnReviewId = Number(new URLSearchParams(window.location.search).get('review_id') || 0);
    let activeReviewId = null;
    let reviewProjects = [];
    let selectedProject = null;
    const optimizationTimers = new Map();
    const reviewPollTimers = new Map();
    const reviewProgress = { reviewId: null, startedAt: 0, elapsedTimer: null };
    const optimizationProgress = { optimizationId: null, startedAt: 0, elapsedTimer: null };
    const reviewProgressModal = reviewProgressElement && window.bootstrap
        ? window.bootstrap.Modal.getOrCreateInstance(reviewProgressElement)
        : null;
    const optimizationProgressModal = optimizationProgressElement && window.bootstrap
        ? window.bootstrap.Modal.getOrCreateInstance(optimizationProgressElement)
        : null;

    if (!source || !mode || !project || !projectWrap || !fileWrap || !scriptFile || !episodes || !submit || !result || !history || !error) return;

    function showError(message = '') {
        error.textContent = message;
        error.classList.toggle('d-none', !message);
    }

    async function readJson(response) {
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.detail || '请求失败');
        return payload;
    }

    function label(value) {
        return { queued: '等待审核', running: '审核中', cancelled: '已取消', pending_confirmation: '待人工确认', confirmed: '已确认', stale: '已过期', failed: '审核失败' }[value] || value || '-';
    }

    function reviewScope(payload) {
        if (payload.source_type === 'text') return '剧本文件 · 单集审核';
        const projectName = reviewProjects.find((item) => Number(item.id) === Number(payload.project_id))?.name || '创作项目';
        const modeName = { single: '单集', multiple: '多集', collection: '合集' }[payload.mode] || '剧本';
        const selected = payload.episode_numbers || [];
        return `${projectName} · ${modeName}${selected.length ? ` · ${selected.map((number) => `第 ${number} 集`).join('、')}` : ''}`;
    }

    function formatReviewElapsed(milliseconds) {
        const seconds = Math.max(0, Math.floor(milliseconds / 1000));
        const minutes = Math.floor(seconds / 60);
        return minutes ? `${minutes} 分 ${seconds % 60} 秒` : `${seconds} 秒`;
    }

    function refreshReviewProgressElapsed() {
        if (!reviewProgress.startedAt || !reviewProgressElapsed) return;
        reviewProgressElapsed.textContent = `已等待 ${formatReviewElapsed(Date.now() - reviewProgress.startedAt)}`;
    }

    function showReviewProgress(scope = '') {
        if (!reviewProgressModal) return;
        reviewProgress.reviewId = null;
        reviewProgress.startedAt = Date.now();
        if (reviewProgressScope) reviewProgressScope.textContent = scope;
        if (reviewProgressStatus) {
            reviewProgressStatus.className = 'alert alert-info py-2';
            reviewProgressStatus.textContent = '正在创建审核任务';
        }
        if (reviewProgressValue) reviewProgressValue.textContent = '正在启动';
        if (reviewProgressBar) reviewProgressBar.className = 'progress-bar progress-bar-striped progress-bar-animated';
        if (reviewProgressError) {
            reviewProgressError.textContent = '';
            reviewProgressError.classList.add('d-none');
        }
        reviewProgressViewReport?.classList.add('d-none');
        refreshReviewProgressElapsed();
        if (reviewProgress.elapsedTimer) window.clearInterval(reviewProgress.elapsedTimer);
        reviewProgress.elapsedTimer = window.setInterval(refreshReviewProgressElapsed, 1000);
        reviewProgressModal.show();
    }

    function hideReviewProgress() {
        reviewProgressModal?.hide();
    }

    function updateReviewProgress(review, message = '') {
        if (!reviewProgressModal || reviewProgress.reviewId !== review.id) return;
        const isRunning = ['queued', 'running'].includes(review.status);
        const completed = review.status === 'pending_confirmation';
        const cancelled = review.status === 'cancelled';
        const failed = review.status === 'failed';
        const unsuccessful = failed || cancelled;
        if (reviewProgressStatus) {
            reviewProgressStatus.className = `alert ${failed ? 'alert-danger' : cancelled ? 'alert-warning' : completed ? 'alert-success' : 'alert-info'} py-2`;
            reviewProgressStatus.textContent = message || (failed ? '审核任务失败' : cancelled ? '审核已取消' : completed ? '审核完成，审核报告已生成' : label(review.status));
        }
        if (reviewProgressValue) reviewProgressValue.textContent = failed ? '审核失败' : cancelled ? '已取消' : completed ? '审核完成' : '处理中';
        if (reviewProgressBar) {
            reviewProgressBar.className = `progress-bar progress-bar-striped${isRunning ? ' progress-bar-animated' : ''}${failed ? ' bg-danger' : cancelled ? ' bg-warning' : completed ? ' bg-success' : ''}`;
            reviewProgressBar.style.width = isRunning ? '100%' : '100%';
        }
        if (unsuccessful && reviewProgressError) {
            reviewProgressError.textContent = review.error || review.error_message || (cancelled ? '审核任务已取消。' : '审核任务执行失败，请稍后重试。');
            reviewProgressError.classList.remove('d-none');
        }
        reviewProgressViewReport?.classList.toggle('d-none', !completed);
        if (!isRunning && reviewProgress.elapsedTimer) {
            window.clearInterval(reviewProgress.elapsedTimer);
            reviewProgress.elapsedTimer = null;
            refreshReviewProgressElapsed();
        }
    }

    function showReviewProgressError(message) {
        if (!reviewProgressModal) return;
        if (reviewProgressStatus) {
            reviewProgressStatus.className = 'alert alert-danger py-2';
            reviewProgressStatus.textContent = '创建审核任务失败';
        }
        if (reviewProgressValue) reviewProgressValue.textContent = '审核失败';
        if (reviewProgressBar) reviewProgressBar.className = 'progress-bar progress-bar-striped bg-danger';
        if (reviewProgressError) {
            reviewProgressError.textContent = message || '创建审核任务失败，请稍后重试。';
            reviewProgressError.classList.remove('d-none');
        }
        if (reviewProgress.elapsedTimer) {
            window.clearInterval(reviewProgress.elapsedTimer);
            reviewProgress.elapsedTimer = null;
        }
    }

    function optimizationScope(review, modeName) {
        return `${sourceLabel(review)} · ${modeName === 'structure' ? '结构优化' : '场次剧本优化'}`;
    }

    function refreshOptimizationProgressElapsed() {
        if (!optimizationProgress.startedAt || !optimizationProgressElapsed) return;
        optimizationProgressElapsed.textContent = `已等待 ${formatReviewElapsed(Date.now() - optimizationProgress.startedAt)}`;
    }

    function showOptimizationProgress(scope = '') {
        if (!optimizationProgressModal) return;
        optimizationProgress.optimizationId = null;
        optimizationProgress.startedAt = Date.now();
        if (optimizationProgressScope) optimizationProgressScope.textContent = scope;
        if (optimizationProgressStatus) {
            optimizationProgressStatus.className = 'alert alert-info py-2';
            optimizationProgressStatus.textContent = '正在创建优化草稿任务';
        }
        if (optimizationProgressValue) optimizationProgressValue.textContent = '正在启动';
        if (optimizationProgressBar) optimizationProgressBar.className = 'progress-bar progress-bar-striped progress-bar-animated';
        if (optimizationProgressError) {
            optimizationProgressError.textContent = '';
            optimizationProgressError.classList.add('d-none');
        }
        optimizationProgressViewDrafts?.classList.add('d-none');
        refreshOptimizationProgressElapsed();
        if (optimizationProgress.elapsedTimer) window.clearInterval(optimizationProgress.elapsedTimer);
        optimizationProgress.elapsedTimer = window.setInterval(refreshOptimizationProgressElapsed, 1000);
        optimizationProgressModal.show();
    }

    function hideOptimizationProgress() {
        optimizationProgressModal?.hide();
    }

    function updateOptimizationProgress(optimization) {
        if (!optimizationProgressModal || optimizationProgress.optimizationId !== optimization.id) return;
        const isRunning = ['queued', 'running'].includes(optimization.status);
        const completed = optimization.status === 'completed';
        const failed = optimization.status === 'failed';
        if (optimizationProgressStatus) {
            optimizationProgressStatus.className = `alert ${failed ? 'alert-danger' : completed ? 'alert-success' : 'alert-info'} py-2`;
            optimizationProgressStatus.textContent = failed
                ? '优化草稿生成失败'
                : completed ? '优化草稿已生成，可进入逐集查看和对比。' : optimizationStatus(optimization.status);
        }
        if (optimizationProgressValue) optimizationProgressValue.textContent = failed ? '生成失败' : completed ? '生成完成' : '处理中';
        if (optimizationProgressBar) {
            optimizationProgressBar.className = `progress-bar progress-bar-striped${isRunning ? ' progress-bar-animated' : ''}${failed ? ' bg-danger' : completed ? ' bg-success' : ''}`;
            optimizationProgressBar.style.width = '100%';
        }
        if (failed && optimizationProgressError) {
            optimizationProgressError.textContent = optimization.error || '优化草稿任务执行失败，请稍后重试。';
            optimizationProgressError.classList.remove('d-none');
        }
        optimizationProgressViewDrafts?.classList.toggle('d-none', !completed);
        if (!isRunning && optimizationProgress.elapsedTimer) {
            window.clearInterval(optimizationProgress.elapsedTimer);
            optimizationProgress.elapsedTimer = null;
            refreshOptimizationProgressElapsed();
        }
    }

    function showOptimizationProgressError(message) {
        if (!optimizationProgressModal) return;
        if (optimizationProgressStatus) {
            optimizationProgressStatus.className = 'alert alert-danger py-2';
            optimizationProgressStatus.textContent = '创建优化草稿任务失败';
        }
        if (optimizationProgressValue) optimizationProgressValue.textContent = '生成失败';
        if (optimizationProgressBar) optimizationProgressBar.className = 'progress-bar progress-bar-striped bg-danger';
        if (optimizationProgressError) {
            optimizationProgressError.textContent = message || '创建优化草稿任务失败，请稍后重试。';
            optimizationProgressError.classList.remove('d-none');
        }
        if (optimizationProgress.elapsedTimer) {
            window.clearInterval(optimizationProgress.elapsedTimer);
            optimizationProgress.elapsedTimer = null;
        }
    }

    function sourceLabel(review) {
        return review.source_type === 'text'
            ? `剧本文件：${review.source_payload?.source_filename || '未命名文件'}（非正式）`
            : (review.source_payload?.project_name || `项目 ${review.project_id}`);
    }

    function reviewEpisodeCheckboxes() {
        return [...episodes.querySelectorAll('input[name="script-review-episode"]')];
    }

    function syncSelectAllReviewEpisodes() {
        const button = episodes.querySelector('#script-review-select-all-episodes');
        const checkboxes = reviewEpisodeCheckboxes();
        if (!button || !checkboxes.length) return;
        const allSelected = checkboxes.every((input) => input.checked);
        button.setAttribute('aria-pressed', String(allSelected));
        button.innerHTML = allSelected
            ? '<i class="bi bi-x-square me-1" aria-hidden="true"></i>取消全选'
            : '<i class="bi bi-check2-square me-1" aria-hidden="true"></i>全选';
    }

    function toggleAllReviewEpisodes() {
        const checkboxes = reviewEpisodeCheckboxes();
        const selectAll = checkboxes.length > 0 && !checkboxes.every((input) => input.checked);
        checkboxes.forEach((input) => { input.checked = selectAll; });
        syncSelectAllReviewEpisodes();
    }

    function renderEpisodes() {
        episodes.replaceChildren();
        if (source.value !== 'project' || !selectedProject || mode.value === 'collection') return;
        const latest = new Map();
        (selectedProject.episodes || []).filter((item) => item.stage === 'script').forEach((item) => {
            const existing = latest.get(item.episode_number);
            if (!existing || Number(item.version) > Number(existing.version)) latest.set(item.episode_number, item);
        });
        const episodeNumbers = [...latest.keys()].sort((a, b) => a - b);
        if (mode.value === 'single') {
            const select = document.createElement('select');
            select.className = 'form-select form-select-sm';
            select.setAttribute('aria-label', '选择审核集数');
            const placeholder = document.createElement('option');
            placeholder.value = '';
            placeholder.textContent = '选择集数';
            select.append(placeholder);
            episodeNumbers.forEach((episodeNumber) => {
                const option = document.createElement('option');
                option.value = String(episodeNumber);
                option.textContent = `第 ${episodeNumber} 集`;
                select.append(option);
            });
            episodes.append(select);
            return;
        }
        const selectAll = document.createElement('button');
        selectAll.id = 'script-review-select-all-episodes';
        selectAll.type = 'button';
        selectAll.className = 'btn btn-outline-light btn-sm';
        selectAll.title = '全选可审核集数';
        selectAll.setAttribute('aria-label', '全选可审核集数');
        selectAll.addEventListener('click', toggleAllReviewEpisodes);
        episodes.append(selectAll);
        episodeNumbers.forEach((episodeNumber) => {
            const wrap = document.createElement('label');
            wrap.className = 'form-check form-check-inline m-0';
            const input = document.createElement('input');
            input.className = 'form-check-input';
            input.type = 'checkbox';
            input.name = 'script-review-episode';
            input.value = String(episodeNumber);
            input.addEventListener('change', syncSelectAllReviewEpisodes);
            const caption = document.createElement('span');
            caption.className = 'form-check-label small';
            caption.textContent = `第 ${episodeNumber} 集`;
            wrap.append(input, caption);
            episodes.append(wrap);
        });
        syncSelectAllReviewEpisodes();
    }

    async function loadSelectedProject() {
        const id = Number(project.value || 0);
        selectedProject = null;
        if (reviewPromptRequirements) reviewPromptRequirements.value = '';
        if (!id) {
            resetPromptSelector(reviewPromptTemplate, reviewPromptDescription);
            return renderEpisodes();
        }
        try {
            selectedProject = await readJson(await aiworkerApi.request(`/api/creative/projects/${id}`));
            await loadPromptSelector(
                id, 'review', reviewPromptTemplate, reviewPromptDescription,
            );
        } catch (requestError) {
            showError(requestError.message);
        }
        renderEpisodes();
    }

    async function loadProjects() {
        const payload = await readJson(await aiworkerApi.request('/api/creative/projects'));
        reviewProjects = (payload.items || []).filter((item) => item.outline_batch_id);
        project.replaceChildren();
        if (!reviewProjects.length) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = '暂无已锁定分集大纲的项目';
            project.append(option);
            selectedProject = null;
            resetPromptSelector(reviewPromptTemplate, reviewPromptDescription);
            renderEpisodes();
            return;
        }
        reviewProjects.forEach((item) => {
            const option = document.createElement('option');
            option.value = String(item.id);
            option.textContent = item.name;
            project.append(option);
        });
        await loadSelectedProject();
    }

    function toggleSource() {
        const uploading = source.value === 'text';
        projectWrap.classList.toggle('d-none', uploading);
        fileWrap.classList.toggle('d-none', !uploading);
        reviewPromptWrap?.classList.toggle('d-none', uploading);
        episodes.classList.toggle('d-none', uploading || mode.value === 'collection');
        mode.disabled = uploading;
        if (uploading) mode.value = 'single';
        renderEpisodes();
    }

    function selectedEpisodes() {
        const selectedEpisode = episodes.querySelector('select');
        if (selectedEpisode) return selectedEpisode.value ? [Number(selectedEpisode.value)] : [];
        return [...episodes.querySelectorAll('input:checked')].map((input) => Number(input.value));
    }

    function resetPromptSelector(select, description, message = '选择项目后读取模板库') {
        if (select) {
            select.replaceChildren(new Option(message, ''));
            select.disabled = true;
        }
        if (description) description.textContent = message;
    }

    function templateDimensionText(template) {
        const dimensions = template?.content?.dimensions || {};
        return [...new Set(Object.values(dimensions).map((value) => String(value || '').trim()).filter(Boolean))].join(' · ');
    }

    async function loadPromptSelector(projectId, stage, select, description) {
        if (!select || !projectId) return resetPromptSelector(select, description);
        resetPromptSelector(select, description, '正在读取项目模板…');
        const response = await aiworkerApi.request(`/api/creative/projects/${projectId}/prompt-modules/${stage}?episode_number=0`);
        const data = await readJson(response);
        if (!data.settings?.enabled) {
            resetPromptSelector(select, description, '项目尚未启用模板库，将沿用经典提示词');
            return data;
        }
        const templates = (data.templates || []).filter((item) => !Array.isArray(item.supported_stages) || item.supported_stages.includes(stage));
        select.replaceChildren();
        const effectiveTitle = data.effective?.title || '项目默认模板';
        select.append(new Option(`使用项目默认（${effectiveTitle}）`, ''));
        templates.forEach((template) => {
            const option = new Option(`${template.title} · ${templateDimensionText(template)}`, template.template_key);
            option.dataset.revision = String(template.revision || 1);
            option.dataset.description = template.description || '';
            select.append(option);
        });
        select.disabled = false;
        const updateDescription = () => {
            const selected = select.selectedOptions[0];
            description.textContent = selected?.dataset.description
                || `当前默认：${effectiveTitle}。模板不得覆盖系统输出契约和流程阻断。`;
        };
        select.onchange = updateDescription;
        updateDescription();
        return data;
    }

    function promptSelectionPayload(select, requirements) {
        if (!select || select.disabled) return {};
        const selected = select.selectedOptions[0];
        const customRequirements = String(requirements?.value || '').trim();
        return {
            ...(select.value ? {
                prompt_template_key: select.value,
                prompt_template_revision: Number(selected?.dataset.revision || 1),
            } : {}),
            ...(customRequirements ? { prompt_override: { custom_requirements: customRequirements } } : {}),
        };
    }

    async function createReview() {
        showError();
        const file = source.value === 'text' ? scriptFile.files?.[0] : null;
        if (source.value === 'text' && !file) {
            showError('请选择剧本文件');
            return;
        }
        submit.disabled = true;
        const payload = source.value === 'text'
            ? { source_type: 'text', mode: 'single' }
            : {
                source_type: 'project', mode: mode.value, project_id: Number(project.value),
                episode_numbers: selectedEpisodes(),
                ...promptSelectionPayload(reviewPromptTemplate, reviewPromptRequirements),
            };
        showReviewProgress(reviewScope(payload));
        try {
            const response = source.value === 'text'
                ? await aiworkerApi.request('/api/script-reviews/file', {
                    method: 'POST',
                    headers: {'X-Script-Filename': encodeURIComponent(file.name), 'Content-Type': file.type || 'text/plain'},
                    body: file,
                })
                : await aiworkerApi.request('/api/script-reviews', {
                    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
                });
            const created = await readJson(response);
            activeReviewId = created.review.id;
            reviewProgress.reviewId = created.review.id;
            updateReviewProgress(created.review);
            renderReview(created.review);
            await loadHistory();
            void pollReview(activeReviewId);
        } catch (requestError) {
            showError(requestError.message);
            showReviewProgressError(requestError.message);
        } finally {
            submit.disabled = false;
        }
    }

    function findingTitle(item) {
        if (item.title && item.title !== '未命名问题') return item.title;
        const prefix = String(item.rule_id || '').split('-', 1)[0].toLowerCase();
        return {
            logic: '剧情逻辑待补强', character: '人物动机待补强',
            detail: '细节表达不明确', plot: '冲突与爽点待加强',
            dialogue: '台词与 OS 待优化', pacing: '剧情节奏待调整',
            continuity: '跨集连续性待补强',
        }[prefix] || '审核问题待补充';
    }

    function outlineFieldLabel(field) {
        return {
            core_event: '核心事件',
            conflict: '冲突',
            character_progress: '人物推进',
            highlight: '爽点',
            reversal: '反转',
            cliffhanger: '结尾悬念',
            estimated_seconds: '预计时长',
        }[field] || '大纲要点';
    }

    function outlineAnchorText(anchor) {
        if (!anchor || typeof anchor !== 'object') return '';
        const valueText = (value) => Array.isArray(value) ? value.join('、') : String(value ?? '').trim();
        if (anchor.field) {
            const value = valueText(anchor.value);
            return value ? `${outlineFieldLabel(String(anchor.field))}：${value}` : outlineFieldLabel(String(anchor.field));
        }
        return Object.entries(anchor)
            .filter(([, value]) => valueText(value))
            .map(([key, value]) => `${outlineFieldLabel(key)}：${valueText(value)}`)
            .join('；');
    }

    function finding(item) {
        const node = document.createElement('li');
        node.className = 'mb-2';
        const evidence = item.evidence ? ` 证据：${item.evidence}` : '';
        const anchorText = outlineAnchorText(item.outline_anchor);
        const outline = anchorText ? ` 对应大纲：${anchorText}` : '';
        const beats = (item.beat_orders || []).length ? ` 关联节拍：第 ${item.beat_orders.join('、第 ')} 节` : '';
        const route = { script_only: '场次细节优化', beats_then_script: '先优化 Beats', outline_revision_required: '需人工修订大纲' }[item.change_route] || '场次细节优化';
        node.textContent = `[${item.severity || 'P2'}] ${findingTitle(item)}${outline}${beats}${evidence}${item.suggestion ? ` 建议：${item.suggestion}` : ''} · 处理方式：${route}`;
        return node;
    }

    function renderReviewContext(review) {
        const sourcePayload = review.source_payload || {};
        if (!sourcePayload.formal) {
            const notice = document.createElement('div');
            notice.className = 'alert alert-info py-2 mb-3';
            notice.textContent = '这是非正式文本诊断，不可人工确认或生成优化草稿。';
            result.append(notice);
            return;
        }
        result.append(sectionHeading('评审依据'));
        const summary = document.createElement('div');
        summary.className = 'small text-muted mb-2';
        summary.textContent = `Brief v${sourcePayload.brief?.version || '-'} · 人物关系 ${sourcePayload.relationships?.length || 0} 条`;
        result.append(summary);
        const list = document.createElement('ul');
        list.className = 'ps-3 small mb-3';
        (sourcePayload.episodes || []).forEach((episode) => {
            const item = document.createElement('li');
            item.textContent = `第 ${episode.episode_number} 集：大纲 v${episode.outline?.version || '-'} · Beats v${episode.beats?.version || '-'} · 场次剧本 v${episode.script?.version || episode.version || '-'}`;
            list.append(item);
        });
        result.append(list);
    }

    function appendFindingSection(parent, title, items) {
        if (!items?.length) return;
        const heading = document.createElement('h6');
        heading.className = 'mt-4 mb-2';
        heading.textContent = title;
        const list = document.createElement('ul');
        list.className = 'ps-3';
        items.forEach((item) => list.append(finding(item)));
        parent.append(heading, list);
    }

    function sectionHeading(text) {
        const heading = document.createElement('h6');
        heading.className = 'mt-4 mb-2';
        heading.textContent = text;
        return heading;
    }

    function findingKey(item) {
        return [item.rule_id, item.evidence, item.suggestion]
            .map((value) => String(value || '').trim())
            .join('|');
    }

    function optimizationFindings(report) {
        const items = [];
        (report.core_findings || []).forEach((item, index) => items.push({ ref: `core:${index}`, item }));
        (report.episode_reviews || []).forEach((episodeReview, episodeIndex) => {
            (episodeReview.findings || []).forEach((item, findingIndex) => {
                items.push({ ref: `episode:${episodeIndex}:${findingIndex}`, item });
            });
        });
        (report.cross_episode_findings || []).forEach((item, index) => items.push({ ref: `cross:${index}`, item, cross: true }));
        return items;
    }

    function renderOptimizationSection(review) {
        if (review.status !== 'confirmed' || review.final_verdict !== '建议修改' || !review.project_id || !review.source_payload?.formal) return;
        const section = document.createElement('section');
        section.className = 'border-top pt-4 mt-4';
        section.append(sectionHeading('AI 优化草稿'));
        const modeSelect = document.createElement('select');
        modeSelect.className = 'form-select form-select-sm mb-3';
        modeSelect.innerHTML = '<option value="direct">直接优化场次剧本</option><option value="structure">结构优化（Beats + 场次剧本）</option>';
        section.append(modeSelect);
        const promptPanel = document.createElement('div');
        promptPanel.className = 'border rounded p-3 mb-3';
        const promptSelect = document.createElement('select');
        promptSelect.className = 'form-select form-select-sm mb-2';
        promptSelect.setAttribute('aria-label', '优化模板');
        const promptRequirements = document.createElement('textarea');
        promptRequirements.className = 'form-control form-control-sm';
        promptRequirements.rows = 2;
        promptRequirements.maxLength = 4000;
        promptRequirements.placeholder = '本次优化补充要求，例如：减少解释性台词，保留冷幽默。';
        const promptDescription = document.createElement('div');
        promptDescription.className = 'form-text';
        promptPanel.append(promptSelect, promptRequirements, promptDescription);
        section.append(promptPanel);
        loadPromptSelector(review.project_id, 'optimization', promptSelect, promptDescription)
            .catch((requestError) => resetPromptSelector(promptSelect, promptDescription, requestError.message));
        const findingRows = optimizationFindings(review.report || {});
        const selectedFindings = document.createElement('div');
        findingRows.forEach(({ ref, item, cross }) => {
            const row = document.createElement('div');
            row.className = 'd-flex flex-wrap gap-2 align-items-center mb-2';
            const label = document.createElement('label');
            label.className = 'form-check mb-0';
            const input = document.createElement('input');
            input.className = 'form-check-input';
            input.type = 'checkbox';
            input.value = ref;
            input.dataset.route = item.change_route || 'script_only';
            const text = document.createElement('span');
            text.className = 'form-check-label small';
            text.textContent = findingTitle(item);
            label.append(input, text);
            row.append(label);
            if (cross) {
                const targets = document.createElement('select');
                targets.className = 'form-select form-select-sm';
                targets.multiple = true;
                targets.size = Math.min(4, (review.episode_numbers || []).length || 1);
                targets.dataset.targetsFor = ref;
                (review.episode_numbers || []).forEach((number) => {
                    const option = document.createElement('option');
                    option.value = String(number);
                    option.selected = true;
                    option.textContent = `第 ${number} 集`;
                    targets.append(option);
                });
                row.append(targets);
            }
            selectedFindings.append(row);
        });
        const availability = document.createElement('div');
        availability.className = 'small text-muted mb-2';
        section.append(selectedFindings, availability);
        let start;
        const syncFindingAvailability = () => {
            let selectableCount = 0;
            [...selectedFindings.querySelectorAll('input')].forEach((input) => {
                const route = input.dataset.route;
                const allowed = (modeSelect.value === 'direct' && route === 'script_only')
                    || (modeSelect.value === 'structure' && route === 'beats_then_script');
                input.disabled = !allowed;
                if (!allowed) input.checked = false;
                if (allowed) selectableCount += 1;
            });
            if (selectableCount === 0) {
                availability.className = 'small text-warning mb-2';
                availability.textContent = modeSelect.value === 'structure'
                    ? '当前没有需要调整 Beats 的问题。本次审核均可直接优化场次剧本，请切换为“直接优化场次剧本”。'
                    : '当前没有可直接优化的场次问题；请选择结构优化，或先修订并锁定分集大纲。';
            } else {
                availability.className = 'small text-muted mb-2';
                availability.textContent = `当前模式可选择 ${selectableCount} 条问题。`;
            }
            if (start) start.disabled = selectableCount === 0;
        };
        modeSelect.addEventListener('change', syncFindingAvailability);
        syncFindingAvailability();
        start = document.createElement('button');
        start.type = 'button';
        start.className = 'btn btn-primary btn-sm mt-2';
        start.textContent = '生成优化草稿';
        start.addEventListener('click', async () => {
            const findings = [...selectedFindings.querySelectorAll('input:checked')].map((input) => {
                const targets = selectedFindings.querySelector(`select[data-targets-for="${input.value}"]`);
                return {
                    ref: input.value,
                    episode_numbers: targets ? [...targets.selectedOptions].map((option) => Number(option.value)) : [],
                };
            });
            start.disabled = true;
            showOptimizationProgress(optimizationScope(review, modeSelect.value));
            try {
                const created = await readJson(await aiworkerApi.request(`/api/script-reviews/${review.id}/optimizations`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        mode: modeSelect.value,
                        findings,
                        ...promptSelectionPayload(promptSelect, promptRequirements),
                    }),
                }));
                optimizationProgress.optimizationId = created.optimization.id;
                updateOptimizationProgress(created.optimization);
                await loadOptimizationHistory(review, history);
            } catch (requestError) {
                showError(requestError.message);
                showOptimizationProgressError(requestError.message);
            } finally {
                start.disabled = false;
            }
        });
        section.append(start);
        syncFindingAvailability();
        const history = document.createElement('div');
        history.className = 'mt-3';
        section.append(history);
        result.append(section);
        loadOptimizationHistory(review, history);
    }

    function optimizationStatus(value) {
        return { queued: '等待生成', running: '生成中', completed: '已生成', failed: '生成失败', ready: '待采纳', accepted: '已采纳', rejected: '已放弃' }[value] || value;
    }

    function scheduleOptimizationHistory(review, container, items) {
        const timer = optimizationTimers.get(review.id);
        const stillRunning = items.some((optimization) => ['queued', 'running'].includes(optimization.status));
        if (!stillRunning) {
            if (timer) window.clearTimeout(timer);
            optimizationTimers.delete(review.id);
            return;
        }
        if (timer) return;
        const next = window.setTimeout(() => {
            optimizationTimers.delete(review.id);
            if (activeReviewId === review.id) loadOptimizationHistory(review, container);
        }, 2000);
        optimizationTimers.set(review.id, next);
    }

    async function loadOptimizationHistory(review, container) {
        try {
            const payload = await readJson(await aiworkerApi.request(`/api/script-reviews/${review.id}/optimizations`));
            container.replaceChildren();
            const items = payload.items || [];
            items.forEach((optimization) => {
                updateOptimizationProgress(optimization);
                const title = document.createElement('div');
                title.className = 'small fw-semibold mt-3';
                title.textContent = `${optimization.mode === 'structure' ? '结构优化' : '场次剧本优化'} · ${optimizationStatus(optimization.status)}`;
                container.append(title);
                (optimization.drafts || []).forEach((draft) => {
                    const row = document.createElement('div');
                    row.className = 'small d-flex flex-wrap align-items-center gap-2 mt-2';
                    const text = document.createElement('span');
                    text.textContent = `第 ${draft.episode_number} 集 · ${draft.kind === 'beats' ? 'Beats' : '场次剧本'} · ${optimizationStatus(draft.status)}`;
                    row.append(text);
                    const stage = draft.kind === 'beats' ? 'beats' : 'scripts';
                    const compare = document.createElement('a');
                    compare.className = 'btn btn-outline-light btn-sm';
                    compare.href = `/writing/projects/${optimization.project_id}/episodes/${draft.episode_number}/${stage}?optimization_draft=${draft.id}&review_id=${review.id}`;
                    compare.textContent = draft.kind === 'script' && draft.status === 'ready' ? '编辑草稿' : '查看对比';
                    row.append(compare);
                    if (draft.status === 'ready') {
                        const reject = document.createElement('button');
                        reject.type = 'button';
                        reject.className = 'btn btn-outline-secondary btn-sm';
                        reject.textContent = '放弃';
                        reject.addEventListener('click', () => rejectOptimizationDraft(draft.id, review, container));
                        if (draft.kind === 'beats') {
                            const accept = document.createElement('button');
                            accept.type = 'button';
                            accept.className = 'btn btn-success btn-sm';
                            accept.textContent = '采纳';
                            accept.addEventListener('click', () => acceptOptimizationDraft(draft.id, review, container));
                            row.append(accept);
                        }
                        row.append(reject);
                    }
                    container.append(row);
                });
            });
            scheduleOptimizationHistory(review, container, items);
        } catch (requestError) {
            showError(requestError.message);
        }
    }

    async function acceptOptimizationDraft(draftId, review, container) {
        try {
            await readJson(await aiworkerApi.request(`/api/script-optimization-drafts/${draftId}/accept`, { method: 'POST' }));
            await loadOptimizationHistory(review, container);
        } catch (requestError) {
            showError(requestError.message);
        }
    }

    async function rejectOptimizationDraft(draftId, review, container) {
        try {
            await readJson(await aiworkerApi.request(`/api/script-optimization-drafts/${draftId}/reject`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
            }));
            await loadOptimizationHistory(review, container);
        } catch (requestError) {
            showError(requestError.message);
        }
    }

    async function downloadReviewReport(reviewId) {
        try {
            const response = await aiworkerApi.request(`/api/script-reviews/${reviewId}/export`);
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                throw new Error(payload.detail || '下载审核报告失败');
            }
            const link = document.createElement('a');
            link.href = URL.createObjectURL(await response.blob());
            link.download = `script-review-${reviewId}.md`;
            link.click();
            URL.revokeObjectURL(link.href);
        } catch (requestError) {
            showError(requestError.message);
        }
    }

    function renderReview(review) {
        activeReviewId = review.id;
        result.replaceChildren();
        result.classList.remove('d-none');
        const heading = document.createElement('div');
        heading.className = 'd-flex justify-content-between align-items-center mb-3';
        const title = document.createElement('h5');
        title.className = 'mb-0';
        title.textContent = `审核报告 #${review.id}`;
        const status = document.createElement('span');
        status.className = 'badge text-bg-secondary';
        status.textContent = label(review.status);
        const actions = document.createElement('div');
        actions.className = 'd-flex gap-2 align-items-center';
        const download = document.createElement('button');
        download.type = 'button';
        download.className = 'btn btn-outline-light btn-sm';
        download.title = '下载审核报告';
        download.setAttribute('aria-label', '下载审核报告');
        download.innerHTML = '<i class="bi bi-download" aria-hidden="true"></i>';
        download.addEventListener('click', () => downloadReviewReport(review.id));
        actions.append(download, status);
        heading.append(title, actions);
        result.append(heading);
        const coverage = document.createElement('div');
        coverage.className = 'small text-muted mb-3';
        const episodeNumbers = review.episode_numbers || [];
        coverage.textContent = `${{ single: '单集', multiple: '多集', collection: '合集' }[review.mode] || '审核'} · 实际覆盖 ${episodeNumbers.length} 集（${episodeNumbers.map((number) => `第 ${number} 集`).join('、') || '-'}）`;
        result.append(coverage);
        renderReviewContext(review);
        if (!review.report) return;
        const partial = review.report.partial === true;
        if (!partial && !review.report.ai_verdict) return;
        if (partial) {
            const notice = document.createElement('div');
            notice.className = 'alert alert-warning py-2 mb-3';
            const completed = Number(review.report.completed_episode_count || 0);
            const total = Number(review.report.total_episode_count || episodeNumbers.length || 0);
            notice.textContent = `阶段性报告：已完成 ${completed}/${total} 集逐集审核，跨集汇总尚未完成。请继续或重新发起审核后获取最终结论。`;
            result.append(notice);
        } else {
            result.append(sectionHeading('修改建议'));
            const overview = document.createElement('p');
            overview.className = 'mb-3';
            overview.textContent = `AI 建议：${review.ai_verdict}。${review.report.summary || ''}`;
            result.append(overview);
        }
        const coreFindings = review.report.core_findings || [];
        const coreFindingKeys = new Set(coreFindings.map(findingKey));
        const findings = document.createElement('ul');
        findings.className = 'ps-3';
        coreFindings.forEach((item) => findings.append(finding(item)));
        if (findings.children.length) result.append(findings);
        const episodeReviews = review.report.episode_reviews || [];
        const episodeSummaries = episodeReviews.filter((item) => item.summary);
        if (episodeSummaries.length) {
            result.append(sectionHeading('剧情概要'));
            episodeSummaries.forEach((episodeReview) => {
                const summary = document.createElement('p');
                summary.className = 'mb-2';
                summary.textContent = `第 ${episodeReview.episode_number || '-'} 集：${episodeReview.summary}`;
                result.append(summary);
            });
        }
        const episodeFindings = [];
        episodeReviews.forEach((episodeReview) => {
            const uniqueFindings = (episodeReview.findings || [])
                .filter((item) => !coreFindingKeys.has(findingKey(item)));
            if (uniqueFindings.length) episodeFindings.push({ episodeReview, items: uniqueFindings });
        });
        if (episodeFindings.length) {
            result.append(sectionHeading('逐集补充问题'));
            episodeFindings.forEach(({ episodeReview, items }) => {
                const heading = document.createElement('div');
                heading.className = 'small fw-semibold mb-1';
                heading.textContent = `第 ${episodeReview.episode_number || '-'} 集`;
                result.append(heading);
                const list = document.createElement('ul');
                list.className = 'ps-3';
                items.forEach((item) => list.append(finding(item)));
                result.append(list);
            });
        }
        appendFindingSection(result, '跨集问题', review.report.cross_episode_findings || []);
        if (review.status === 'pending_confirmation' && review.source_payload?.formal) {
            const controls = document.createElement('div');
            controls.className = 'd-flex flex-wrap gap-2 align-items-center mt-3';
            const note = document.createElement('input');
            note.className = 'form-control form-control-sm';
            note.style.maxWidth = '320px';
            note.placeholder = '确认备注（可选）';
            ['通过', '建议修改'].forEach((verdict) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = verdict === '通过' ? 'btn btn-success btn-sm' : 'btn btn-warning btn-sm';
                button.textContent = `确认${verdict}`;
                button.addEventListener('click', () => confirmReview(review.id, verdict, note.value));
                controls.append(button);
            });
            controls.append(note);
            result.append(controls);
        } else if (review.final_verdict) {
            const final = document.createElement('div');
            final.className = 'small text-muted mt-3';
            const confirmedAt = review.confirmed_at ? ` · ${new Date(review.confirmed_at * 1000).toLocaleString()}` : '';
            final.textContent = `人工结论：${review.final_verdict}${review.confirmed_by ? ` · ${review.confirmed_by}` : ''}${confirmedAt}${review.confirmation_note ? ` · ${review.confirmation_note}` : ''}`;
            result.append(final);
        } else if (review.status === 'stale') {
            const stale = document.createElement('div');
            stale.className = 'alert alert-warning mt-3 mb-0';
            stale.textContent = '剧本已更新，此报告已过期，需重新审核后才能确认。';
            result.append(stale);
        }
        renderOptimizationSection(review);
    }

    async function confirmReview(reviewId, finalVerdict, confirmationNote) {
        try {
            const review = await readJson(await aiworkerApi.request(`/api/script-reviews/${reviewId}/confirm`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ final_verdict: finalVerdict, confirmation_note: confirmationNote }),
            }));
            renderReview(review);
            await loadHistory();
        } catch (requestError) {
            showError(requestError.message);
        }
    }

    async function openReview(reviewId) {
        try {
            const review = await readJson(await aiworkerApi.request(`/api/script-reviews/${reviewId}`));
            renderReview(review);
            if (['queued', 'running'].includes(review.status)) {
                showReviewProgress(`${sourceLabel(review)} · ${label(review.status)}`);
                reviewProgress.reviewId = review.id;
                updateReviewProgress(review);
                void pollReview(review.id);
            }
        } catch (requestError) {
            showError(requestError.message);
        }
    }

    async function deleteReview(reviewId) {
        if (!window.confirm('确定删除这条审核记录及其未采纳的优化草稿吗？此操作不可恢复。')) return;
        try {
            await readJson(await aiworkerApi.request(`/api/script-reviews/${reviewId}`, { method: 'DELETE' }));
            if (activeReviewId === reviewId) {
                activeReviewId = null;
                result.replaceChildren();
                result.classList.add('d-none');
            }
            await loadHistory();
        } catch (requestError) {
            showError(requestError.message);
        }
    }

    async function pollReview(reviewId) {
        if (reviewPollTimers.has(reviewId)) return;
        try {
            const review = await readJson(await aiworkerApi.request(`/api/script-reviews/${reviewId}`));
            if (review.id === activeReviewId) renderReview(review);
            updateReviewProgress(review);
            if (['queued', 'running'].includes(review.status)) {
                const timer = window.setTimeout(() => {
                    reviewPollTimers.delete(reviewId);
                    void pollReview(reviewId);
                }, 2000);
                reviewPollTimers.set(reviewId, timer);
            } else {
                await loadHistory();
            }
        } catch (requestError) {
            showError(requestError.message);
            if (reviewProgress.reviewId === reviewId) showReviewProgressError(requestError.message);
        }
    }

    async function loadHistory() {
        try {
            const payload = await readJson(await aiworkerApi.request('/api/script-reviews'));
            history.replaceChildren();
            (payload.items || []).forEach((review) => {
                const row = document.createElement('tr');
                [sourceLabel(review), review.episode_numbers?.join('、') || '-', review.ai_verdict || '-', review.final_verdict || '-', label(review.status), review.created_at ? new Date(review.created_at * 1000).toLocaleString() : '-'].forEach((value) => {
                    const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
                });
                const action = document.createElement('td'); action.className = 'text-end';
                const button = document.createElement('button'); button.type = 'button'; button.className = 'btn btn-outline-light btn-sm'; button.title = '查看审核报告'; button.innerHTML = '<i class="bi bi-eye" aria-hidden="true"></i>';
                button.addEventListener('click', () => openReview(review.id)); action.append(button); row.append(action); history.append(row);
                if (!['queued', 'running'].includes(review.status)) {
                    const remove = document.createElement('button');
                    remove.type = 'button';
                    remove.className = 'btn btn-outline-danger btn-sm ms-2';
                    remove.title = '删除审核记录';
                    remove.setAttribute('aria-label', '删除审核记录');
                    remove.innerHTML = '<i class="bi bi-trash" aria-hidden="true"></i>';
                    remove.addEventListener('click', () => deleteReview(review.id));
                    action.append(remove);
                }
            });
            if (!history.children.length) history.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-4">暂无审核记录</td></tr>';
        } catch (requestError) {
            showError(requestError.message);
        }
    }

    source.addEventListener('change', toggleSource);
    mode.addEventListener('change', renderEpisodes);
    project.addEventListener('change', loadSelectedProject);
    submit.addEventListener('click', createReview);
    reviewProgressViewReport?.addEventListener('click', hideReviewProgress);
    optimizationProgressViewDrafts?.addEventListener('click', hideOptimizationProgress);
    document.getElementById('script-review-refresh')?.addEventListener('click', async () => { await loadProjects(); await loadHistory(); });
    (async () => {
        try {
            await loadProjects();
            await loadHistory();
            toggleSource();
            if (returnReviewId > 0) await openReview(returnReviewId);
        } catch (requestError) {
            showError(requestError.message);
        }
    })();
}());
