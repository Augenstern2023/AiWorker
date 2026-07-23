(function () {
    'use strict';

    const submissionList = document.getElementById('my-submission-list');
    const projectList = document.getElementById('my-project-list');
    const errorBox = document.getElementById('my-tasks-error');
    const refreshBtn = document.getElementById('refresh-my-tasks');
    if (!submissionList || !projectList || !errorBox) return;

    const submissionStatusLabels = {
        draft: '草稿',
        submitted: '已提交',
        approved: '已批准',
        returned: '已退回',
        superseded: '已 supersede',
    };

    const taskTypeLabels = {
        brief: '创作任务书',
        characters: '人物关系',
        outline: '全剧大纲',
        beats: '单集节拍',
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

    function showError(message) {
        errorBox.textContent = message || '';
        errorBox.classList.toggle('d-none', !message);
    }

    function formatDate(value) {
        if (!value) return '-';
        const date = new Date(value * 1000);
        if (Number.isNaN(date.getTime())) return '-';
        return date.toLocaleString('zh-CN', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
        });
    }

    function textCell(value, className) {
        const cell = document.createElement('td');
        cell.className = className || '';
        cell.textContent = value || '-';
        return cell;
    }

    function renderSubmissions(submissions) {
        const countEl = document.getElementById('submission-count');
        if (countEl) countEl.textContent = String(submissions.length);
        const rows = submissions.length
            ? submissions.map((item) => {
                    const row = document.createElement('tr');
                    row.append(
                        textCell(taskTypeLabels[item.resource_type] || item.resource_type),
                        textCell(item.project_name || '-'),
                        textCell(taskTypeLabels[item.task_type] || item.task_type),
                        textCell(submissionStatusLabels[item.status] || item.status),
                        textCell(formatDate(item.submitted_at || item.created_at)),
                        textCell(item.reviewed_by || '-'),
                        textCell(formatDate(item.reviewed_at)),
                        textCell(item.review_comment || '-'),
                    );
                    return row;
                })
            : [
                    {
                        colspan: 8,
                        className: 'text-center text-muted py-4',
                        textContent: '暂无审批记录',
                    },
                ].map((meta) => {
                    const row = document.createElement('tr');
                    const cell = document.createElement('td');
                    cell.colSpan = 8;
                    cell.className = 'text-center text-muted py-4';
                    cell.textContent = meta.textContent;
                    row.appendChild(cell);
                    return row;
                });
        submissionList.replaceChildren(...rows);
    }

    function renderProjects(projects) {
        const countEl = document.getElementById('project-count');
        if (countEl) countEl.textContent = String(projects.length);
        const projectHref = (projectId) => {
            const path = window.location.pathname || '';
            if (path.startsWith('/writing/')) return `/writing/projects/${projectId}/outlines`;
            return `/planning/projects/${projectId}/brief`;
        };
        const rows = projects.length
            ? projects.map((project) => {
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
                })
            : [
                    {
                        colspan: 5,
                        className: 'text-center text-muted py-4',
                        textContent: '暂无被分配的项目',
                    },
                ].map((meta) => {
                    const row = document.createElement('tr');
                    const cell = document.createElement('td');
                    cell.colSpan = 5;
                    cell.className = 'text-center text-muted py-4';
                    cell.textContent = meta.textContent;
                    row.appendChild(cell);
                    return row;
                });
        projectList.replaceChildren(...rows);
    }

    async function load() {
        showError();
        try {
            const [submissionResponse, projectResponse] = await Promise.all([
                aiworkerApi.request('/api/workflow/my-submissions'),
                aiworkerApi.request('/api/creative/projects'),
            ]);
            if (!submissionResponse.ok) {
                throw new Error(await submissionResponse.json().then((payload) => payload.detail || '读取审批记录失败').catch(() => '读取审批记录失败'));
            }
            if (!projectResponse.ok) {
                throw new Error(await projectResponse.json().then((payload) => payload.detail || '读取项目失败').catch(() => '读取项目失败'));
            }
            const submissions = await submissionResponse.json();
            const projectPayload = await projectResponse.json();
            const projects = Array.isArray(projectPayload.items) ? projectPayload.items : [];
            const projectNameMap = new Map(projects.map((project) => [String(project.id), project.name]));
            const enrichedSubmissions = (Array.isArray(submissions) ? submissions : []).map((item) => ({
                ...item,
                project_name: item.project_id ? projectNameMap.get(String(item.project_id)) || '-' : '-',
            }));
            renderSubmissions(enrichedSubmissions);
            renderProjects(projects);
        } catch (reason) {
            showError(reason.message || '读取任务数据失败');
            renderSubmissions([]);
            renderProjects([]);
        }
    }

    refreshBtn?.addEventListener('click', load);
    load();
})();
