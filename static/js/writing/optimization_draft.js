(function () {
    'use strict';

    const draftId = new URLSearchParams(window.location.search).get('optimization_draft');
    let reviewId = Number(new URLSearchParams(window.location.search).get('review_id') || 0);
    let hasUnsavedChanges = false;
    if (!window.aiworkerApi) return;
    const expectedKind = window.location.pathname.endsWith('/beats') ? 'beats' : 'script';

    function hideOriginalScriptWorkspace() {
        if (expectedKind !== 'script' || !draftId || document.getElementById('optimization-draft-workspace-style')) return;
        const style = document.createElement('style');
        style.id = 'optimization-draft-workspace-style';
        // Alpine renders the original workspace after this script in some page loads.
        style.textContent = '#project-script-workspace { display: none !important; }';
        document.head.append(style);
    }

    function showReviewBackLink(reviewId = null) {
        if (!draftId) return;
        const link = document.getElementById('optimization-draft-review-back-link');
        if (!link) return;
        link.href = reviewId ? `/review/scripts?review_id=${reviewId}` : '/review/scripts';
        link.title = '返回审核';
        link.setAttribute('aria-label', '返回审核');
        link.hidden = false;
    }

    function reviewPageUrl() {
        return reviewId ? `/review/scripts?review_id=${encodeURIComponent(reviewId)}` : '/review/scripts';
    }

    async function linkBackToReview(draft) {
        if (!draft.optimization_id) return null;
        const response = await aiworkerApi.request(`/api/script-optimizations/${encodeURIComponent(draft.optimization_id)}`);
        const optimization = await response.json();
        if (response.ok && optimization.review_id) {
            reviewId = Number(optimization.review_id);
            showReviewBackLink(reviewId);
            return optimization;
        }
        return null;
    }

    function setUnsavedChanges(value) {
        hasUnsavedChanges = Boolean(value);
    }

    window.addEventListener('beforeunload', (event) => {
        if (!hasUnsavedChanges) return;
        event.preventDefault();
        event.returnValue = '';
    });

    function waitForScriptWorkspace(timeoutMs = 1200) {
        return new Promise((resolve) => {
            const existing = document.getElementById('project-script-workspace');
            if (existing) {
                resolve(existing);
                return;
            }
            const startedAt = Date.now();
            const timer = window.setInterval(() => {
                const workspace = document.getElementById('project-script-workspace');
                if (workspace || Date.now() - startedAt >= timeoutMs) {
                    window.clearInterval(timer);
                    resolve(workspace || null);
                }
            }, 30);
        });
    }

    function pageContext() {
        const match = window.location.pathname.match(/\/writing\/projects\/(\d+)\/episodes\/(\d+)\/(beats|scripts)$/);
        if (!match) return null;
        return { projectId: match[1], episodeNumber: match[2], stage: match[3] === 'beats' ? 'beats' : 'script' };
    }

    function mount(section, before = null) {
        if (before?.parentElement) {
            before.parentElement.insertBefore(section, before);
            return;
        }
        document.querySelector('main')?.prepend(section);
    }

    function draftLabel(draft) {
        const status = { ready: '待采纳', accepted: '已采纳', rejected: '已放弃' }[draft.status] || draft.status;
        return `第 ${draft.episode_number} 集 · ${status}`;
    }

    function appendText(parent, value, className = '') {
        const node = document.createElement('div');
        node.className = className;
        node.textContent = String(value || '').trim() || '未填写';
        parent.append(node);
    }

    function blockLabel(block) {
        return {
            action: '动作', dialogue: '台词', voiceover: '画外音',
            inner_voice: '内心独白', cut: '切镜',
        }[block.type] || '内容';
    }

    function renderScriptPreview(content) {
        const fragment = document.createDocumentFragment();
        const title = document.createElement('div');
        title.className = 'fw-semibold mb-3';
        title.textContent = content.episode_title || '未命名剧本';
        fragment.append(title);
        const scenes = Array.isArray(content.scenes) ? content.scenes : [];
        if (!scenes.length) {
            appendText(fragment, '暂无场次内容', 'small text-muted');
            return fragment;
        }
        scenes.forEach((scene) => {
            const section = document.createElement('section');
            section.className = 'border-top pt-3 mt-3';
            const heading = document.createElement('div');
            heading.className = 'fw-semibold small';
            heading.textContent = `第 ${scene.sequence_no || '-'} 段 · 第 ${scene.scene_no || '-'} 场`;
            const meta = document.createElement('div');
            meta.className = 'small text-muted mb-2';
            const details = [scene.time_of_day, scene.interior_exterior, scene.location].filter(Boolean);
            meta.textContent = details.join(' · ') || '场景信息未填写';
            section.append(heading, meta);
            const people = Array.isArray(scene.characters) && scene.characters.length ? `人物：${scene.characters.join('、')}` : '';
            const props = Array.isArray(scene.props) && scene.props.length ? `道具：${scene.props.join('、')}` : '';
            if (people || props) appendText(section, [people, props].filter(Boolean).join(' · '), 'small text-muted mb-2');
            (scene.blocks || []).forEach((block) => {
                const line = document.createElement('div');
                line.className = 'small mb-2';
                const kind = document.createElement('span');
                kind.className = 'text-muted me-2';
                kind.textContent = `${blockLabel(block)}：`;
                line.append(kind);
                if (block.speaker) {
                    const speaker = document.createElement('span');
                    speaker.className = 'fw-semibold me-1';
                    speaker.textContent = `${block.speaker}${block.modifier ? `（${block.modifier}）` : ''}`;
                    line.append(speaker);
                }
                const text = document.createElement('span');
                text.style.whiteSpace = 'pre-wrap';
                text.textContent = block.text || '未填写';
                line.append(text);
                section.append(line);
            });
            fragment.append(section);
        });
        return fragment;
    }

    function renderBeatsPreview(content) {
        const fragment = document.createDocumentFragment();
        const beats = Array.isArray(content.beats) ? content.beats : [];
        if (!beats.length) {
            appendText(fragment, '暂无 Beats 内容', 'small text-muted');
            return fragment;
        }
        const fields = [
            ['scene', '场景'], ['characters', '人物'], ['goal', '行动目标'], ['obstacle', '阻力'],
            ['emotion_change', '情绪变化'], ['state_change', '局面变化'], ['cost', '代价'],
            ['opposition_tactic', '对抗策略'], ['turn', '转折'], ['hook', '结尾钩子'],
        ];
        beats.forEach((beat) => {
            const section = document.createElement('section');
            section.className = 'border-top pt-3 mt-3';
            const heading = document.createElement('div');
            heading.className = 'fw-semibold small mb-2';
            heading.textContent = `节拍 ${beat.order || '-'}`;
            section.append(heading);
            fields.forEach(([key, label]) => {
                const value = Array.isArray(beat[key]) ? beat[key].join('、') : beat[key];
                if (!value) return;
                const line = document.createElement('div');
                line.className = 'small mb-1';
                const name = document.createElement('span');
                name.className = 'text-muted me-2';
                name.textContent = `${label}：`;
                const text = document.createElement('span');
                text.style.whiteSpace = 'pre-wrap';
                text.textContent = value;
                line.append(name, text);
                section.append(line);
            });
            fragment.append(section);
        });
        return fragment;
    }

    function scriptComparisonLines(content) {
        const lines = [{text: content.episode_title || '未命名剧本', heading: true, scope: 'title'}];
        (content.scenes || []).forEach((scene, sceneIndex) => {
            const scope = `scene:${scene.sequence_no || sceneIndex + 1}:${scene.scene_no || sceneIndex + 1}`;
            lines.push({
                text: `第 ${scene.sequence_no || '-'} 段 · 第 ${scene.scene_no || '-'} 场`,
                heading: true,
                scope,
            });
            const details = [scene.time_of_day, scene.interior_exterior, scene.location].filter(Boolean);
            if (details.length) lines.push({text: details.join(' · '), scope});
            if (Array.isArray(scene.characters) && scene.characters.length) {
                lines.push({text: `人物：${scene.characters.join('、')}`, scope});
            }
            if (Array.isArray(scene.props) && scene.props.length) {
                lines.push({text: `道具：${scene.props.join('、')}`, scope});
            }
            (scene.blocks || []).forEach((block) => {
                const speaker = block.speaker
                    ? `${block.speaker}${block.modifier ? `（${block.modifier}）` : ''} `
                    : '';
                lines.push({text: `${blockLabel(block)}：${speaker}${block.text || '未填写'}`, scope});
            });
        });
        return lines;
    }

    function beatsComparisonLines(content) {
        const fields = [
            ['scene', '场景'], ['characters', '人物'], ['goal', '行动目标'], ['obstacle', '阻力'],
            ['emotion_change', '情绪变化'], ['state_change', '局面变化'], ['cost', '代价'],
            ['opposition_tactic', '对抗策略'], ['turn', '转折'], ['hook', '结尾钩子'],
        ];
        const lines = [];
        (content.beats || []).forEach((beat, beatIndex) => {
            const scope = `beat:${beat.order || beatIndex + 1}`;
            lines.push({text: `节拍 ${beat.order || '-'}`, heading: true, scope});
            fields.forEach(([key, label]) => {
                const value = Array.isArray(beat[key]) ? beat[key].join('、') : beat[key];
                if (value) lines.push({text: `${label}：${value}`, scope});
            });
        });
        return lines.length ? lines : [{text: '暂无 Beats 内容', scope: 'empty'}];
    }

    function comparisonLines(content, kind) {
        return kind === 'beats'
            ? beatsComparisonLines(content || {})
            : scriptComparisonLines(content || {});
    }

    function uniqueLinePositions(lines) {
        const positions = new Map();
        lines.forEach((line, index) => {
            const matches = positions.get(line) || [];
            matches.push(index);
            positions.set(line, matches);
        });
        return positions;
    }

    function computeLineDiff(currentLines, draftLines) {
        const currentStatus = Array(currentLines.length).fill('diff-removed');
        const draftStatus = Array(draftLines.length).fill('diff-added');
        const currentText = currentLines.map((line) => `${line.scope || ''}\u0000${line.text}`);
        const draftText = draftLines.map((line) => `${line.scope || ''}\u0000${line.text}`);
        const currentPositions = uniqueLinePositions(currentText);
        const draftPositions = uniqueLinePositions(draftText);
        const anchors = currentText.flatMap((line, currentIndex) => {
            const currentMatches = currentPositions.get(line) || [];
            const draftMatches = draftPositions.get(line) || [];
            return currentMatches.length === 1 && draftMatches.length === 1
                ? [{currentIndex, draftIndex: draftMatches[0]}]
                : [];
        });
        const tails = [];
        const predecessors = Array(anchors.length).fill(-1);
        const tailIndices = [];
        anchors.forEach((anchor, anchorIndex) => {
            let low = 0;
            let high = tails.length;
            while (low < high) {
                const middle = Math.floor((low + high) / 2);
                if (tails[middle] < anchor.draftIndex) low = middle + 1;
                else high = middle;
            }
            if (low > 0) predecessors[anchorIndex] = tailIndices[low - 1];
            tails[low] = anchor.draftIndex;
            tailIndices[low] = anchorIndex;
        });
        let anchorIndex = tailIndices[tails.length - 1];
        while (anchorIndex !== undefined && anchorIndex >= 0) {
            const anchor = anchors[anchorIndex];
            currentStatus[anchor.currentIndex] = 'unchanged';
            draftStatus[anchor.draftIndex] = 'unchanged';
            anchorIndex = predecessors[anchorIndex];
        }
        return {currentStatus, draftStatus};
    }

    function renderDiffPreview(lines, statuses) {
        const fragment = document.createDocumentFragment();
        lines.forEach((line, index) => {
            const row = document.createElement('div');
            row.className = `optimization-diff-line${line.heading ? ' optimization-diff-heading' : ''}${statuses[index] === 'unchanged' ? '' : ` ${statuses[index]}`}`;
            row.textContent = line.text;
            fragment.append(row);
        });
        return fragment;
    }

    function previewColumn(title, content, kind, statuses = null) {
        const column = document.createElement('div');
        column.className = 'col-12 col-xl-6';
        const heading = document.createElement('h6');
        heading.className = 'mb-2';
        heading.textContent = title;
        const body = document.createElement('div');
        body.className = 'border p-3';
        body.append(
            statuses
                ? renderDiffPreview(comparisonLines(content, kind), statuses)
                : kind === 'beats' ? renderBeatsPreview(content || {}) : renderScriptPreview(content || {}),
        );
        column.append(heading, body);
        return column;
    }

    async function loadCurrentEpisode(context) {
        const response = await aiworkerApi.request(`/api/creative/projects/${context.projectId}`);
        const project = await response.json();
        if (!response.ok) return null;
        const candidates = (project.episodes || []).filter((item) => (
            item.stage === context.stage && Number(item.episode_number) === Number(context.episodeNumber)
        ));
        if (project.workflow_mode === 'free') {
            const head = (project.resource_heads || []).find((item) => (
                item.stage === context.stage && Number(item.episode_number) === Number(context.episodeNumber)
            ));
            return candidates.find((item) => String(item.id) === String(head?.resource_ref || '')) || null;
        }
        return candidates.sort((left, right) => (
            Number(right.version || 0) - Number(left.version || 0) || Number(right.id || 0) - Number(left.id || 0)
        ))[0] || null;
    }

    function editorField(label, value, update, {multiline = false, type = 'text', column = 'col-md-3'} = {}) {
        const wrap = document.createElement('div');
        wrap.className = column;
        const caption = document.createElement('label');
        caption.className = 'form-label small';
        caption.textContent = label;
        const control = document.createElement(multiline ? 'textarea' : 'input');
        control.className = 'form-control form-control-sm';
        if (multiline) control.rows = 2;
        else control.type = type;
        control.value = value || '';
        control.addEventListener('input', () => {
            update(control.value);
            setUnsavedChanges(true);
        });
        wrap.append(caption, control);
        return wrap;
    }

    function setList(target, key, value) {
        target[key] = value.split(/[,，]/).map((item) => item.trim()).filter(Boolean);
    }

    function scriptText(content) {
        const lines = [content.episode_title || '未命名剧本', ''];
        (content.scenes || []).forEach((scene) => {
            lines.push(`[${scene.sequence_no || '-'}]-[${scene.scene_no || '-'}] ${[scene.time_of_day, scene.interior_exterior, scene.location].filter(Boolean).join(' ')}`);
            (scene.blocks || []).forEach((block) => {
                const speaker = block.speaker ? `${block.speaker}${block.modifier ? `（${block.modifier}）` : ''}：` : '';
                lines.push(`${blockLabel(block)} ${speaker}${block.text || ''}`.trim());
            });
            lines.push('');
        });
        return lines.join('\n');
    }

    function downloadOptimizationDraft(content) {
        const blob = new Blob([scriptText(content)], {type: 'text/plain;charset=utf-8'});
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = `${content.episode_title || 'AI优化草稿'}.txt`;
        link.click();
        URL.revokeObjectURL(link.href);
    }

    async function saveOptimizationDraft(draft, content, button, message) {
        button.disabled = true;
        message.textContent = '正在保存草稿...';
        try {
            const response = await aiworkerApi.request(`/api/script-optimization-drafts/${encodeURIComponent(draft.id)}`, {
                method: 'PATCH', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({content}),
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.detail || '保存优化草稿失败');
            setUnsavedChanges(false);
            message.textContent = '草稿已保存，可以稍后继续编辑。';
        } catch (error) {
            message.textContent = error.message || '保存优化草稿失败';
        } finally {
            button.disabled = false;
        }
    }

    async function regenerateOptimizationDraft(draft, button, message) {
        if (hasUnsavedChanges && !window.confirm('当前有未保存修改，重新生成后这些修改不会保留。确定继续吗？')) return;
        button.disabled = true;
        message.textContent = '正在创建本集重新生成任务...';
        try {
            const response = await aiworkerApi.request(`/api/script-optimization-drafts/${encodeURIComponent(draft.id)}/regenerate`, {
                method: 'POST',
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.detail || '重新生成本集失败');
            setUnsavedChanges(false);
            window.location.assign(reviewPageUrl());
        } catch (error) {
            message.textContent = error.message || '重新生成本集失败';
            button.disabled = false;
        }
    }

    async function applyOptimizationDraft(draft, content, lockAfterAccept, button, message) {
        if (lockAfterAccept && !window.confirm('应用后将立即锁定这个场次剧本版本，确定继续吗？')) return;
        button.disabled = true;
        message.textContent = lockAfterAccept ? '正在应用并锁定...' : '正在应用为新版本...';
        try {
            const response = await aiworkerApi.request(`/api/script-optimization-drafts/${encodeURIComponent(draft.id)}/accept`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({content, lock_after_accept: lockAfterAccept}),
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.detail || '应用优化草稿失败');
            setUnsavedChanges(false);
            message.textContent = lockAfterAccept ? '已应用并锁定场次剧本。' : '已应用为新的可编辑版本。';
            window.location.assign(reviewPageUrl());
        } catch (error) {
            message.textContent = error.message || '应用优化草稿失败';
            button.disabled = false;
        }
    }

    function renderScriptEditor(draft, workspace = null) {
        const content = structuredClone(draft.content || {});
        content.scenes = Array.isArray(content.scenes) ? content.scenes : [];
        workspace?.classList.add('d-none');
        const section = document.createElement('section');
        section.className = 'border-top border-bottom py-3 mb-3';
        const header = document.createElement('div');
        header.className = 'd-flex justify-content-between align-items-center gap-3 mb-3';
        const title = document.createElement('h5');
        title.className = 'mb-0';
        title.textContent = `AI 优化场次剧本 · 第 ${draft.episode_number} 集`;
        const actions = document.createElement('div');
        actions.className = 'd-flex flex-wrap gap-2';
        const download = document.createElement('button');
        download.type = 'button';
        download.className = 'btn btn-outline-info btn-sm';
        download.textContent = '下载草稿';
        download.addEventListener('click', () => downloadOptimizationDraft(content));
        const save = document.createElement('button');
        save.type = 'button';
        save.className = 'btn btn-outline-primary btn-sm';
        save.textContent = '保存草稿';
        const apply = document.createElement('button');
        apply.type = 'button';
        apply.className = 'btn btn-primary btn-sm';
        apply.textContent = '应用为新版本';
        const applyAndLock = document.createElement('button');
        applyAndLock.type = 'button';
        applyAndLock.className = 'btn btn-warning btn-sm';
        applyAndLock.textContent = '应用并锁定';
        const regenerate = document.createElement('button');
        regenerate.type = 'button';
        regenerate.className = 'btn btn-outline-warning btn-sm';
        regenerate.textContent = '重新生成本集';
        const message = document.createElement('div');
        message.className = 'small text-muted ms-auto';
        save.addEventListener('click', () => saveOptimizationDraft(draft, content, save, message));
        apply.addEventListener('click', () => applyOptimizationDraft(draft, content, false, apply, message));
        applyAndLock.addEventListener('click', () => applyOptimizationDraft(draft, content, true, applyAndLock, message));
        regenerate.addEventListener('click', () => regenerateOptimizationDraft(draft, regenerate, message));
        actions.append(download, save, regenerate, apply, applyAndLock);
        header.append(title, actions);
        section.append(header);
        section.append(editorField('集标题', content.episode_title, (value) => { content.episode_title = value; }, {column: 'col-12'}));
        content.scenes.forEach((scene, sceneIndex) => {
            const sceneSection = document.createElement('section');
            sceneSection.className = 'border-top pt-3 mt-3';
            const heading = document.createElement('div');
            heading.className = 'small fw-semibold mb-2';
            heading.textContent = `第 ${sceneIndex + 1} 场`;
            const fields = document.createElement('div');
            fields.className = 'row g-2';
            fields.append(
                editorField('段', scene.sequence_no, (value) => { scene.sequence_no = Number(value); }, {type: 'number'}),
                editorField('场', scene.scene_no, (value) => { scene.scene_no = Number(value); }, {type: 'number'}),
                editorField('时间', scene.time_of_day, (value) => { scene.time_of_day = value; }),
                editorField('内外', scene.interior_exterior, (value) => { scene.interior_exterior = value; }),
                editorField('场景', scene.location, (value) => { scene.location = value; }, {column: 'col-md-6'}),
                editorField('道具', (scene.props || []).join('、'), (value) => setList(scene, 'props', value)),
                editorField('出场人物', (scene.characters || []).join('、'), (value) => setList(scene, 'characters', value)),
            );
            sceneSection.append(heading, fields);
            (scene.blocks || []).forEach((block) => {
                const row = document.createElement('div');
                row.className = 'row g-2 mt-1';
                const typeWrap = document.createElement('div');
                typeWrap.className = 'col-md-2';
                const typeSelect = document.createElement('select');
                typeSelect.className = 'form-select form-select-sm';
                [['action', '动作'], ['dialogue', '台词'], ['voiceover', '画外音'], ['inner_voice', '内心独白'], ['cut', '切镜']].forEach(([value, label]) => {
                    const option = document.createElement('option');
                    option.value = value;
                    option.textContent = label;
                    option.selected = block.type === value;
                    typeSelect.append(option);
                });
                typeSelect.addEventListener('change', () => {
                    block.type = typeSelect.value;
                    setUnsavedChanges(true);
                });
                typeWrap.append(typeSelect);
                row.append(
                    typeWrap,
                    editorField('人物', block.speaker, (value) => { block.speaker = value; }, {column: 'col-md-2'}),
                    editorField('情绪', block.modifier, (value) => { block.modifier = value; }, {column: 'col-md-2'}),
                    editorField('内容', block.text, (value) => { block.text = value; }, {multiline: true, column: 'col-md-6'}),
                );
                sceneSection.append(row);
            });
            section.append(sceneSection);
        });
        section.append(message);
        mount(section, workspace);
    }

    function renderDraftNavigation(draft, optimization, before = null) {
        if (!optimization) return;
        const peers = (optimization.drafts || [])
            .filter((item) => item.kind === draft.kind)
            .sort((left, right) => Number(left.episode_number) - Number(right.episode_number) || Number(left.id) - Number(right.id));
        const index = peers.findIndex((item) => Number(item.id) === Number(draft.id));
        if (index < 0) return;
        const navigation = document.createElement('nav');
        navigation.className = 'd-flex flex-wrap justify-content-between align-items-center gap-2 mb-3';
        navigation.setAttribute('aria-label', '优化草稿导航');
        const position = document.createElement('span');
        position.className = 'small text-muted';
        position.textContent = `草稿 ${index + 1}/${peers.length}`;
        const actions = document.createElement('div');
        actions.className = 'd-flex gap-2';
        const draftUrl = (item) => {
            const stage = item.kind === 'beats' ? 'beats' : 'scripts';
            return `/writing/projects/${optimization.project_id}/episodes/${item.episode_number}/${stage}?optimization_draft=${item.id}&review_id=${reviewId || optimization.review_id}`;
        };
        const previous = document.createElement(index > 0 ? 'a' : 'button');
        previous.className = 'btn btn-outline-light btn-sm';
        previous.textContent = '上一份';
        if (index > 0) previous.href = draftUrl(peers[index - 1]);
        else previous.disabled = true;
        const next = document.createElement(index < peers.length - 1 ? 'a' : 'button');
        next.className = 'btn btn-outline-light btn-sm';
        next.textContent = '下一份';
        if (index < peers.length - 1) next.href = draftUrl(peers[index + 1]);
        else next.disabled = true;
        actions.append(previous, next);
        navigation.append(position, actions);
        mount(navigation, before);
    }

    function renderDraftComparison(draft, current, before = null) {
        const section = document.createElement('section');
        section.className = 'border-top border-bottom py-3 mb-3';
        const header = document.createElement('div');
        header.className = 'd-flex flex-wrap justify-content-between align-items-center gap-2 mb-2';
        const heading = document.createElement('h5');
        heading.className = 'mb-0';
        heading.textContent = `AI 优化草稿 · 第 ${draft.episode_number} 集`;
        header.append(heading);
        const message = document.createElement('div');
        message.className = 'small text-muted mb-2';
        if (expectedKind === 'beats' && ['ready', 'rejected'].includes(draft.status)) {
            const regenerate = document.createElement('button');
            regenerate.type = 'button';
            regenerate.className = 'btn btn-outline-warning btn-sm';
            regenerate.textContent = '重新生成本集';
            regenerate.addEventListener('click', () => regenerateOptimizationDraft(draft, regenerate, message));
            header.append(regenerate);
        }
        const summary = document.createElement('div');
        summary.className = 'small text-muted mb-2';
        summary.textContent = '左侧为当前版本，右侧为 AI 优化草稿。';
        const legend = document.createElement('div');
        legend.className = 'd-flex gap-2 small mb-3';
        legend.innerHTML = '<span class="optimization-diff-legend diff-removed">原稿删除</span><span class="optimization-diff-legend diff-added">草稿新增</span>';
        const comparison = document.createElement('div');
        comparison.className = 'row g-3';
        const currentLines = comparisonLines(current?.content, expectedKind);
        const draftLines = comparisonLines(draft.content, expectedKind);
        const diff = computeLineDiff(currentLines, draftLines);
        comparison.append(
            previewColumn('当前版本', current?.content, expectedKind, diff.currentStatus),
            previewColumn('AI 优化草稿', draft.content, expectedKind, diff.draftStatus),
        );
        section.append(header, message, summary, legend, comparison);
        mount(section, before);
        return section;
    }

    async function loadDraft() {
        const response = await aiworkerApi.request(`/api/script-optimization-drafts/${encodeURIComponent(draftId)}`);
        const draft = await response.json();
        if (!response.ok || draft.kind !== expectedKind) return;
        const optimization = await linkBackToReview(draft).catch(() => null);
        const current = await loadCurrentEpisode(pageContext()).catch(() => null);
        const workspace = expectedKind === 'script' ? await waitForScriptWorkspace() : null;
        workspace?.classList.add('d-none');
        const comparison = renderDraftComparison(draft, current, workspace);
        renderDraftNavigation(draft, optimization, comparison);
        if (expectedKind === 'script' && draft.status === 'ready') renderScriptEditor(draft, workspace);
    }

    async function loadDraftEntries() {
        const context = pageContext();
        if (!context) return;
        const response = await aiworkerApi.request(
            `/api/creative/projects/${context.projectId}/episodes/${context.episodeNumber}/optimization-drafts?stage=${context.stage}`,
        );
        const payload = await response.json();
        if (!response.ok || !(payload.items || []).length) return;
        const section = document.createElement('section');
        section.className = 'border-top border-bottom py-3 mb-3';
        const heading = document.createElement('h5');
        heading.className = 'mb-2';
        heading.textContent = 'AI 优化草稿';
        const entries = document.createElement('div');
        entries.className = 'd-flex flex-wrap gap-2';
        payload.items.forEach((draft) => {
            const link = document.createElement('a');
            link.className = 'btn btn-outline-light btn-sm';
            link.href = `${window.location.pathname}?optimization_draft=${draft.id}`;
            link.textContent = draftLabel(draft);
            entries.append(link);
        });
        section.append(heading, entries);
        mount(section);
    }

    hideOriginalScriptWorkspace();
    showReviewBackLink(reviewId || null);
    (draftId ? loadDraft() : loadDraftEntries()).catch(() => {});
}());
