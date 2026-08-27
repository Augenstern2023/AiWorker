(function () {
    'use strict';

    const writerActionPollers = new Map();
    let mountedWriterSession = null;
    let writerWorkbenchCallbacks = {};

    async function writerApiJson(result) {
        if (result && typeof result.json === 'function') {
            const payload = await result.json().catch(() => ({}));
            if (result.ok === false) throw new Error(payload.detail?.message || payload.detail || '请求失败');
            return payload;
        }
        return result || {};
    }

    function writerRequestId() {
        return window.crypto?.randomUUID?.() || `writer-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    }

    function writerActionHost() {
        return document.getElementById('writer-agent-conversation') || document.getElementById('writer-agent-messages');
    }

    function stopWriterActionPolling(actionId) {
        const timer = writerActionPollers.get(String(actionId));
        if (timer !== undefined) window.clearInterval(timer);
        writerActionPollers.delete(String(actionId));
    }

    function replaceMountedAction(updated) {
        if (!mountedWriterSession || !updated?.id) return;
        const actions = mountedWriterSession.actions || (mountedWriterSession.actions = []);
        const index = actions.findIndex((item) => String(item.id) === String(updated.id));
        const previousStatus = index >= 0 ? actions[index]?.status : '';
        if (index >= 0) actions[index] = Object.assign({}, actions[index], updated);
        else actions.push(updated);
        if (updated.status === 'succeeded' && previousStatus !== 'succeeded') {
            const projectId = Number(updated.project_id || updated.result?.project_id || mountedWriterSession.project_id || 0);
            if (Number.isInteger(projectId) && projectId > 0) mountedWriterSession.project_id = projectId;
            delete mountedWriterSession.project;
            delete mountedWriterSession.__projectLoadError;
        }
        if (typeof writerWorkbenchCallbacks.onSession === 'function') {
            writerWorkbenchCallbacks.onSession(mountedWriterSession);
        } else {
            window.writerAgentWorkbench.mount(mountedWriterSession);
        }
    }

    async function refreshWriterAction(actionId) {
        if (!mountedWriterSession) return;
        try {
            const updated = await writerApiJson(await window.aiworkerApi.request(
                `/api/writer-agent/sessions/${mountedWriterSession.id}/actions/${actionId}`
            ));
            replaceMountedAction(updated);
            if (!['queued', 'running'].includes(updated.status)) stopWriterActionPolling(actionId);
        } catch (error) {
            stopWriterActionPolling(actionId);
            writerWorkbenchCallbacks.showError?.(error.message);
        }
    }

    function startWriterActionPolling(actionId) {
        const key = String(actionId);
        if (writerActionPollers.has(key)) return;
        writerActionPollers.set(key, window.setInterval(() => refreshWriterAction(key), 1000));
    }

    function safeWriterLocalUrl(value) {
        const raw = String(value || '');
        try {
            const origin = window.location.origin === 'null'
                ? 'http://writer-agent.local'
                : window.location.origin;
            const parsed = new URL(raw, origin);
            if (
                raw.startsWith('/')
                && !raw.startsWith('//')
                && !raw.includes('\\')
                && parsed.origin === origin
            ) return `${parsed.pathname}${parsed.search}${parsed.hash}`;
        } catch (_error) {
            return '';
        }
        return '';
    }

    async function decideWriterAction(action, decision, card) {
        const buttons = card.querySelectorAll('button');
        buttons.forEach((button) => { button.disabled = true; });
        let updated = null;
        try {
            updated = await writerApiJson(await window.aiworkerApi.request(
                `/api/writer-agent/sessions/${mountedWriterSession.id}/actions/${action.id}/decision`,
                {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        decision,
                        expected_revision: Number(action.revision),
                        client_request_id: writerRequestId(),
                    }),
                }
            ));
            replaceMountedAction(Object.assign({}, action, updated));
            if (['queued', 'running'].includes(updated.status)) startWriterActionPolling(action.id);
        } catch (error) {
            writerWorkbenchCallbacks.showError?.(error.message);
        } finally {
            if (!updated || updated.status === 'pending') buttons.forEach((button) => { button.disabled = false; });
        }
    }

    async function retryWriterAction(action, button) {
        button.disabled = true;
        try {
            const retry = await writerApiJson(await window.aiworkerApi.request(
                `/api/writer-agent/sessions/${mountedWriterSession.id}/actions/${action.id}/retry`,
                {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({client_request_id: writerRequestId()}),
                }
            ));
            replaceMountedAction(retry);
            startWriterActionPolling(retry.id);
        } catch (error) {
            button.disabled = false;
            writerWorkbenchCallbacks.showError?.(error.message);
        }
    }

    function writerActionChainKey(action) {
        return String(action?.root_action_id || action?.id || '');
    }

    function writerActionOrder(entry) {
        const action = entry.action || {};
        return [
            Number(action.created_at || 0),
            Number(action.updated_at || 0),
            Number(entry.index || 0),
        ];
    }

    function writerActionChains(actions) {
        const grouped = new Map();
        (actions || []).forEach((action, index) => {
            const key = writerActionChainKey(action);
            if (!grouped.has(key)) grouped.set(key, []);
            grouped.get(key).push({action, index});
        });
        return [...grouped.values()].map((entries) => {
            entries.sort((left, right) => {
                const a = writerActionOrder(left);
                const b = writerActionOrder(right);
                return a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
            });
            return {
                current: entries.at(-1).action,
                history: entries.slice(0, -1).reverse().map((entry) => entry.action),
            };
        });
    }

    function writerActionStatusLabel(action) {
        return ({
            pending: '待确认', queued: '已排队', running: '生成中',
            succeeded: '已完成', failed: '执行失败', cancelled: '已取消',
        })[action?.status] || action?.status || '';
    }

    function renderWriterActionHistory(actions) {
        const details = document.createElement('details');
        details.className = 'writer-action-history border-top mt-3 pt-2';
        const summary = document.createElement('summary');
        summary.className = 'small text-muted';
        summary.textContent = `查看执行历史（${actions.length}）`;
        details.append(summary);
        actions.forEach((action) => {
            const item = document.createElement('section');
            item.className = 'writer-action-history-item small border-top mt-2 pt-2';
            writerCardText(item, 'fw-semibold', action.title || action.action_type || '创作动作');
            const progress = Number(action.total_steps || 0) > 0
                ? ` · ${Number(action.completed_steps || 0)} / ${Number(action.total_steps)}`
                : '';
            const occurredAt = sessionTime(action.updated_at || action.created_at);
            writerCardText(
                item, 'text-muted mt-1',
                `${writerActionStatusLabel(action)}${progress}${occurredAt ? ` · ${occurredAt}` : ''}`
            );
            details.append(item);
        });
        return details;
    }

    function renderWriterActionCard(action, history = []) {
        const card = document.createElement('article');
        card.className = `writer-action-card status-${action.status || 'pending'}`;
        card.dataset.actionId = String(action.id);
        const title = document.createElement('h3');
        title.textContent = action.title || action.action_type || '创作动作';
        const summary = document.createElement('p');
        summary.className = 'mb-2 text-muted';
        summary.textContent = action.summary || '';
        const status = document.createElement('div');
        status.className = 'small writer-action-status';
        status.textContent = writerActionStatusLabel(action);
        card.append(title, summary, status);
        if (Number(action.total_steps || 0) > 0) {
            writerCardText(
                card,
                'small writer-action-progress mt-2',
                `${Number(action.completed_steps || 0)} / ${Number(action.total_steps)}`
            );
        }
        (action.resource_refs || []).forEach((resource) => renderWriterValue(card, resource));
        const actionOpenUrl = safeWriterLocalUrl(action.open_url);
        if (action.status === 'succeeded' && actionOpenUrl) {
            const link = document.createElement('a');
            link.className = 'btn btn-outline-light btn-sm mt-3';
            link.href = actionOpenUrl;
            link.textContent = '打开成果';
            card.append(link);
        }
        if (action.status === 'pending') {
            const confirm = document.createElement('button');
            confirm.type = 'button';
            confirm.className = 'btn btn-primary btn-sm mt-3';
            confirm.textContent = action.action_type === 'project.link' ? '确认关联' : '确认生成';
            confirm.addEventListener('click', () => decideWriterAction(action, 'confirm', card));
            const cancel = document.createElement('button');
            cancel.type = 'button';
            cancel.className = 'btn btn-outline-light btn-sm mt-3 ms-2';
            cancel.textContent = '取消';
            cancel.addEventListener('click', () => decideWriterAction(action, 'cancel', card));
            card.append(confirm, cancel);
        } else if (['queued', 'running'].includes(action.status)) {
            const cancel = document.createElement('button');
            cancel.type = 'button';
            cancel.className = 'btn btn-outline-light btn-sm mt-3';
            cancel.textContent = action.phase === 'cancelling' ? '取消中' : '取消生成';
            cancel.disabled = action.phase === 'cancelling';
            cancel.addEventListener('click', () => decideWriterAction(action, 'cancel', card));
            card.append(cancel);
        }
        if (action.status === 'failed' && action.retryable) {
            const retry = document.createElement('button');
            retry.type = 'button';
            retry.className = 'btn btn-primary btn-sm mt-3';
            retry.textContent = action.result?.partial ? '仅重试失败项' : '重试';
            retry.addEventListener('click', () => retryWriterAction(action, retry));
            card.append(retry);
        }
        if (history.length > 0) card.append(renderWriterActionHistory(history));
        if (['queued', 'running'].includes(action.status)) startWriterActionPolling(action.id);
        return card;
    }

    function writerCardText(parent, className, value) {
        const element = document.createElement('div');
        element.className = className;
        element.textContent = String(value ?? '');
        parent.append(element);
        return element;
    }

    function renderWriterValue(parent, value) {
        if (Array.isArray(value)) {
            value.forEach((item) => renderWriterValue(parent, item));
            return;
        }
        if (value && typeof value === 'object') {
            Object.entries(value).forEach(([key, item]) => {
                const row = document.createElement('div');
                row.className = 'small mt-1';
                const label = document.createElement('strong');
                label.textContent = `${key}：`;
                row.append(label);
                if (item && typeof item === 'object') renderWriterValue(row, item);
                else row.append(document.createTextNode(String(item ?? '')));
                parent.append(row);
            });
            return;
        }
        writerCardText(parent, 'small mt-1', value);
    }

    function renderWriterCreativeCard(cardData) {
        const card = document.createElement('article');
        card.className = `writer-creative-card writer-card-${cardData.card_type || 'unknown'}`;
        card.dataset.cardId = String(cardData.card_id || '');
        const type = cardData.card_type;
        const labels = {
            directions: '原创方向', brief: '原创 Brief', plan: '创作计划',
            progress: '创作进度', result: '创作成果', conflict: '创作冲突',
        };
        writerCardText(card, 'small text-muted mb-2', labels[type] || '创作卡片');
        if (type === 'directions') {
            (cardData.items || []).forEach((item, index) => {
                const direction = document.createElement('section');
                direction.className = 'border-top pt-2 mt-2';
                writerCardText(direction, 'fw-semibold', `${index + 1}. ${item.title || '未命名方向'}`);
                [
                    ['主角', item.protagonist], ['背景', item.background],
                    ['核心冲突', item.core_conflict], ['人物关系', item.relationship_engine],
                    ['情绪回报', item.emotional_payoff],
                ].forEach(([label, value]) => {
                    if (value) writerCardText(direction, 'small mt-1', `${label}：${value}`);
                });
                if (item.difference_dimensions?.length) {
                    writerCardText(direction, 'small text-muted mt-1', `差异维度：${item.difference_dimensions.join('、')}`);
                }
                card.append(direction);
            });
        } else if (type === 'brief') {
            renderWriterValue(card, cardData.content || {});
            if (cardData.assumptions?.length) {
                writerCardText(card, 'small text-muted mt-2', `AI 假设：${cardData.assumptions.join('；')}`);
            }
        } else if (type === 'plan') {
            writerCardText(card, 'fw-semibold', cardData.title || '创作计划');
            if (cardData.summary) writerCardText(card, 'small mt-1', cardData.summary);
            (cardData.steps || []).forEach((step, index) => {
                writerCardText(card, 'small mt-1', `${index + 1}. ${step.title || step.stage || '步骤'} · ${step.kind || ''}`);
            });
        } else if (type === 'conflict') {
            writerCardText(card, 'fw-semibold', cardData.message || cardData.code || '项目状态已变化');
            if (cardData.current_revision != null) {
                writerCardText(card, 'small text-muted mt-1', `当前版本：${cardData.current_revision}`);
            }
            const replan = document.createElement('button');
            replan.type = 'button';
            replan.className = 'btn btn-outline-light btn-sm mt-3';
            replan.textContent = '重新生成计划';
            replan.addEventListener('click', () => writerWorkbenchCallbacks.sendMessage?.('基于当前项目状态重新生成计划'));
            card.append(replan);
        }
        return card;
    }

    function renderMountedCreativeCards(host, session) {
        host.querySelectorAll('.writer-creative-card').forEach((card) => card.remove());
        const modelCardTypes = new Set(['directions', 'brief', 'plan', 'conflict']);
        (session?.messages || []).forEach((message) => {
            if (message.role !== 'assistant') return;
            (message.cards || [])
                .filter((card) => modelCardTypes.has(card?.card_type))
                .forEach((card) => host.append(renderWriterCreativeCard(card)));
        });
    }

    async function showWriterProjectPicker(picker) {
        picker.hidden = false;
        picker.replaceChildren();
        const loading = document.createElement('span');
        loading.className = 'small text-muted';
        loading.textContent = '正在读取可关联项目…';
        picker.append(loading);
        try {
            const payload = await writerApiJson(await window.aiworkerApi.request('/api/creative/projects?participating_only=true'));
            const projects = payload.items || payload.projects || [];
            picker.replaceChildren();
            if (!projects.length) {
                picker.textContent = '暂无可关联项目';
                return;
            }
            projects.forEach((project) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'btn btn-outline-light btn-sm';
                button.textContent = project.name || `项目 #${project.id}`;
                button.addEventListener('click', () => writerWorkbenchCallbacks.sendProjectLink?.(Number(project.id)));
                picker.append(button);
            });
        } catch (error) {
            picker.textContent = error.message;
        }
    }

    function renderWriterProjectContext(session) {
        const context = document.getElementById('writer-agent-project-context');
        if (!context) return;
        context.replaceChildren();
        const project = session?.project || (session?.project_id ? {id: session.project_id, name: `项目 #${session.project_id}`} : null);
        if (project) {
            const content = document.createElement('div');
            content.className = 'writer-project-summary';
            const name = document.createElement('strong');
            name.textContent = project.name || `项目 #${project.id}`;
            const mode = project.creation_mode === 'imitation' ? '仿写' : '原创';
            let stage = project.current_stage || '';
            if (!stage && (project.outline_batch_id || (project.episodes || []).some((item) => item.stage === 'outline'))) stage = 'outline';
            if (!stage && project.character_batch_id) stage = 'characters';
            if (!stage && project.locked_brief_id) stage = 'brief';
            const stageLabels = {brief: 'Brief', characters: '人物关系', outline: '全剧大纲', beats: '分集 Beats', script: '分集剧本'};
            const meta = document.createElement('span');
            meta.className = 'small text-muted ms-2';
            meta.textContent = `${mode}${stage ? ` · 当前阶段：${stageLabels[stage] || stage}` : '创作'}`;
            const link = document.createElement('a');
            link.className = 'btn btn-outline-light btn-sm ms-auto';
            link.href = stage === 'outline'
                ? `/writing/projects/${project.id}/outlines`
                : stage === 'characters'
                    ? `/planning/projects/${project.id}/characters`
                    : `/planning/projects/${project.id}/brief`;
            link.textContent = '打开项目';
            content.append(name, meta, link);
            context.append(content);
        } else {
            const create = document.createElement('button');
            create.id = 'writer-agent-create-project';
            create.type = 'button';
            create.className = 'btn btn-primary btn-sm';
            create.textContent = '创建原创项目';
            create.addEventListener('click', () => writerWorkbenchCallbacks.sendMessage?.('创建原创项目'));
            const link = document.createElement('button');
            link.id = 'writer-agent-link-project';
            link.type = 'button';
            link.className = 'btn btn-outline-light btn-sm';
            link.textContent = '关联已有项目';
            const picker = document.createElement('div');
            picker.id = 'writer-agent-project-picker';
            picker.className = 'writer-project-picker';
            picker.hidden = true;
            link.addEventListener('click', () => showWriterProjectPicker(picker));
            context.append(create, link, picker);
        }
    }

    function writerProjectTarget(project) {
        const value = Number(project?.target_episode_count || 0);
        return Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
    }

    function writerProjectEpisodeNumber(project, episode) {
        const number = Number(episode?.episode_number || 0);
        const total = writerProjectTarget(project);
        return Number.isInteger(number) && number > 0 && (!total || number <= total) ? number : 0;
    }

    function writerProjectResourceIsCurrent(item) {
        return item && !['superseded', 'historical'].includes(item.status);
    }

    function writerLatestProjectResource(items) {
        return [...items].sort((left, right) =>
            Number(right.version || 0) - Number(left.version || 0)
            || Number(right.id || 0) - Number(left.id || 0)
        )[0] || null;
    }

    function writerCurrentProjectBlueprints(project) {
        const latest = new Map();
        (project?.blueprints || []).filter(writerProjectResourceIsCurrent).forEach((item) => {
            const key = String(item.candidate_group_id || item.id || '');
            const prior = latest.get(key);
            if (!prior || writerLatestProjectResource([prior, item]) === item) latest.set(key, item);
        });
        return [...latest.values()];
    }

    function writerCurrentProjectCharacters(project) {
        const items = (project?.characters || []).filter(writerProjectResourceIsCurrent);
        let batchId = String(project?.character_batch_id || '');
        if (!batchId) {
            const latest = writerLatestProjectResource(items.filter((item) =>
                ['draft', 'submitted', 'returned'].includes(item.status)
                && Number(item.source_refs?.brief_id || 0) === Number(project?.locked_brief_id || 0)
            ));
            batchId = String(latest?.batch_id || '');
        }
        return batchId ? items.filter((item) => String(item.batch_id || '') === batchId) : [];
    }

    function writerCurrentProjectOutlines(project) {
        const items = (project?.episodes || []).filter((item) =>
            item?.stage === 'outline'
            && writerProjectResourceIsCurrent(item)
            && writerProjectEpisodeNumber(project, item)
        );
        let batchId = String(project?.outline_batch_id || '');
        if (!batchId && project?.locked_brief_id && project?.character_batch_id) {
            const latest = writerLatestProjectResource(items.filter((item) =>
                ['draft', 'submitted', 'returned'].includes(item.status)
                && Number(item.upstream_refs?.brief_id || 0) === Number(project.locked_brief_id)
                && String(item.upstream_refs?.character_batch_id || '') === String(project.character_batch_id)
            ));
            batchId = String(latest?.batch_id || '');
        }
        if (!batchId) return [];
        const byEpisode = new Map();
        items.filter((item) => String(item.batch_id || '') === batchId).forEach((item) => {
            const number = writerProjectEpisodeNumber(project, item);
            const prior = byEpisode.get(number);
            if (!prior || writerLatestProjectResource([prior, item]) === item) byEpisode.set(number, item);
        });
        return [...byEpisode.values()];
    }

    function writerCurrentProjectBeats(project) {
        const outlineBatchId = String(project?.outline_batch_id || '');
        const byEpisode = new Map();
        if (!outlineBatchId) return byEpisode;
        (project?.episodes || []).forEach((item) => {
            const number = writerProjectEpisodeNumber(project, item);
            if (
                item?.stage !== 'beats'
                || !number
                || !writerProjectResourceIsCurrent(item)
                || String(item.upstream_refs?.outline_batch_id || '') !== outlineBatchId
            ) return;
            const prior = byEpisode.get(number);
            if (!prior || writerLatestProjectResource([prior, item]) === item) byEpisode.set(number, item);
        });
        return byEpisode;
    }

    function writerCurrentProjectScripts(project, beatsByEpisode = writerCurrentProjectBeats(project)) {
        const lockedStatuses = new Set(['locked', 'approved']);
        const lockedBeats = new Map([...beatsByEpisode].filter(([, item]) => lockedStatuses.has(item.status)));
        const byEpisode = new Map();
        (project?.episodes || []).forEach((item) => {
            const number = writerProjectEpisodeNumber(project, item);
            const beats = lockedBeats.get(number);
            if (
                item?.stage !== 'script'
                || !beats
                || !writerProjectResourceIsCurrent(item)
                || Number(item.upstream_refs?.beats_id || 0) !== Number(beats.id)
                || Number(item.upstream_refs?.beats_version || 0) !== Number(beats.version)
            ) return;
            const prior = byEpisode.get(number);
            if (!prior || writerLatestProjectResource([prior, item]) === item) byEpisode.set(number, item);
        });
        return byEpisode;
    }

    function writerProjectMetrics(project) {
        const blueprints = writerCurrentProjectBlueprints(project);
        const characters = writerCurrentProjectCharacters(project);
        const outlines = writerCurrentProjectOutlines(project);
        const beats = writerCurrentProjectBeats(project);
        const scripts = writerCurrentProjectScripts(project, beats);
        const locked = (items) => [...items].filter((item) => ['locked', 'approved'].includes(item.status)).length;
        return {
            blueprints, characters, outlines, beats, scripts,
            outlineLocked: locked(outlines),
            beatsLocked: locked(beats.values()),
            scriptsLocked: locked(scripts.values()),
        };
    }

    function writerProjectStage(project) {
        if (!project) return 'brief';
        const metrics = writerProjectMetrics(project);
        if (metrics.scripts.size) return 'script';
        if (metrics.beats.size) return 'beats';
        if (metrics.outlines.length) return 'outline';
        if (metrics.characters.length) return 'characters';
        if (project.locked_brief_id) return 'brief';
        const stage = String(project.current_stage || '').toLowerCase();
        return ['brief', 'characters', 'outline', 'beats', 'script'].includes(stage) ? stage : 'brief';
    }

    function writerProjectUrl(project, stage = writerProjectStage(project)) {
        const projectId = Number(project?.id);
        if (!Number.isInteger(projectId) || projectId < 1) return '';
        if (stage === 'brief') return `/planning/projects/${projectId}/brief`;
        if (stage === 'characters') return `/planning/projects/${projectId}/characters`;
        if (stage === 'beats' || stage === 'script') {
            const metrics = writerProjectMetrics(project);
            const resources = stage === 'beats' ? metrics.beats : metrics.scripts;
            const episode = Math.min(...resources.keys());
            if (Number.isFinite(episode)) return `/writing/projects/${projectId}/episodes/${episode}/${stage === 'beats' ? 'beats' : 'scripts'}`;
        }
        return `/writing/projects/${projectId}/outlines`;
    }

    function writerProjectUnit(project) {
        const medium = `${project?.target_platform || ''} ${project?.genre || ''}`;
        return medium.includes('小说') ? '章' : '集';
    }

    function writerProjectDuration(seconds) {
        const value = Number(seconds || 0);
        if (!value) return '';
        if (value < 60) return `${value} 秒`;
        const minutes = Math.round((value / 60) * 10) / 10;
        return `${minutes} 分钟`;
    }

    function renderWriterProjectProgressState(host, message, retry) {
        host.replaceChildren();
        const state = document.createElement('div');
        state.className = 'writer-project-progress-state';
        state.setAttribute('role', 'status');
        const heading = document.createElement('h5');
        heading.className = 'mb-2 text-body';
        heading.textContent = '项目进度';
        const copy = document.createElement('span');
        copy.className = 'small';
        copy.textContent = message;
        state.append(heading, copy);
        if (retry) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn btn-outline-light btn-sm mt-3 align-self-start';
            button.textContent = '重试读取';
            button.addEventListener('click', retry);
            state.append(button);
        }
        host.append(state);
    }

    function renderWriterProjectProgress(session) {
        const host = document.getElementById('writer-agent-project-progress');
        if (!host) return;
        if (session?.__projectLoading) {
            renderWriterProjectProgressState(host, '正在读取项目进度…');
            return;
        }
        if (session?.__projectLoadError) {
            renderWriterProjectProgressState(
                host,
                `项目进度读取失败：${session.__projectLoadError}`,
                () => hydrateWriterProject(session, true)
            );
            return;
        }
        const project = session?.project;
        if (!project) {
            renderWriterProjectProgressState(
                host,
                session?.project_id
                    ? '正在读取项目进度…'
                    : '关联或创建项目后，这里会显示创作阶段与分章进度。'
            );
            return;
        }

        const total = writerProjectTarget(project);
        const unit = writerProjectUnit(project);
        const currentStage = writerProjectStage(project);
        const metrics = writerProjectMetrics(project);
        const blueprintCount = metrics.blueprints.length;
        const outlineCount = metrics.outlines.length;
        const beatsCount = metrics.beats.size;
        const scriptCount = metrics.scripts.size;
        const briefComplete = Boolean(project.locked_brief_id);
        const charactersComplete = Boolean(project.character_batch_id);
        const outlineComplete = Boolean(project.outline_batch_id && total && metrics.outlineLocked >= total);
        const beatsComplete = Boolean(total && metrics.beatsLocked >= total);
        const scriptsComplete = Boolean(total && metrics.scriptsLocked >= total);
        const countLabel = (count) => total ? `${count} / ${total} ${unit}` : `${count} ${unit}`;
        const stages = [
            {
                key: 'brief', label: '方向 / Brief', complete: briefComplete,
                value: briefComplete ? 'Brief 已锁定' : blueprintCount ? `${blueprintCount} 个方向 · 待锁定` : '待生成方向',
            },
            {
                key: 'characters', label: '人物关系', complete: charactersComplete,
                value: charactersComplete ? '已锁定' : metrics.characters.length ? `${metrics.characters.length} 个人物 · 待锁定` : '待生成',
            },
            {key: 'outline', label: '全剧大纲', complete: outlineComplete, value: countLabel(outlineCount)},
            {key: 'beats', label: `分${unit} Beats`, complete: beatsComplete, value: countLabel(beatsCount)},
            {key: 'script', label: '正文', complete: scriptsComplete, value: countLabel(scriptCount)},
        ];
        const ratios = [
            briefComplete ? 1 : 0,
            charactersComplete ? 1 : 0,
            outlineComplete ? 1 : 0,
            total ? Math.min(metrics.beatsLocked / total, 1) : 0,
            total ? Math.min(metrics.scriptsLocked / total, 1) : 0,
        ];
        const percent = Math.round((ratios.reduce((sum, value) => sum + value, 0) / ratios.length) * 100);

        host.replaceChildren();
        const headingRow = document.createElement('div');
        headingRow.className = 'd-flex align-items-start justify-content-between gap-2';
        const heading = document.createElement('h5');
        heading.className = 'mb-0';
        heading.textContent = '项目进度';
        const mode = document.createElement('span');
        mode.className = 'small text-muted';
        mode.textContent = project.creation_mode === 'imitation' ? '仿写' : '原创';
        headingRow.append(heading, mode);
        const name = document.createElement('div');
        name.className = 'writer-project-progress-name fw-semibold mt-3';
        name.textContent = project.name || `项目 #${project.id}`;
        const meta = document.createElement('div');
        meta.className = 'writer-project-progress-meta mt-2';
        if (total) writerCardText(meta, 'writer-project-progress-chip', `目标 ${total} ${unit}`);
        const duration = writerProjectDuration(project.episode_duration_seconds);
        if (duration) writerCardText(meta, 'writer-project-progress-chip', `单${unit} ${duration}`);
        const overall = document.createElement('div');
        overall.className = 'writer-project-progress-overall mt-3';
        overall.setAttribute('role', 'progressbar');
        overall.setAttribute('aria-label', '项目已锁定成果进度');
        overall.setAttribute('aria-valuemin', '0');
        overall.setAttribute('aria-valuemax', '100');
        overall.setAttribute('aria-valuenow', String(percent));
        const fill = document.createElement('span');
        fill.style.width = `${percent}%`;
        overall.append(fill);
        const list = document.createElement('div');
        list.className = 'writer-project-stage-list';
        stages.forEach((stage) => {
            const item = document.createElement('div');
            item.className = `writer-project-stage${stage.complete ? ' is-complete' : ''}${stage.key === currentStage ? ' is-current' : ''}`;
            const row = document.createElement('div');
            row.className = 'writer-project-stage-row';
            const label = document.createElement('span');
            label.className = stage.key === currentStage ? 'fw-semibold' : '';
            label.textContent = stage.label;
            const value = document.createElement('span');
            value.className = 'writer-project-stage-value';
            value.textContent = stage.value;
            row.append(label, value);
            item.append(row);
            list.append(item);
        });
        const link = document.createElement('a');
        link.className = 'btn btn-outline-light btn-sm w-100 mt-3';
        link.href = writerProjectUrl(project, currentStage);
        link.textContent = '打开当前阶段';
        host.append(headingRow, name, meta, overall, list, link);
    }

    async function hydrateWriterProject(session, force = false) {
        if (!session?.project_id || session.__projectLoading || (session.project && !force)) return;
        if (session.__projectLoadError && !force) return;
        delete session.__projectLoadError;
        session.__projectLoading = true;
        renderWriterProjectProgress(session);
        try {
            const project = await writerApiJson(await window.aiworkerApi.request(`/api/creative/projects/${session.project_id}`));
            session.project = project;
            if (typeof writerWorkbenchCallbacks.onSession === 'function') writerWorkbenchCallbacks.onSession(session);
            else window.writerAgentWorkbench.mount(session);
        } catch (error) {
            session.__projectLoadError = error?.message || '请求失败';
        } finally {
            delete session.__projectLoading;
            renderWriterProjectProgress(session);
        }
    }

    window.writerAgentWorkbench = {
        configure(callbacks = {}) { writerWorkbenchCallbacks = Object.assign({}, writerWorkbenchCallbacks, callbacks); },
        mount(session) {
            mountedWriterSession = session || {id: null, actions: []};
            renderWriterProjectContext(mountedWriterSession);
            renderWriterProjectProgress(mountedWriterSession);
            hydrateWriterProject(mountedWriterSession);
            const host = writerActionHost();
            if (!host) return;
            renderMountedCreativeCards(host, mountedWriterSession);
            host.querySelectorAll('.writer-action-card').forEach((card) => card.remove());
            writerActionChains(mountedWriterSession.actions || []).forEach(
                ({current, history}) => host.append(
                    renderWriterActionCard(current, history)
                )
            );
        },
        stop(actionId) { stopWriterActionPolling(actionId); },
    };

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
    let thinkingStartedAt = 0;
    let mountedProjectReviewSessionId = null;
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
        reportsToggleButton.title = isHidden ? '展开项目面板' : '隐藏项目面板';
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
        const slowResponse = thinkingStartedAt > 0
            && Date.now() - thinkingStartedAt >= 60_000;
        label.textContent = slowResponse
            ? '模型响应较慢，仍在处理；超时后可直接重试'
            : thinkingStages[thinkingStageIndex];
        const dots = document.createElement('span');
        dots.className = 'writer-agent-thinking-dots';
        line.append(spinner, label, dots);
        article.append(line);
        return article;
    }

    function beginThinking(message = '', showPendingMessage = true) {
        pendingMessage = showPendingMessage ? message : '';
        thinkingStageIndex = 0;
        thinkingStartedAt = Date.now();
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
        thinkingStartedAt = 0;
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
            if (['project_review_offer', 'project_review'].includes(message.kind)
                && window.writerProjectReviewUI?.renderMessage(article, message, {session: activeSession, showError})) {
                conversation.append(article); return;
            }
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
        const nextProjectReviewSessionId = activeSession?.id ?? null;
        if (mountedProjectReviewSessionId !== null && Number(mountedProjectReviewSessionId) !== Number(nextProjectReviewSessionId)) {
            window.writerProjectReviewUI?.disposeSession(mountedProjectReviewSessionId);
        }
        mountedProjectReviewSessionId = nextProjectReviewSessionId;
        detailElement.classList.toggle('d-none', !hasSession);
        if (hasSession) { titleElement.textContent = activeSession.title || '未命名创作会话'; materialNameElement.textContent = activeSession.filename ? `来源文件：${activeSession.filename}` : '来源：直接输入'; }
        renderSessions(); renderConversation(); renderReportFiles(); setBusy(isBusy);
        window.writerAgentWorkbench.configure({sendMessage: sendSuggestedMessage, sendProjectLink: sendSuggestedProjectLink, onSession: updateSession, showError});
        window.writerAgentWorkbench.mount(activeSession);
        window.writerProjectReviewUI?.mountSession(activeSession, {showError});
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

    function isBootstrapShortcutSession(session, message) {
        const messages = session?.messages;
        return Boolean(
            session?.id
            && String(session.content || '').trim() === String(message || '').trim()
            && Array.isArray(messages)
            && messages.length > 0
            && messages.every((item) => item?.kind === 'material')
        );
    }

    function bootstrapMessageId(sessionId, projectId = null) {
        const suffix = projectId == null ? '' : `-project-${Number(projectId)}`;
        return `writer-bootstrap-${sessionId}${suffix}`;
    }

    async function ask(message, showPendingMessage = true, displayUserMessage = true, messageId = null) {
        beginThinking(message, showPendingMessage);
        try {
            const updated = await readJson(await aiworkerApi.request(`/api/writer-agent/sessions/${activeSession.id}/messages`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message, display_user_message: displayUserMessage, message_id: messageId || writerRequestId()})}));
            endThinking(false);
            updateSession(updated);
        } catch (error) {
            endThinking();
            throw error;
        }
    }

    async function linkProject(projectId, displayUserMessage = true, showPendingMessage = true, messageId = null) {
        beginThinking('关联已有项目', showPendingMessage);
        try {
            const updated = await readJson(await aiworkerApi.request(
                `/api/writer-agent/sessions/${activeSession.id}/messages`,
                {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        message: '关联已有项目',
                        display_user_message: displayUserMessage,
                        link_project_id: Number(projectId),
                        message_id: messageId || writerRequestId(),
                    }),
                }
            ));
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

    async function sendSuggestedMessage(message) {
        const content = String(message || '').trim();
        if (!content || isBusy) return;
        try {
            showError(); setBusy(true);
            if (!activeSession) await createInitialSession(null, content);
            const bootstrap = isBootstrapShortcutSession(activeSession, content);
            await ask(content, !bootstrap, !bootstrap, bootstrap ? bootstrapMessageId(activeSession.id) : null);
        } catch (error) {
            showError(error.message);
        } finally {
            setBusy(false);
        }
    }

    async function sendSuggestedProjectLink(projectId) {
        if (isBusy) return;
        try {
            showError(); setBusy(true);
            const content = '关联已有项目';
            if (!activeSession) await createInitialSession(null, content);
            const bootstrap = isBootstrapShortcutSession(activeSession, content);
            await linkProject(projectId, !bootstrap, !bootstrap, bootstrap ? bootstrapMessageId(activeSession.id, projectId) : null);
        } catch (error) {
            showError(error.message);
        } finally {
            setBusy(false);
        }
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
