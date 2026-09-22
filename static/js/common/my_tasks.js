(function () {
    'use strict';

    const projectList = document.getElementById('my-project-list');
    const taskSummary = document.getElementById('task-summary');
    const taskSearch = document.getElementById('task-search');
    const taskStatusFilter = document.getElementById('task-status-filter');
    const errorBox = document.getElementById('my-tasks-error');
    const refreshBtn = document.getElementById('refresh-my-tasks');
    const editForm = document.getElementById('project-edit-form');
    const editModalElement = document.getElementById('project-edit-modal');
    const deleteModalElement = document.getElementById('project-delete-modal');
    const editModal = editModalElement ? bootstrap.Modal.getOrCreateInstance(editModalElement) : null;
    const deleteModal = deleteModalElement ? bootstrap.Modal.getOrCreateInstance(deleteModalElement) : null;
    const editFields = {
        id: document.getElementById('project-edit-id'),
        name: document.getElementById('project-edit-name'),
        genre: document.getElementById('project-edit-genre'),
        platform: document.getElementById('project-edit-platform'),
        audience: document.getElementById('project-edit-audience'),
        episodeCount: document.getElementById('project-edit-episode-count'),
        duration: document.getElementById('project-edit-duration'),
        submit: document.getElementById('project-edit-submit'),
    };
    const deleteFields = {
        id: document.getElementById('project-delete-id'),
        name: document.getElementById('project-delete-name'),
        submit: document.getElementById('project-delete-submit'),
    };
    if (!projectList || !errorBox) return;

    let allProjects = [];

    const projectStatusLabels = {
        created: '待生成方向',
        direction_review: '方向生成中',
        brief_locked: 'Brief 已锁定',
        characters_draft: '人物关系草稿',
        characters_locked: '人物关系已锁定',
        outline_draft: '大纲草稿',
        outline_locked: '大纲已锁定',
        beats_in_progress: 'Beats 制作中',
        archived: '已归档',
    };

    function projectNextAction(project) {
        const actions = {
            created: {label: '完善创作方向', stage: 'action'},
            direction_review: {label: '查看生成进度', stage: 'running'},
            brief_locked: {label: '推进人物关系', stage: 'action'},
            characters_draft: {label: '继续编辑人物', stage: 'action'},
            characters_locked: {label: '推进分集大纲', stage: 'action'},
            outline_draft: {label: '继续编辑大纲', stage: 'action'},
            outline_locked: {label: '推进单集 Beats', stage: 'action'},
            beats_in_progress: {label: '继续编辑 Beats', stage: 'running'},
            archived: {label: '查看归档项目', stage: 'done'},
        };
        return actions[project.status] || {label: '打开项目', stage: 'action'};
    }

    function showError(message) {
        errorBox.textContent = message || '';
        errorBox.classList.toggle('d-none', !message);
    }

    function createSkeleton() {
        const skeleton = document.createElement('div');
        skeleton.id = 'my-tasks-skeleton';
        skeleton.className = 'task-card task-card-skeleton';
        skeleton.setAttribute('aria-hidden', 'true');
        const title = document.createElement('div');
        title.className = 'ui-skeleton task-skeleton-title';
        const line = document.createElement('div');
        line.className = 'ui-skeleton task-skeleton-line';
        const shortLine = document.createElement('div');
        shortLine.className = 'ui-skeleton task-skeleton-line task-skeleton-line-short';
        skeleton.append(title, line, shortLine);
        return skeleton;
    }

    function setLoading(visible) {
        projectList.setAttribute('aria-busy', String(visible));
        if (visible) projectList.replaceChildren(createSkeleton());
    }

    function formatDate(value) {
        if (!value) return '-';
        const date = new Date(typeof value === 'number' ? value * 1000 : value);
        if (Number.isNaN(date.getTime())) return '-';
        return date.toLocaleString('zh-CN', {
            year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit',
        });
    }

    function createIcon(icon) {
        const element = document.createElement('i');
        element.className = 'bi ' + icon;
        element.setAttribute('aria-hidden', 'true');
        return element;
    }

    function iconButton(icon, label, className, onClick, text) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn btn-sm ' + className;
        button.title = label;
        button.setAttribute('aria-label', label);
        button.append(createIcon(icon));
        if (text) {
            const textNode = document.createElement('span');
            textNode.className = 'ms-1';
            textNode.textContent = text;
            button.append(textNode);
        }
        button.addEventListener('click', onClick);
        return button;
    }

    function projectHref(projectId) {
        const encodedId = encodeURIComponent(String(projectId));
        return window.location.pathname.startsWith('/writing/')
            ? '/writing/projects/' + encodedId + '/outlines'
            : '/planning/projects/' + encodedId + '/brief';
    }

    function projectSource(project) {
        return project.topic_title || project.source_title || '未关联来源选题';
    }

    function addDetail(detailList, label, value) {
        const item = document.createElement('div');
        item.className = 'task-card-detail';
        const term = document.createElement('dt');
        term.textContent = label;
        const description = document.createElement('dd');
        description.textContent = value || '-';
        item.append(term, description);
        detailList.append(item);
    }

    function createEmptyState(hasProjects) {
        const state = document.createElement('div');
        state.className = 'task-empty-state';
        state.setAttribute('role', 'status');
        const title = document.createElement('h4');
        title.className = 'h6 mb-2';
        title.textContent = hasProjects ? '没有匹配的任务' : '还没有分配的项目';
        const message = document.createElement('p');
        message.className = 'text-muted mb-3';
        message.textContent = hasProjects
            ? '试试调整关键词或状态筛选。'
            : '新的项目任务会出现在这里。';
        state.append(title, message);
        if (hasProjects) {
            const clearButton = document.createElement('button');
            clearButton.type = 'button';
            clearButton.className = 'btn btn-outline-primary btn-sm';
            clearButton.textContent = '清除筛选';
            clearButton.addEventListener('click', clearFilters);
            state.append(clearButton);
        }
        return state;
    }

    function createProjectCard(project) {
        const action = projectNextAction(project);
        const card = document.createElement('article');
        card.className = 'task-card';
        card.setAttribute('data-task-card', '');
        card.setAttribute('data-task-status', action.stage);

        const header = document.createElement('div');
        header.className = 'task-card-header';
        const title = document.createElement('h4');
        title.className = 'task-card-title';
        title.textContent = project.name || '未命名项目';
        const status = document.createElement('span');
        status.className = 'task-status task-status-' + action.stage;
        status.textContent = projectStatusLabels[project.status] || project.status || '待处理';
        header.append(title, status);

        const details = document.createElement('dl');
        details.className = 'task-card-details';
        addDetail(details, '来源选题', projectSource(project));
        addDetail(details, '当前阶段', projectStatusLabels[project.status] || project.status || '待处理');
        addDetail(details, '更新时间', formatDate(project.updated_at));

        const footer = document.createElement('div');
        footer.className = 'task-card-footer';
        const nextAction = document.createElement('a');
        nextAction.className = 'btn btn-primary task-next-action';
        nextAction.href = projectHref(project.id);
        nextAction.setAttribute('aria-label', action.label + '：' + (project.name || '未命名项目'));
        nextAction.append(createIcon('bi-arrow-right'));
        const actionText = document.createElement('span');
        actionText.className = 'ms-1';
        actionText.textContent = action.label;
        nextAction.append(actionText);
        footer.append(nextAction);

        const secondary = document.createElement('details');
        secondary.className = 'task-card-secondary';
        const secondarySummary = document.createElement('summary');
        secondarySummary.textContent = '更多操作';
        const secondaryActions = document.createElement('div');
        secondaryActions.className = 'task-card-secondary-actions';
        secondaryActions.append(
            iconButton('bi-pencil', '编辑项目参数', 'btn-outline-secondary', function () { openEdit(project); }, '编辑'),
            iconButton('bi-trash', '删除项目', 'btn-outline-danger', function () { openDelete(project); }, '删除'),
        );
        secondary.append(secondarySummary, secondaryActions);
        footer.append(secondary);

        card.append(header, details, footer);
        return card;
    }

    function renderSummary(projects) {
        const counts = {action: 0, running: 0, done: 0};
        projects.forEach(function (project) {
            counts[projectNextAction(project).stage] += 1;
        });
        ['action', 'running', 'done'].forEach(function (stage) {
            const element = document.getElementById('task-summary-' + stage);
            if (element) element.textContent = String(counts[stage]);
        });
        if (taskSummary) taskSummary.setAttribute('data-project-count', String(projects.length));
    }

    function renderProjects(projects) {
        const cards = projects.length
            ? projects.map(createProjectCard)
            : [createEmptyState(allProjects.length > 0)];
        projectList.replaceChildren(...cards);
    }

    function applyProjectFilters() {
        const query = (taskSearch?.value || '').trim().toLocaleLowerCase('zh-CN');
        const status = taskStatusFilter?.value || 'all';
        const filtered = allProjects.filter(function (project) {
            const searchable = [project.name, projectSource(project)].filter(Boolean).join(' ').toLocaleLowerCase('zh-CN');
            return (!query || searchable.includes(query))
                && (status === 'all' || projectNextAction(project).stage === status);
        });
        renderProjects(filtered);
    }

    function clearFilters() {
        if (taskSearch) taskSearch.value = '';
        if (taskStatusFilter) taskStatusFilter.value = 'all';
        applyProjectFilters();
        taskSearch?.focus();
    }

    function openEdit(project) {
        if (!editModal || !project) return;
        editFields.id.value = String(project.id);
        editFields.name.textContent = project.name || '未命名项目';
        editFields.genre.value = project.genre || '';
        editFields.platform.value = project.target_platform || '';
        editFields.audience.value = project.target_audience || '';
        editFields.episodeCount.value = String(project.target_episode_count || 1);
        editFields.duration.value = String(project.episode_duration_seconds || 120);
        editModal.show();
    }

    function openDelete(project) {
        if (!deleteModal || !project) return;
        deleteFields.id.value = String(project.id);
        deleteFields.name.textContent = project.name || '未命名项目';
        deleteModal.show();
    }

    async function load() {
        showError();
        setLoading(true);
        try {
            const response = await aiworkerApi.request('/api/creative/projects');
            if (!response.ok) {
                const errPayload = await response.json().catch(function () { return {}; });
                throw new Error(errPayload.detail || '读取项目失败');
            }
            const data = await response.json();
            allProjects = Array.isArray(data.items) ? data.items : [];
            renderSummary(allProjects);
            applyProjectFilters();
        } catch (reason) {
            allProjects = [];
            renderSummary(allProjects);
            showError(reason.message || '读取项目失败');
            renderProjects([]);
        } finally {
            setLoading(false);
        }
    }

    refreshBtn?.addEventListener('click', load);
    taskSearch?.addEventListener('input', applyProjectFilters);
    taskStatusFilter?.addEventListener('change', applyProjectFilters);
    editForm?.addEventListener('submit', async function (event) {
        event.preventDefault();
        const projectId = editFields.id.value;
        if (!projectId) return;
        showError();
        const submitBtn = editFields.submit;
        if (window.aiworkerUi) window.aiworkerUi.setButtonLoading(submitBtn, {loading: true, label: '保存中…'});
        try {
            const response = await aiworkerApi.request('/api/creative/projects/' + projectId, {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    genre: editFields.genre.value.trim(),
                    target_platform: editFields.platform.value.trim(),
                    target_audience: editFields.audience.value.trim(),
                    target_episode_count: Number(editFields.episodeCount.value),
                    episode_duration_seconds: Number(editFields.duration.value),
                }),
            });
            if (!response.ok) {
                var detail = await response.json().catch(function () { return {}; });
                throw new Error(detail.detail || detail.message || '保存项目失败');
            }
            editModal?.hide();
            if (window.aiworkerUi) {
                window.aiworkerUi.invalidateCache('creative:project:');
                window.aiworkerUi.showToast({kind: 'success', title: '保存成功', message: '项目参数已更新。'});
            }
            await load();
        } catch (reason) {
            showError(reason.message || '保存项目失败');
            if (window.aiworkerUi) window.aiworkerUi.showToast({kind: 'danger', title: '保存失败', message: reason.message || '保存项目失败'});
        } finally {
            if (window.aiworkerUi) window.aiworkerUi.setButtonLoading(submitBtn, {loading: false});
        }
    });
    deleteFields.submit?.addEventListener('click', async function () {
        const projectId = deleteFields.id.value;
        if (!projectId) return;
        showError();
        const submitBtn = deleteFields.submit;
        if (window.aiworkerUi) window.aiworkerUi.setButtonLoading(submitBtn, {loading: true, label: '删除中…'});
        try {
            const response = await aiworkerApi.request('/api/creative/projects/' + projectId, {
                method: 'DELETE',
            });
            if (!response.ok) {
                var detail = await response.json().catch(function () { return {}; });
                throw new Error(detail.detail || detail.message || '删除项目失败');
            }
            deleteModal?.hide();
            if (window.aiworkerUi) {
                window.aiworkerUi.invalidateCache('creative:project:');
                window.aiworkerUi.showToast({kind: 'success', title: '删除成功', message: '项目已删除。'});
            }
            await load();
        } catch (reason) {
            showError(reason.message || '删除项目失败');
            if (window.aiworkerUi) window.aiworkerUi.showToast({kind: 'danger', title: '删除失败', message: reason.message || '删除项目失败'});
        } finally {
            if (window.aiworkerUi) window.aiworkerUi.setButtonLoading(submitBtn, {loading: false});
        }
    });
    load();
})();
