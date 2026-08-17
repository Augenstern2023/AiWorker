(function () {
    'use strict';

    const root = document.getElementById('managed-projects');
    const body = document.getElementById('managed-project-list');
    const error = document.getElementById('managed-project-error');
    if (!root || !body || !error) return;

    const projectStatusLabels = {
        created: '待生成方向',
        direction_review: '方向评审',
        brief_locked: 'Brief 已锁定',
        characters_draft: '人物关系草稿',
        characters_locked: '人物关系已锁定',
        outline_draft: '大纲草稿',
        outline_locked: '大纲已锁定',
        beats_in_progress: 'Beats 进行中',
        archived: '已归档',
    };

    function showError(message = '') {
        error.textContent = message;
        error.classList.toggle('d-none', !message);
    }

    async function responseError(response, fallback) {
        const payload = await response.json().catch(() => ({}));
        return payload.detail || payload.message || fallback;
    }

    function textCell(value, className = '') {
        const cell = document.createElement('td');
        cell.className = className;
        cell.textContent = value || '-';
        return cell;
    }

    function projectCell(project) {
        const cell = document.createElement('td');
        const link = document.createElement('a');
        link.className = 'project-name-button link-info text-decoration-none fw-semibold';
        link.href = `/planning/projects/${project.id}/brief`;
        link.textContent = project.name;
        cell.appendChild(link);
        return cell;
    }

    function render(projects) {
        const requestedProjectId = String(root.dataset.projectId || '');
        const rows = projects.filter((project) => {
            const projectId = String(project.id);
            return !requestedProjectId || requestedProjectId === projectId;
        });
        if (!rows.length) {
            const row = document.createElement('tr');
            const cell = document.createElement('td');
            cell.colSpan = 4;
            cell.className = 'text-center text-muted py-5';
            cell.textContent = '暂无负责项目，请从选题库创建项目及任务。';
            row.appendChild(cell);
            body.replaceChildren(row);
            return;
        }
        body.replaceChildren(...rows.map((project) => {
            const row = document.createElement('tr');
            row.append(
                projectCell(project),
                textCell(project.topic_title),
                textCell(projectStatusLabels[project.status] || project.status),
            );
            const actions = document.createElement('td');
            actions.className = 'text-end';
            const projectLink = document.createElement('a');
            projectLink.className = 'btn btn-sm btn-outline-info me-1';
            projectLink.href = `/planning/projects/${project.id}/brief`;
            projectLink.title = '打开创作任务书';
            projectLink.setAttribute('aria-label', '打开创作任务书');
            projectLink.innerHTML = '<i class="bi bi-folder2-open" aria-hidden="true"></i>';
            actions.append(projectLink);
            row.appendChild(actions);
            return row;
        }));
    }

    async function load() {
        showError();
        try {
            const projectResponse = await window.aiworkerApi.request('/api/creative/projects');
            if (!projectResponse.ok) {
                throw new Error(await responseError(projectResponse, '读取负责项目失败'));
            }
            const projectPayload = await projectResponse.json();
            render(projectPayload.items || []);
        } catch (reason) {
            showError(reason.message || '读取负责项目失败');
            body.replaceChildren();
        }
    }

    load();
})();
