(async function () {
    'use strict';

    const root = document.getElementById('writer-dashboard');
    if (!root || !window.aiworkerApi) return;

    const currentUserId = String(root.dataset.userId || '');
    const writingTypes = new Set(['outline', 'beats', 'script']);
    const stageLabels = {outline: '全剧大纲', beats: 'Beats', script: '场次剧本'};
    const statusLabels = {
        pool: '待领取', assigned: '待开始', returned: '已退回',
        in_progress: '进行中', completed: '已完成', cancelled: '已取消',
    };
    const statusClasses = {
        pool: 'text-bg-secondary', assigned: 'text-bg-warning', returned: 'text-bg-danger',
        in_progress: 'text-bg-primary', completed: 'text-bg-success', cancelled: 'text-bg-secondary',
    };

    function setText(id, value) {
        const element = document.getElementById(id);
        if (element) element.textContent = String(value);
    }

    function showError(id, message) {
        const element = document.getElementById(id);
        if (!element) return;
        element.textContent = message || '';
        element.classList.toggle('d-none', !message);
    }

    async function requestJson(url) {
        const response = await aiworkerApi.request(url);
        if (!response.ok) {
            const detail = aiworkerApi.parseErrorDetail
                ? await aiworkerApi.parseErrorDetail(response)
                : '请求失败';
            throw new Error(detail || '请求失败');
        }
        return response.json();
    }

    function timestamp(value) {
        if (typeof value === 'number') return value < 100000000000 ? value * 1000 : value;
        const parsed = Date.parse(value || '');
        return Number.isNaN(parsed) ? 0 : parsed;
    }

    function formatDate(value) {
        const time = timestamp(value);
        if (!time) return '-';
        return new Date(time).toLocaleString('zh-CN', {
            month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
        });
    }

    function renderProgress(tasks, type) {
        const active = tasks.filter(task => task.task_type === type && task.status !== 'cancelled');
        const completed = active.filter(task => task.status === 'completed').length;
        const percent = active.length ? Math.round(completed / active.length * 100) : 0;
        setText(`progress-${type}-count`, `${completed} / ${active.length}`);
        setText(`progress-${type}-percent`, `${percent}%`);
        const bar = document.getElementById(`progress-${type}-bar`);
        if (bar) {
            bar.style.width = `${percent}%`;
            bar.parentElement?.setAttribute('aria-valuenow', String(percent));
        }
    }

    function taskRow(task, projectNames) {
        const row = document.createElement('tr');
        const stage = document.createElement('td');
        stage.className = 'ps-3 fw-semibold';
        stage.textContent = stageLabels[task.task_type] || task.task_type;

        const project = document.createElement('td');
        const projectId = String(task.project_id || '');
        const projectName = projectNames.get(projectId) || (projectId ? `项目 #${projectId}` : '未关联项目');
        if (projectId) {
            const link = document.createElement('a');
            link.className = 'text-reset text-decoration-none';
            link.href = `/writing/projects/${encodeURIComponent(projectId)}/outlines`;
            link.textContent = projectName;
            project.appendChild(link);
        } else {
            project.textContent = projectName;
        }

        const status = document.createElement('td');
        const badge = document.createElement('span');
        badge.className = `badge ${statusClasses[task.status] || 'text-bg-secondary'}`;
        badge.textContent = statusLabels[task.status] || task.status || '-';
        status.appendChild(badge);

        const updated = document.createElement('td');
        updated.className = 'pe-3 small text-muted';
        updated.textContent = formatDate(task.updated_at || task.created_at);
        row.append(stage, project, status, updated);
        return row;
    }

    function renderTasks(tasks, projects) {
        const mine = tasks.filter(task => (
            writingTypes.has(task.task_type)
            && String(task.assignee_user_id || '') === currentUserId
        ));
        setText('stat-pending', mine.filter(task => ['assigned', 'returned'].includes(task.status)).length);
        setText('stat-in-progress', mine.filter(task => task.status === 'in_progress').length);
        setText('stat-completed', mine.filter(task => task.status === 'completed').length);
        writingTypes.forEach(type => renderProgress(mine, type));

        const projectNames = new Map(projects.map(project => [String(project.id), project.name || `项目 #${project.id}`]));
        const recent = mine.slice().sort((left, right) => (
            timestamp(right.updated_at || right.created_at) - timestamp(left.updated_at || left.created_at)
        )).slice(0, 8);
        const body = document.getElementById('recent-writing-tasks');
        const empty = document.getElementById('recent-writing-tasks-empty');
        if (body) body.replaceChildren(...recent.map(task => taskRow(task, projectNames)));
        if (empty) empty.classList.toggle('d-none', recent.length > 0);
    }

    const [projectResult, taskResult] = await Promise.allSettled([
        requestJson('/api/creative/projects?participating_only=true'),
        requestJson('/api/workflow/tasks'),
    ]);
    const projects = projectResult.status === 'fulfilled' && Array.isArray(projectResult.value?.items)
        ? projectResult.value.items : [];
    const tasks = taskResult.status === 'fulfilled' && Array.isArray(taskResult.value)
        ? taskResult.value : [];

    setText('stat-projects', projects.length);
    renderTasks(tasks, projects);
    if (projectResult.status === 'rejected') {
        showError('writer-projects-error', `参与项目加载失败：${projectResult.reason?.message || '请稍后重试'}`);
    }
    if (taskResult.status === 'rejected') {
        showError('writer-tasks-error', `编剧任务加载失败：${taskResult.reason?.message || '请稍后重试'}`);
    }
})();
