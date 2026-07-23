(function () {
    'use strict';

    const root = document.getElementById('managed-projects');
    const body = document.getElementById('managed-project-list');
    const error = document.getElementById('managed-project-error');
    if (!root || !body || !error) return;

    const taskStatusLabels = {
        pool: '待分配',
        assigned: '已分配',
        in_progress: '进行中',
        waiting_review: '待审批',
        returned: '已退回',
        completed: '已完成',
        cancelled: '已取消',
    };
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

    function render(projects, tasks) {
        const briefTasks = new Map(
            tasks
                .filter((task) => task.task_type === 'brief' && task.project_id && task.assignee_user_id)
                .map((task) => [String(task.project_id), task]),
        );
        const requestedProjectId = String(root.dataset.projectId || '');
        const rows = projects.filter((project) => {
            const projectId = String(project.id);
            return briefTasks.has(projectId) && (!requestedProjectId || requestedProjectId === projectId);
        });
        if (!rows.length) {
            const row = document.createElement('tr');
            const cell = document.createElement('td');
            cell.colSpan = 6;
            cell.className = 'text-center text-muted py-5';
            cell.textContent = '暂无已分配的策划项目，请从选题库分配策划任务。';
            row.appendChild(cell);
            body.replaceChildren(row);
            return;
        }
        body.replaceChildren(...rows.map((project) => {
            const task = briefTasks.get(String(project.id));
            const row = document.createElement('tr');
            row.append(
                textCell(project.name, 'fw-semibold'),
                textCell(project.topic_title),
                textCell(task.assignee_display_name),
                textCell(taskStatusLabels[task.status] || task.status),
                textCell(projectStatusLabels[project.status] || project.status),
            );
            const actions = document.createElement('td');
            actions.className = 'text-end';
            const projectLink = document.createElement('a');
            projectLink.className = 'btn btn-sm btn-outline-info me-1';
            projectLink.href = `/review/projects/${project.id}/members`;
            projectLink.title = '查看项目';
            projectLink.setAttribute('aria-label', '查看项目');
            projectLink.innerHTML = '<i class="bi bi-folder2-open" aria-hidden="true"></i>';
            const approvalLink = document.createElement('a');
            approvalLink.className = 'btn btn-sm btn-outline-success';
            approvalLink.href = `/review/approvals?project_id=${encodeURIComponent(project.id)}`;
            approvalLink.title = '查看审批';
            approvalLink.setAttribute('aria-label', '查看审批');
            approvalLink.innerHTML = '<i class="bi bi-clipboard-check" aria-hidden="true"></i>';
            actions.append(projectLink, approvalLink);
            row.appendChild(actions);
            return row;
        }));
    }

    async function load() {
        showError();
        try {
            const [projectResponse, taskResponse] = await Promise.all([
                window.aiworkerApi.request('/api/creative/projects'),
                window.aiworkerApi.request('/api/workflow/tasks'),
            ]);
            if (!projectResponse.ok) {
                throw new Error(await responseError(projectResponse, '读取负责项目失败'));
            }
            if (!taskResponse.ok) {
                throw new Error(await responseError(taskResponse, '读取策划任务失败'));
            }
            const projectPayload = await projectResponse.json();
            const tasks = await taskResponse.json();
            render(projectPayload.items || [], tasks || []);
        } catch (reason) {
            showError(reason.message || '读取负责项目失败');
            body.replaceChildren();
        }
    }

    load();
})();
