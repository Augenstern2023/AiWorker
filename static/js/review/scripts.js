(function () {
    'use strict';

    const source = document.getElementById('script-review-source');
    const mode = document.getElementById('script-review-mode');
    const project = document.getElementById('script-review-project');
    const projectWrap = document.getElementById('script-review-project-wrap');
    const textWrap = document.getElementById('script-review-text-wrap');
    const text = document.getElementById('script-review-text');
    const episodes = document.getElementById('script-review-episodes');
    const submit = document.getElementById('script-review-submit');
    const result = document.getElementById('script-review-result');
    const history = document.getElementById('script-review-history');
    const error = document.getElementById('script-review-error');
    let activeReviewId = null;
    let reviewProjects = [];
    let selectedProject = null;

    if (!source || !mode || !project || !projectWrap || !textWrap || !text || !episodes || !submit || !result || !history || !error) return;

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
        return { queued: '等待审核', running: '审核中', pending_confirmation: '待人工确认', confirmed: '已确认', stale: '已过期', failed: '审核失败' }[value] || value || '-';
    }

    function sourceLabel(review) {
        return review.source_type === 'text' ? '粘贴文本' : (review.source_payload?.project_name || `项目 ${review.project_id}`);
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
        episodeNumbers.forEach((episodeNumber) => {
            const wrap = document.createElement('label');
            wrap.className = 'form-check form-check-inline m-0';
            const input = document.createElement('input');
            input.className = 'form-check-input';
            input.type = 'checkbox';
            input.name = 'script-review-episode';
            input.value = String(episodeNumber);
            const caption = document.createElement('span');
            caption.className = 'form-check-label small';
            caption.textContent = `第 ${episodeNumber} 集`;
            wrap.append(input, caption);
            episodes.append(wrap);
        });
    }

    async function loadSelectedProject() {
        const id = Number(project.value || 0);
        selectedProject = null;
        if (!id) return renderEpisodes();
        try {
            selectedProject = await readJson(await aiworkerApi.request(`/api/creative/projects/${id}`));
        } catch (requestError) {
            showError(requestError.message);
        }
        renderEpisodes();
    }

    async function loadProjects() {
        const payload = await readJson(await aiworkerApi.request('/api/creative/projects'));
        reviewProjects = payload.items || [];
        project.replaceChildren();
        reviewProjects.forEach((item) => {
            const option = document.createElement('option');
            option.value = String(item.id);
            option.textContent = item.name;
            project.append(option);
        });
        await loadSelectedProject();
    }

    function toggleSource() {
        const pasted = source.value === 'text';
        projectWrap.classList.toggle('d-none', pasted);
        textWrap.classList.toggle('d-none', !pasted);
        episodes.classList.toggle('d-none', pasted || mode.value === 'collection');
        mode.disabled = pasted;
        if (pasted) mode.value = 'single';
        renderEpisodes();
    }

    function selectedEpisodes() {
        const selectedEpisode = episodes.querySelector('select');
        if (selectedEpisode) return selectedEpisode.value ? [Number(selectedEpisode.value)] : [];
        return [...episodes.querySelectorAll('input:checked')].map((input) => Number(input.value));
    }

    async function createReview() {
        showError();
        submit.disabled = true;
        try {
            const payload = source.value === 'text'
                ? { source_type: 'text', mode: 'single', text: text.value }
                : { source_type: 'project', mode: mode.value, project_id: Number(project.value), episode_numbers: selectedEpisodes() };
            const created = await readJson(await aiworkerApi.request('/api/script-reviews', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
            }));
            activeReviewId = created.review.id;
            renderReview(created.review);
            await loadHistory();
            pollReview(activeReviewId);
        } catch (requestError) {
            showError(requestError.message);
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

    function finding(item) {
        const node = document.createElement('li');
        node.className = 'mb-2';
        const evidence = item.evidence ? ` 证据：${item.evidence}` : '';
        node.textContent = `[${item.severity || 'P2'}] ${findingTitle(item)}${evidence}${item.suggestion ? ` 建议：${item.suggestion}` : ''}`;
        return node;
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
        if (review.status !== 'confirmed' || review.final_verdict !== '建议修改' || !review.project_id) return;
        const section = document.createElement('section');
        section.className = 'border-top pt-4 mt-4';
        section.append(sectionHeading('AI 优化草稿'));
        const modeSelect = document.createElement('select');
        modeSelect.className = 'form-select form-select-sm mb-3';
        modeSelect.innerHTML = '<option value="direct">直接优化场次剧本</option><option value="structure">结构优化（Beats + 场次剧本）</option>';
        section.append(modeSelect);
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
        section.append(selectedFindings);
        const start = document.createElement('button');
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
            try {
                await readJson(await aiworkerApi.request(`/api/script-reviews/${review.id}/optimizations`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mode: modeSelect.value, findings }),
                }));
                await loadOptimizationHistory(review, history);
            } catch (requestError) {
                showError(requestError.message);
            } finally {
                start.disabled = false;
            }
        });
        section.append(start);
        const history = document.createElement('div');
        history.className = 'mt-3';
        section.append(history);
        result.append(section);
        loadOptimizationHistory(review, history);
    }

    function optimizationStatus(value) {
        return { queued: '等待生成', running: '生成中', completed: '已生成', failed: '生成失败', ready: '待采纳', accepted: '已采纳', rejected: '已放弃' }[value] || value;
    }

    async function loadOptimizationHistory(review, container) {
        try {
            const payload = await readJson(await aiworkerApi.request(`/api/script-reviews/${review.id}/optimizations`));
            container.replaceChildren();
            (payload.items || []).forEach((optimization) => {
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
                    compare.href = `/writing/projects/${optimization.project_id}/episodes/${draft.episode_number}/${stage}?optimization_draft=${draft.id}`;
                    compare.textContent = '查看对比';
                    row.append(compare);
                    if (draft.status === 'ready') {
                        const accept = document.createElement('button');
                        accept.type = 'button';
                        accept.className = 'btn btn-success btn-sm';
                        accept.textContent = '采纳';
                        accept.addEventListener('click', () => acceptOptimizationDraft(draft.id, review, container));
                        const reject = document.createElement('button');
                        reject.type = 'button';
                        reject.className = 'btn btn-outline-secondary btn-sm';
                        reject.textContent = '放弃';
                        reject.addEventListener('click', () => rejectOptimizationDraft(draft.id, review, container));
                        row.append(accept, reject);
                    }
                    container.append(row);
                });
            });
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
        heading.append(title, status);
        result.append(heading);
        const coverage = document.createElement('div');
        coverage.className = 'small text-muted mb-3';
        const episodeNumbers = review.episode_numbers || [];
        coverage.textContent = `${{ single: '单集', multiple: '多集', collection: '合集' }[review.mode] || '审核'} · 实际覆盖 ${episodeNumbers.length} 集（${episodeNumbers.map((number) => `第 ${number} 集`).join('、') || '-'}）`;
        result.append(coverage);
        if (!review.report || !review.report.ai_verdict) return;
        result.append(sectionHeading('修改建议'));
        const overview = document.createElement('p');
        overview.className = 'mb-3';
        overview.textContent = `AI 建议：${review.ai_verdict}。${review.report.summary || ''}`;
        result.append(overview);
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
        if (review.status === 'pending_confirmation') {
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
            renderReview(await readJson(await aiworkerApi.request(`/api/script-reviews/${reviewId}`)));
        } catch (requestError) {
            showError(requestError.message);
        }
    }

    async function pollReview(reviewId) {
        try {
            const review = await readJson(await aiworkerApi.request(`/api/script-reviews/${reviewId}`));
            if (review.id === activeReviewId) renderReview(review);
            if (['queued', 'running'].includes(review.status)) window.setTimeout(() => pollReview(reviewId), 2000);
        } catch (requestError) {
            showError(requestError.message);
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
    document.getElementById('script-review-refresh')?.addEventListener('click', async () => { await loadProjects(); await loadHistory(); });
    (async () => { try { await loadProjects(); await loadHistory(); toggleSource(); } catch (requestError) { showError(requestError.message); } })();
}());
