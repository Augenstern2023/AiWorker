(function () {
    'use strict';

    const pollers = new Map();
    const createRequestIds = new Map();
    const inertSiblings = [];
    let currentSession = null;
    let currentContext = {};
    let returnFocus = null;

    function drawer() { return document.getElementById('writer-project-review-drawer'); }
    function content() { return document.getElementById('writer-project-review-content'); }
    function requestId() { return window.crypto?.randomUUID?.() || `project-review-${Date.now()}-${Math.random().toString(16).slice(2)}`; }
    function key(sessionId, reviewId) { return `${sessionId}:${reviewId}`; }
    function number(value) { const parsed = Number(value || 0); return Number.isFinite(parsed) ? Math.floor(parsed) : 0; }
    function addText(parent, className, value, tagName = 'div') { const node = document.createElement(tagName); node.className = className; node.textContent = String(value || ''); parent.append(node); return node; }
    function safeWriterLocalUrl(value) {
        const raw = String(value || '');
        try {
            const origin = window.location.origin === 'null' ? 'http://writer-agent.local' : window.location.origin;
            const parsed = new URL(raw, origin);
            if (raw.startsWith('/') && !raw.startsWith('//') && !raw.includes('\\') && parsed.origin === origin) return `${parsed.pathname}${parsed.search}${parsed.hash}`;
        } catch (_error) { return ''; }
        return '';
    }
    async function json(result) {
        if (result && typeof result.json === 'function') {
            const payload = await result.json().catch(() => ({}));
            if (result.ok === false) throw new Error(payload.detail?.message || payload.detail || '请求失败');
            return payload;
        }
        return result || {};
    }
    function api(sessionId, suffix = '') { return `/api/writer-agent/sessions/${sessionId}/project-reviews${suffix}`; }
    function openDrawer(trigger) {
        const element = drawer();
        if (!element) return;
        returnFocus = trigger || document.activeElement;
        if (!inertSiblings.length) Array.from(element.parentElement?.children || []).forEach((node) => {
            if (node !== element && node instanceof HTMLElement) { node.inert = true; inertSiblings.push(node); }
        });
        element.hidden = false;
        element.setAttribute('aria-hidden', 'false');
        element.querySelector('#writer-project-review-close')?.focus();
    }
    function closeDrawer() {
        const element = drawer();
        if (!element) return;
        element.hidden = true;
        element.setAttribute('aria-hidden', 'true');
        inertSiblings.splice(0).forEach((node) => { node.inert = false; });
        if (returnFocus instanceof HTMLElement && document.contains(returnFocus)) returnFocus.focus();
    }
    function manifestFor(review) { return review?.source_payload?.manifest || review?.report?.source_manifest || {}; }
    function episodeNumbers(manifest) {
        const stages = manifest?.stages || {};
        return Object.values(stages).flatMap((stage) => Array.isArray(stage?.episode_numbers) ? stage.episode_numbers.map(number).filter(Boolean) : []);
    }
    function rangeLabel(numbers) {
        if (!numbers.length) return '无已加载分集';
        const unique = [...new Set(numbers)].sort((left, right) => left - right);
        return unique.length === 1 ? `实际加载第 ${unique[0]} 集` : `实际加载第 ${unique[0]}–${unique.at(-1)} 集`;
    }
    function resourceDetails(parent, resource, label) {
        if (!resource) return;
        const details = document.createElement('details'); details.className = 'writer-project-review-resource';
        const summary = document.createElement('summary'); summary.textContent = label; details.append(summary);
        addText(details, 'small text-muted mt-2', `${number(resource.content_chars).toLocaleString()} 字符 · ${resource.status || '当前版本'}`);
        if (resource.summary) addText(details, 'small mt-2', resource.summary);
        const url = safeWriterLocalUrl(resource.source_url);
        if (url) { const link = document.createElement('a'); link.className = 'small d-inline-block mt-2'; link.href = url; link.textContent = '打开源内容'; details.append(link); }
        parent.append(details);
    }
    function renderPreview(preview) {
        const host = content(); if (!host) return;
        host.replaceChildren();
        const manifest = preview.manifest || {};
        const project = manifest.project || {};
        addText(host, 'writer-project-review-kicker', '本次加载内容');
        addText(host, 'writer-project-review-project', `项目 #${number(manifest.project_id || project.id)}`);
        if (project.name) addText(host, 'small text-muted mt-1', `${project.name} · 项目版本 ${number(project.revision)}`);
        const freeze = document.createElement('section'); freeze.className = 'writer-project-review-section';
        addText(freeze, 'fw-semibold', '冻结快照');
        resourceDetails(freeze, manifest.brief, `Brief v${number(manifest.brief?.version) || 1}`);
        const characters = manifest.characters || {};
        addText(freeze, 'small mt-2', `人物批次 ${characters.batch_id || '未加载'} · ${number(characters.character_count)} 人物 / ${number(characters.relationship_count)} 关系`);
        const stages = manifest.stages || {};
        Object.entries(stages).forEach(([name, stage]) => {
            addText(freeze, 'small mt-2', `${name === 'outline' ? '全剧大纲' : '剧本'} · ${number(stage.unit_count)} 项 · ${number(stage.content_chars).toLocaleString()} 字符`);
            (stage.units || []).slice(0, 3).forEach((unit) => resourceDetails(freeze, unit.resource, `第 ${number(unit.episode_number)} 集 ${name === 'outline' ? '大纲' : '剧本'} v${number(unit.resource?.version)}`));
        });
        host.append(freeze);
        const notice = document.createElement('section'); notice.className = 'writer-project-review-section writer-project-review-notice';
        addText(notice, 'fw-semibold', rangeLabel(episodeNumbers(manifest)));
        addText(notice, 'small text-muted mt-1', '历史废弃版本不会加载到本次审核。');
        if ((manifest.missing_episode_numbers || []).length) addText(notice, 'small mt-2', `缺失集：${manifest.missing_episode_numbers.join('、')}`);
        if ((manifest.upstream_mismatches || []).length) addText(notice, 'small mt-2', `发现 ${manifest.upstream_mismatches.length} 项上下游引用待核对`);
        host.append(notice);
        const actions = document.createElement('div'); actions.className = 'writer-project-review-actions';
        const confirm = document.createElement('button'); confirm.type = 'button'; confirm.className = 'btn btn-primary writer-session-mutation'; confirm.textContent = '确认并开始完整审核';
        confirm.addEventListener('click', () => createReview(preview, confirm));
        actions.append(confirm); host.append(actions);
    }
    function reportMeta(review) {
        const report = review?.report || {};
        return Object.assign({}, report.cursor || {}, report, review?.progress || {}, review || {});
    }
    async function withWorkerProgress(review) {
        const jobId = String(review?.job_id || '').trim();
        if (!jobId || !['queued', 'running'].includes(String(review?.status || ''))) return review;
        try {
            const job = await json(await window.aiworkerApi.request(`/api/jobs/${encodeURIComponent(jobId)}`));
            const event = [...(job.events || [])].reverse().find((item) => {
                const payload = item?.payload || {};
                return item?.event === 'progress' && (
                    payload.current_episode || payload.batch_start || payload.request_started_at
                );
            });
            return event ? Object.assign({}, review, {progress: Object.assign({}, review.progress || {}, event.payload || {})}) : review;
        } catch (_error) { return review; }
    }
    function reviewProgress(review) {
        const manifest = manifestFor(review);
        const meta = reportMeta(review);
        const total = episodeNumbers(manifest).length || number(manifest.outline_count || manifest.script_count);
        const current = number(meta.current_episode || meta.episode_number);
        const resultCount = Array.isArray(review?.report?.all_unit_results) ? review.report.all_unit_results.length : 0;
        const completed = review.status === 'completed' ? (total || Math.max(current, resultCount)) : Math.max(resultCount, current ? current - 1 : 0);
        return {total, current, completed, batchStart: number(meta.batch_start), batchEnd: number(meta.batch_end), startedAt: Number(meta.request_started_at || 0), attempt: number(meta.request_attempt) || 1};
    }
    function renderCurrentRequest(host, review) {
        const meta = reportMeta(review);
        const refs = Array.isArray(meta.loaded_resource_refs) ? meta.loaded_resource_refs : [];
        if (!meta.request_kind && !meta.stage_kind && !refs.length) return;
        const section = document.createElement('section'); section.className = 'writer-project-review-section';
        addText(section, 'fw-semibold', '当前请求内容');
        const requestLabels = {unit_review: '逐集审核', batch_summary: '批次汇总', final_summary: '最终汇总'};
        const stageLabels = {outline: '大纲', script: '剧本'};
        const requestLabel = requestLabels[meta.request_kind] || meta.request_kind || '审核请求';
        const stageLabel = stageLabels[meta.stage_kind] || meta.stage_kind || '全项目';
        addText(section, 'small mt-2', `${requestLabel} · ${stageLabel}`);
        if (number(meta.prompt_chars) || meta.reasoning_effort) addText(section, 'small text-muted mt-1', `提示词 ${number(meta.prompt_chars).toLocaleString()} 字符 · 推理档位 ${meta.reasoning_effort || '-'}`);
        refs.forEach((ref) => {
            const parts = [];
            const refStage = stageLabels[ref.stage_kind] || ref.stage_kind || '';
            if (refStage && ref.episode_number) parts.push(`${refStage}第 ${number(ref.episode_number)} 集`);
            else if (refStage && Array.isArray(ref.episode_numbers) && ref.episode_numbers.length) parts.push(`${refStage}第 ${number(ref.episode_numbers[0])}–${number(ref.episode_numbers.at(-1))} 集`);
            else parts.push({project: '项目', brief: 'Brief', stage: '阶段'}[ref.kind] || ref.kind || '资源');
            if (ref.id !== undefined && ref.id !== null) parts.push(`资源 #${ref.id}`);
            if (ref.version !== undefined && ref.version !== null) parts.push(`v${ref.version}`);
            addText(section, 'small text-muted mt-1', parts.join(' · '));
        });
        host.append(section);
    }
    function renderReview(review) {
        const host = content(); if (!host) return;
        host.replaceChildren();
        const state = String(review?.status || 'queued');
        const progress = reviewProgress(review);
        addText(host, 'writer-project-review-kicker', '完整项目审核');
        if (state === 'completed') {
            addText(host, 'writer-project-review-state is-complete', `已完成 ${progress.completed}/${progress.total} 集`);
            renderResultControl(host, review, '查看结果');
            const download = document.createElement('a'); download.className = 'btn btn-primary mt-3'; download.href = api(currentSession?.id, `/${review.id}/export`); download.textContent = '下载审核报告'; host.append(download); return;
        }
        if (state === 'failed') {
            addText(host, 'writer-project-review-state is-error', review.error || '完整项目审核中断');
            const resumeAt = progress.current || progress.completed + 1 || 1;
            const resume = document.createElement('button'); resume.type = 'button'; resume.className = 'btn btn-primary mt-3 writer-session-mutation'; resume.textContent = `从第 ${resumeAt} 集继续`;
            resume.addEventListener('click', () => resumeReview(review, resume)); host.append(resume); return;
        }
        renderCurrentRequest(host, review);
        const label = progress.current ? `正在审核第 ${progress.current} 集${progress.batchStart && progress.batchEnd ? `（当前批次第 ${progress.batchStart}–${progress.batchEnd} 集）` : ''}` : '正在准备完整项目审核';
        addText(host, 'writer-project-review-state', label);
        if (progress.total) addText(host, 'small text-muted mt-2', `已完成 ${progress.completed}/${progress.total} 集`);
        if (progress.startedAt && Date.now() / 1000 - progress.startedAt >= 8 * 60) addText(host, 'writer-project-review-slow mt-3', '模型响应较慢，任务仍在运行');
        if (progress.current && progress.attempt > 1) addText(host, 'small mt-2', `第 ${progress.current} 集正在进行第 ${progress.attempt} 次尝试`);
        renderResultControl(host, review, '查看阶段性结果');
        const status = document.createElement('div'); status.className = 'writer-project-review-loading mt-3'; status.setAttribute('role', 'status'); status.textContent = state === 'queued' ? '任务已排队，正在等待执行' : '审核进度会自动刷新'; host.append(status);
    }
    function renderResultControl(host, review, label) {
        const report = review?.report || {};
        const summaries = Array.isArray(report.batch_summaries) ? report.batch_summaries : [];
        const results = Array.isArray(report.all_unit_results) ? report.all_unit_results : [];
        if (!summaries.length && !results.length && !report.summary) return;
        const button = document.createElement('button'); button.type = 'button'; button.className = 'btn btn-outline-light btn-sm mt-3'; button.textContent = label;
        const details = document.createElement('section'); details.className = 'writer-project-review-section'; details.hidden = true;
        if (report.summary) addText(details, 'small', report.summary);
        summaries.forEach((item) => addText(details, 'small mt-2', `${item.stage_kind || '审核'} 批次 ${number(item.batch_index)}：${item.summary || '已生成阶段性结果'}`));
        if (!summaries.length && results.length) addText(details, 'small mt-2', `已保存 ${results.length} 集逐集审核结果`);
        button.addEventListener('click', () => { details.hidden = !details.hidden; button.textContent = details.hidden ? label : '收起结果'; });
        host.append(button, details);
    }
    async function previewReview(session, scope, trigger) {
        if (!session?.id) return;
        currentSession = session;
        openDrawer(trigger); const host = content(); if (host) host.textContent = '正在读取本次冻结内容…';
        try { renderPreview(await json(await window.aiworkerApi.request(api(session.id, '/preview'), {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({audit_scope: scope || 'project'})}))); }
        catch (error) { renderError(error.message, () => previewReview(session, scope, trigger)); }
    }
    function renderError(message, retry) { const host = content(); if (!host) return; host.replaceChildren(); addText(host, 'writer-project-review-state is-error', message || '请求失败'); const button = document.createElement('button'); button.type = 'button'; button.className = 'btn btn-outline-light mt-3'; button.textContent = '重试'; button.addEventListener('click', retry); host.append(button); }
    async function createReview(preview, button) {
        if (!currentSession?.id) return;
        button.disabled = true;
        const requestKey = `${currentSession.id}:${preview.audit_scope}:${preview.fingerprint}`;
        const clientRequestId = createRequestIds.get(requestKey) || requestId();
        createRequestIds.set(requestKey, clientRequestId);
        try { const payload = await json(await window.aiworkerApi.request(api(currentSession.id), {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({audit_scope: preview.audit_scope, expected_fingerprint: preview.fingerprint, client_request_id: clientRequestId})})); recordCreatedReview(payload.review || {}); startPolling(currentSession.id, payload.review?.id, false); renderReview(payload.review || {}); }
        catch (error) { renderError(error.message, () => previewReview(currentSession, preview.audit_scope)); }
    }
    async function resumeReview(review, button) {
        if (!currentSession?.id) return;
        button.disabled = true;
        try { const payload = await json(await window.aiworkerApi.request(api(currentSession.id, `/${review.id}/resume`), {method: 'POST'})); startPolling(currentSession.id, review.id, false); renderReview(payload.review || review); }
        catch (error) { renderError(error.message, () => resumeReview(review, button)); }
    }
    async function refreshReview(sessionId, reviewId) {
        try { const review = await withWorkerProgress(await json(await window.aiworkerApi.request(api(sessionId, `/${reviewId}`)))); if (currentSession && Number(currentSession.id) === Number(sessionId) && !drawer()?.hidden) renderReview(review); if (!['queued', 'running'].includes(String(review.status))) stopPolling(sessionId, reviewId); }
        catch (error) { if (currentSession && Number(currentSession.id) === Number(sessionId)) renderError(error.message, () => refreshReview(sessionId, reviewId)); }
    }
    function startPolling(sessionId, reviewId, refreshImmediately = true) { if (!reviewId || pollers.has(key(sessionId, reviewId))) return; if (refreshImmediately) refreshReview(sessionId, reviewId); pollers.set(key(sessionId, reviewId), window.setInterval(() => refreshReview(sessionId, reviewId), 3000)); }
    function stopPolling(sessionId, reviewId) { const timer = pollers.get(key(sessionId, reviewId)); if (timer !== undefined) window.clearInterval(timer); pollers.delete(key(sessionId, reviewId)); }
    function renderMessage(article, message, context = {}) {
        if (!article || !message) return false;
        const session = context.session || currentSession;
        if (message.kind === 'project_review_offer') {
            if (session?.id) currentSession = session;
            const card = document.createElement('section'); card.className = 'writer-project-review-offer'; addText(card, 'fw-semibold', '完整项目审核'); addText(card, 'small text-muted mt-1', message.content || '请先确认当前项目的冻结内容。');
            const button = document.createElement('button'); button.type = 'button'; button.className = 'btn btn-outline-light btn-sm mt-3 writer-session-mutation'; button.textContent = '查看本次加载内容'; button.addEventListener('click', () => previewReview(session, message.audit_scope, button)); card.append(button); article.append(card); return true;
        }
        if (message.kind === 'project_review') {
            const card = document.createElement('section'); card.className = 'writer-project-review-offer'; addText(card, 'fw-semibold', '完整项目审核'); const button = document.createElement('button'); button.type = 'button'; button.className = 'btn btn-outline-light btn-sm mt-3'; button.textContent = '查看审核进度'; button.addEventListener('click', () => { openDrawer(button); refreshReview(session?.id, message.review_id); }); card.append(button); article.append(card); return true;
        }
        return false;
    }
    function mountSession(session, context = {}) { currentSession = session || null; currentContext = context; const reviews = (session?.messages || []).filter((message) => message?.kind === 'project_review' && message.review_id); if (reviews.length) openDrawer(); reviews.forEach((message) => startPolling(session.id, message.review_id)); }
    function recordCreatedReview(review) {
        if (!currentSession || !review?.id) return;
        const messages = currentSession.messages || (currentSession.messages = []);
        currentSession.messages = messages.filter((message) => message?.kind !== 'project_review_offer');
        if (!currentSession.messages.some((message) => message?.kind === 'project_review' && Number(message.review_id) === Number(review.id))) currentSession.messages.push({kind: 'project_review', role: 'assistant', review_id: review.id, audit_scope: review.audit_scope});
    }
    function disposeSession(sessionId) { [...pollers.keys()].filter((item) => item.startsWith(`${sessionId}:`)).forEach((item) => { const [savedSessionId, reviewId] = item.split(':'); stopPolling(savedSessionId, reviewId); }); [...createRequestIds.keys()].filter((item) => item.startsWith(`${sessionId}:`)).forEach((item) => createRequestIds.delete(item)); if (Number(currentSession?.id) === Number(sessionId)) { currentSession = null; currentContext = {}; closeDrawer(); } }
    document.addEventListener('keydown', (event) => {
        const element = drawer();
        if (!element || element.hidden) return;
        if (event.key === 'Escape') { closeDrawer(); return; }
        if (event.key !== 'Tab') return;
        const focusable = [...element.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')];
        if (!focusable.length) return;
        const first = focusable[0]; const last = focusable.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    document.getElementById('writer-project-review-close')?.addEventListener('click', closeDrawer);
    window.writerProjectReviewUI = {renderMessage, mountSession, disposeSession, safeWriterLocalUrl};
})();
