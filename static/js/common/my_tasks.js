(function () {
    'use strict';

    const projectList = document.getElementById('my-project-list');
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

    function showError(message) {
        errorBox.textContent = message || '';
        errorBox.classList.toggle('d-none', !message);
    }

    function formatDate(value) {
        if (!value) return '-';
        const date = new Date(value * 1000);
        if (Number.isNaN(date.getTime())) return '-';
        return date.toLocaleString('zh-CN', {
            year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit',
        });
    }

    function textCell(value, className) {
        const cell = document.createElement('td');
        cell.className = className || '';
        cell.textContent = value || '-';
        return cell;
    }

    async function responseError(response, fallback) {
        const payload = await response.json().catch(() => ({}));
        return payload.detail || payload.message || fallback;
    }

    function iconButton(icon, label, className, onClick) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `btn btn-sm ${className}`;
        button.title = label;
        button.setAttribute('aria-label', label);
        button.innerHTML = `<i class="bi ${icon}" aria-hidden="true"></i>`;
        button.addEventListener('click', onClick);
        return button;
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

    function renderProjects(projects) {
        const countEl = document.getElementById('project-count');
        if (countEl) countEl.textContent = String(projects.length);
        const projectHref = (projectId) => (
            window.location.pathname.startsWith('/writing/')
                ? `/writing/projects/${projectId}/outlines`
                : `/planning/projects/${projectId}/brief`
        );
        const rows = projects.length ? projects.map((project) => {
            const row = document.createElement('tr');
            const actions = document.createElement('td');
            actions.className = 'text-end';
            const actionGroup = document.createElement('div');
            actionGroup.className = 'btn-group';
            const link = document.createElement('a');
            link.className = 'btn btn-sm btn-outline-info';
            link.href = projectHref(project.id);
            link.title = '查看项目';
            link.setAttribute('aria-label', '查看项目');
            link.innerHTML = '<i class="bi bi-folder2-open" aria-hidden="true"></i>';
            actionGroup.append(
                link,
                iconButton('bi-pencil', '编辑项目参数', 'btn-outline-secondary', () => openEdit(project)),
                iconButton('bi-trash', '删除项目', 'btn-outline-danger', () => openDelete(project)),
            );
            actions.appendChild(actionGroup);
            row.append(
                textCell(project.name, 'fw-semibold'),
                textCell(project.topic_title),
                textCell(projectStatusLabels[project.status] || project.status),
                textCell(formatDate(project.updated_at)),
                actions,
            );
            return row;
        }) : [(() => {
            const row = document.createElement('tr');
            const cell = document.createElement('td');
            cell.colSpan = 5;
            cell.className = 'text-center text-muted py-4';
            cell.textContent = '暂无被分配的项目';
            row.appendChild(cell);
            return row;
        })()];
        projectList.replaceChildren(...rows);
    }

    async function load() {
        showError();
        try {
            const response = await aiworkerApi.request('/api/creative/projects');
            if (!response.ok) {
                throw new Error(await response.json().then((payload) => payload.detail || '读取项目失败'));
            }
            const payload = await response.json();
            renderProjects(Array.isArray(payload.items) ? payload.items : []);
        } catch (reason) {
            showError(reason.message || '读取项目失败');
            renderProjects([]);
        }
    }

    refreshBtn?.addEventListener('click', load);
    editForm?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const projectId = editFields.id.value;
        if (!projectId) return;
        showError();
        editFields.submit.disabled = true;
        try {
            const response = await aiworkerApi.request(`/api/creative/projects/${projectId}`, {
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
            if (!response.ok) throw new Error(await responseError(response, '保存项目失败'));
            editModal?.hide();
            await load();
        } catch (reason) {
            showError(reason.message || '保存项目失败');
        } finally {
            editFields.submit.disabled = false;
        }
    });
    deleteFields.submit?.addEventListener('click', async () => {
        const projectId = deleteFields.id.value;
        if (!projectId) return;
        showError();
        deleteFields.submit.disabled = true;
        try {
            const response = await aiworkerApi.request(`/api/creative/projects/${projectId}`, {
                method: 'DELETE',
            });
            if (!response.ok) throw new Error(await responseError(response, '删除项目失败'));
            deleteModal?.hide();
            await load();
        } catch (reason) {
            showError(reason.message || '删除项目失败');
        } finally {
            deleteFields.submit.disabled = false;
        }
    });
    load();
})();
