(function () {
    'use strict';

    const projectList = document.getElementById('my-project-list');
    const errorBox = document.getElementById('my-tasks-error');
    const refreshBtn = document.getElementById('refresh-my-tasks');
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
            const link = document.createElement('a');
            link.className = 'btn btn-sm btn-outline-info';
            link.href = projectHref(project.id);
            link.title = '查看项目';
            link.setAttribute('aria-label', '查看项目');
            link.innerHTML = '<i class="bi bi-folder2-open" aria-hidden="true"></i>';
            actions.appendChild(link);
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
    load();
})();
