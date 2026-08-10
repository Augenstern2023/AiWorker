(function () {
    'use strict';

    const sessionsElement = document.getElementById('writer-agent-sessions');
    const errorElement = document.getElementById('writer-agent-error');
    const refreshButton = document.getElementById('writer-agent-refresh');
    const reportsToggleButton = document.getElementById('writer-agent-toggle-reports');
    const layoutElement = document.querySelector('.writer-agent-layout');
    const newButton = document.getElementById('writer-agent-new');
    const detailElement = document.getElementById('writer-agent-session-detail');
    const titleElement = document.getElementById('writer-agent-session-title');
    const materialNameElement = document.getElementById('writer-agent-material-name');
    const emptyElement = document.getElementById('writer-agent-empty');
    const conversation = document.getElementById('writer-agent-conversation');
    const messageInput = document.getElementById('writer-agent-message');
    const fileInput = document.getElementById('writer-agent-file');
    const attachButton = document.getElementById('writer-agent-attach');
    const fileNameElement = document.getElementById('writer-agent-file-name');
    const sendButton = document.getElementById('writer-agent-send');
    const reportsElement = document.getElementById('writer-agent-reports');
    const reportStatus = document.getElementById('writer-agent-report-status');
    let sessions = [];
    let activeSession = null;
    let isBusy = false;
    let pendingMessage = '';
    let thinkingStageIndex = 0;
    let thinkingTimer = null;
    const thinkingStages = [
        '正在阅读你提交的材料',
        '正在结合材料与对话上下文',
        '正在梳理结构与人物关系',
        '正在生成本轮答复',
    ];
    const materialReceiptPrompt = '我刚提交了创作材料。请确认已读取，并结合当前材料简要说明可以继续讨论的方向。';

    if (!sessionsElement || !errorElement || !detailElement || !titleElement || !materialNameElement || !emptyElement || !conversation || !messageInput || !fileInput || !attachButton || !fileNameElement || !sendButton || !reportsElement || !reportStatus) return;

    function showError(message = '') { errorElement.textContent = message; errorElement.classList.toggle('d-none', !message); }

    function toggleReportsPanel() {
        const isHidden = layoutElement.classList.toggle('reports-hidden');
        reportsToggleButton.title = isHidden ? '展开评审报告' : '隐藏评审报告';
        reportsToggleButton.setAttribute('aria-label', reportsToggleButton.title);
        reportsToggleButton.setAttribute('aria-pressed', String(!isHidden));
        const icon = reportsToggleButton.querySelector('i');
        icon.className = isHidden ? 'bi bi-layout-sidebar-inset' : 'bi bi-layout-sidebar-inset-reverse';
        icon.setAttribute('aria-hidden', 'true');
    }

    async function readJson(response) {
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.detail || '请求失败');
        return payload;
    }

    function sessionTime(value) {
        if (!value) return '';
        return new Intl.DateTimeFormat('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'}).format(new Date(Number(value) * 1000));
    }

    function setBusy(value) {
        isBusy = value;
        sendButton.disabled = value;
        attachButton.disabled = value;
        refreshButton.disabled = value;
        newButton.disabled = value;
        messageInput.disabled = value;
    }

    function clearAttachment() { fileInput.value = ''; fileNameElement.textContent = ''; }

    function renderSessions() {
        sessionsElement.replaceChildren();
        if (!sessions.length) { sessionsElement.textContent = '还没有创作会话'; sessionsElement.className = 'small text-muted py-3'; return; }
        sessionsElement.className = '';
        sessions.forEach((session) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `writer-agent-session${Number(activeSession?.id) === Number(session.id) ? ' is-active' : ''}`;
            const title = document.createElement('div');
            title.className = 'text-truncate'; title.textContent = session.title;
            const meta = document.createElement('div');
            meta.className = 'small text-muted mt-1';
            meta.textContent = `${session.reports?.length ? `${session.reports.length} 份报告` : '等待诊断'} · ${sessionTime(session.updated_at)}`;
            button.append(title, meta);
            button.addEventListener('click', () => loadSession(session.id));
            sessionsElement.append(button);
        });
    }

    function addText(parent, className, value) { const element = document.createElement('div'); element.className = className; element.textContent = value; parent.append(element); return element; }

    function reportForMessage(message) {
        const reports = activeSession?.reports || [];
        return reports.find((item) => item.id === message.report_id)?.report || null;
    }

    function renderThinkingMessage() {
        const article = document.createElement('article');
        article.className = 'writer-agent-message writer-agent-thinking';
        addText(article, 'small text-muted mb-2', '编剧大师');
        const line = document.createElement('div');
        line.className = 'd-flex align-items-center gap-2 text-muted';
        const spinner = document.createElement('span');
        spinner.className = 'spinner-border spinner-border-sm';
        spinner.setAttribute('aria-hidden', 'true');
        const label = document.createElement('span');
        label.textContent = thinkingStages[thinkingStageIndex];
        const dots = document.createElement('span');
        dots.className = 'writer-agent-thinking-dots';
        line.append(spinner, label, dots);
        article.append(line);
        return article;
    }

    function beginThinking(message = '', showPendingMessage = true) {
        pendingMessage = showPendingMessage ? message : '';
        thinkingStageIndex = 0;
        if (thinkingTimer) window.clearInterval(thinkingTimer);
        thinkingTimer = window.setInterval(() => {
            thinkingStageIndex = (thinkingStageIndex + 1) % thinkingStages.length;
            renderConversation();
        }, 3200);
        renderConversation();
    }

    function endThinking(render = true) {
        pendingMessage = '';
        if (thinkingTimer) window.clearInterval(thinkingTimer);
        thinkingTimer = null;
        if (render) renderConversation();
    }

    function renderStructuredReport(parent, report) {
        if (!report) return;
        const details = document.createElement('details');
        details.className = 'border-top mt-3 pt-3';
        const summary = document.createElement('summary');
        summary.className = 'fw-semibold';
        summary.textContent = `结构化评审 · 总分 ${Number(report.scores?.overall || 0)}`;
        details.append(summary);
        const scores = document.createElement('div');
        scores.className = 'd-flex flex-wrap gap-3 small mt-3';
        [['结构', 'structure'], ['人物', 'character'], ['节奏', 'pacing'], ['钩子', 'hook']].forEach(([label, key]) => addText(scores, 'border-start ps-2', `${label} ${Number(report.scores?.[key] || 0)}`));
        details.append(scores);
        if (report.summary) addText(details, 'small mt-3', report.summary);
        if (report.strengths?.length) { const section = document.createElement('section'); section.className = 'small mt-3'; addText(section, 'fw-semibold mb-1', '亮点'); report.strengths.forEach((item) => addText(section, '', `· ${item}`)); details.append(section); }
        if (report.findings?.length) {
            const section = document.createElement('section'); section.className = 'mt-3'; addText(section, 'fw-semibold small mb-2', '问题与建议');
            report.findings.forEach((finding) => {
                const item = document.createElement('article'); item.className = 'small border-top pt-2 mt-2';
                addText(item, 'fw-semibold', `[${finding.priority || 'P2'}] ${finding.dimension || '综合'}：${finding.title || '待优化问题'}`);
                addText(item, 'mt-1', `证据：${finding.evidence || '-'}`); addText(item, 'mt-1', `建议：${finding.suggestion || '-'}`); addText(item, 'mt-1 text-muted', `处理路径：${finding.route || '-'}`);
                section.append(item);
            });
            details.append(section);
        }
        if (report.next_questions?.length) { const section = document.createElement('section'); section.className = 'small mt-3'; addText(section, 'fw-semibold mb-1', '建议继续确认'); report.next_questions.forEach((item) => addText(section, '', `· ${item}`)); details.append(section); }
        parent.append(details);
    }

    function renderMaterialMessage(message) {
        const article = document.createElement('article'); article.className = 'writer-agent-message is-user';
        addText(article, 'small text-muted mb-2', '你');
        const card = document.createElement('details'); card.className = 'writer-agent-material-card p-3';
        const title = document.createElement('summary'); title.className = 'fw-semibold'; title.textContent = message.material_name || message.content || '创作材料'; card.append(title);
        addText(card, 'small text-muted mt-2', `${Number(message.material_length || 0).toLocaleString()} 字符`);
        addText(card, 'small text-break text-wrap mt-2', message.material_preview || '材料已提交');
        article.append(card); return article;
    }

    function renderConversation() {
        conversation.replaceChildren();
        const messages = activeSession?.messages || [];
        const showTemporaryMessages = Boolean(pendingMessage || thinkingTimer);
        emptyElement.classList.toggle('d-none', Boolean(messages.length) || showTemporaryMessages);
        conversation.classList.toggle('d-none', !messages.length && !showTemporaryMessages);
        messages.forEach((message) => {
            if (message.display_user_message === false) return;
            if (message.kind === 'material') { conversation.append(renderMaterialMessage(message)); return; }
            const article = document.createElement('article');
            article.className = `writer-agent-message${message.role === 'user' ? ' is-user' : ''}`;
            addText(article, 'small text-muted mb-2', message.role === 'assistant' ? '编剧大师' : '你');
            addText(article, 'text-break text-wrap', message.content || '');
            if (message.role === 'assistant') renderStructuredReport(article, reportForMessage(message));
            conversation.append(article);
        });
        if (pendingMessage) {
            const article = document.createElement('article');
            article.className = 'writer-agent-message is-user';
            addText(article, 'small text-muted mb-2', '你');
            addText(article, 'text-break text-wrap', pendingMessage);
            conversation.append(article);
        }
        if (thinkingTimer) conversation.append(renderThinkingMessage());
        conversation.lastElementChild?.scrollIntoView({block: 'nearest'});
    }

    function renderReportFiles() {
        reportsElement.replaceChildren();
        const reports = [...(activeSession?.reports || [])].reverse();
        reportStatus.textContent = reports.length ? `${reports.length} 份报告` : activeSession ? '等待诊断' : '等待材料';
        if (!reports.length) { reportsElement.className = 'small text-muted'; reportsElement.textContent = '每轮有效评审会在这里生成可下载的 Markdown 报告。'; return; }
        reportsElement.className = '';
        reports.forEach((entry, index) => {
            const report = entry.report || {};
            const row = document.createElement('article'); row.className = 'writer-agent-report-file';
            const heading = document.createElement('div'); heading.className = 'd-flex align-items-start gap-2';
            const content = document.createElement('div'); content.className = 'flex-grow-1 min-width-0';
            addText(content, 'fw-semibold small', index === 0 ? `最新评审 · ${Number(report.scores?.overall || 0)} 分` : `评审记录 · ${Number(report.scores?.overall || 0)} 分`);
            addText(content, 'small text-muted mt-1', sessionTime(entry.created_at));
            if (report.summary) addText(content, 'small writer-agent-report-preview mt-2', report.summary);
            const download = document.createElement('button'); download.type = 'button'; download.className = 'btn btn-outline-light btn-sm'; download.title = '下载 Markdown 报告'; download.setAttribute('aria-label', '下载 Markdown 报告'); download.innerHTML = '<i class="bi bi-download" aria-hidden="true"></i>';
            download.addEventListener('click', () => downloadReport(entry.id));
            heading.append(content, download); row.append(heading); reportsElement.append(row);
        });
    }

    function renderActiveSession() {
        const hasSession = Boolean(activeSession);
        detailElement.classList.toggle('d-none', !hasSession);
        if (hasSession) { titleElement.textContent = activeSession.title || '未命名创作会话'; materialNameElement.textContent = activeSession.filename ? `来源文件：${activeSession.filename}` : '来源：直接输入'; }
        renderSessions(); renderConversation(); renderReportFiles(); setBusy(isBusy);
    }

    function updateSession(updated) {
        activeSession = updated;
        const index = sessions.findIndex((item) => Number(item.id) === Number(updated.id));
        if (index >= 0) sessions[index] = updated; else sessions.unshift(updated);
        sessions.sort((left, right) => Number(right.updated_at || 0) - Number(left.updated_at || 0));
        renderActiveSession();
    }

    async function loadSession(sessionId) { try { showError(); updateSession(await readJson(await aiworkerApi.request(`/api/writer-agent/sessions/${sessionId}`))); } catch (error) { showError(error.message); } }

    async function loadSessions(selectFirst = false) {
        try {
            showError(); const payload = await readJson(await aiworkerApi.request('/api/writer-agent/sessions')); sessions = payload.items || [];
            if (activeSession) activeSession = sessions.find((item) => Number(item.id) === Number(activeSession.id)) || null;
            if (!activeSession && selectFirst && sessions.length) await loadSession(sessions[0].id); else renderActiveSession();
        } catch (error) { showError(error.message); }
    }

    async function ask(message, showPendingMessage = true, displayUserMessage = true) {
        beginThinking(message, showPendingMessage);
        try {
            const updated = await readJson(await aiworkerApi.request(`/api/writer-agent/sessions/${activeSession.id}/messages`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message, display_user_message: displayUserMessage})}));
            endThinking(false);
            updateSession(updated);
        } catch (error) {
            endThinking();
            throw error;
        }
    }

    function deriveTitle(content) {
        const firstLine = String(content || '').split(/\r?\n/).map((line) => line.trim()).find(Boolean) || '';
        return firstLine.replace(/^#+\s*/, '').slice(0, 48) || '未命名创作会话';
    }

    async function createInitialSession(file, content) {
        const response = file
            ? await aiworkerApi.request('/api/writer-agent/sessions/file', {method: 'POST', headers: {'X-Writer-Filename': encodeURIComponent(file.name)}, body: file})
            : await aiworkerApi.request('/api/writer-agent/sessions', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: deriveTitle(content), content})});
        updateSession(await readJson(response));
    }

    async function appendUploadedMaterial(file) {
        const updated = await readJson(await aiworkerApi.request(`/api/writer-agent/sessions/${activeSession.id}/materials/file`, {method: 'POST', headers: {'X-Writer-Filename': encodeURIComponent(file.name)}, body: file}));
        updateSession(updated);
    }

    async function sendMessage() {
        const file = fileInput.files?.[0]; const message = messageInput.value.trim();
        if (!file && !message) return;
        try {
            showError(); setBusy(true);
            if (!activeSession) {
                await createInitialSession(file, message);
                if (file && message) await ask(message, true);
                else await ask(materialReceiptPrompt, false, false);
            } else {
                if (file) await appendUploadedMaterial(file);
                if (message) await ask(message, true);
                else if (file) await ask(materialReceiptPrompt, false, false);
            }
            messageInput.value = ''; clearAttachment();
        } catch (error) { showError(error.message); } finally { setBusy(false); }
    }

    async function downloadReport(reportId) {
        if (!activeSession) return;
        try {
            const response = await aiworkerApi.request(`/api/writer-agent/sessions/${activeSession.id}/reports/${reportId}/export`);
            if (!response.ok) { const payload = await response.json().catch(() => ({})); throw new Error(payload.detail || '下载评审报告失败'); }
            const link = document.createElement('a'); link.href = URL.createObjectURL(await response.blob()); link.download = `writer-agent-${activeSession.id}-${reportId}.md`; link.click(); URL.revokeObjectURL(link.href);
        } catch (error) { showError(error.message); }
    }

    fileInput.addEventListener('change', () => { fileNameElement.textContent = fileInput.files?.[0]?.name || ''; });
    attachButton.addEventListener('click', () => fileInput.click());
    sendButton.addEventListener('click', sendMessage);
    messageInput.addEventListener('keydown', (event) => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); sendMessage(); } });
    refreshButton.addEventListener('click', () => loadSessions(false));
    reportsToggleButton.addEventListener('click', toggleReportsPanel);
    newButton.addEventListener('click', () => { activeSession = null; clearAttachment(); messageInput.value = ''; renderActiveSession(); messageInput.focus(); });
    loadSessions(true);
})();
