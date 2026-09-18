(function () {
    'use strict';

    const root = document.getElementById('managed-projects');
    const body = document.getElementById('managed-project-list');
    const error = document.getElementById('managed-project-error');
    if (!root || !error) return;

    const projectId = String(root.dataset.projectId || '');
    const canManageMembers = root.dataset.canManageMembers === 'true';

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
    const projectRoleLabels = {
        owner: '项目负责人',
        project_owner: '项目负责人',
        project_creator: '项目创建负责人',
        creative_planner: '创意策划',
        screenwriter: '编剧',
    };
    const memberStatusLabels = {
        active: '有效',
        inactive: '停用',
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
        const rows = projects;
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
            actions.className = 'text-end text-nowrap';
            const projectLink = document.createElement('a');
            projectLink.className = 'btn btn-sm btn-outline-info me-1';
            projectLink.href = `/planning/projects/${project.id}/brief`;
            projectLink.title = '打开创作任务书';
            projectLink.setAttribute('aria-label', '打开创作任务书');
            projectLink.innerHTML = '<i class="bi bi-folder2-open" aria-hidden="true"></i><span class="visually-hidden">打开创作任务书</span>';
            actions.append(projectLink);
            if (project.can_production_export && window.aiworkerProductionExport) {
                const exportButton = document.createElement('button');
                exportButton.type = 'button';
                exportButton.className = 'btn btn-sm btn-outline-success me-1';
                exportButton.title = '导出项目文档包';
                exportButton.setAttribute('aria-label', `导出${project.name}的项目文档包`);
                exportButton.innerHTML = '<i class="bi bi-box-arrow-up" aria-hidden="true"></i><span class="visually-hidden">导出项目文档包</span>';
                exportButton.addEventListener('click', () => {
                    window.aiworkerProductionExport.open(project.id, {
                        projectName: project.name,
                        trigger: exportButton,
                    });
                });
                actions.append(exportButton);
            }
            if (canManageMembers) {
                const memberLink = document.createElement('a');
                memberLink.className = 'btn btn-sm btn-outline-secondary';
                memberLink.href = `/review/projects/${project.id}/members`;
                memberLink.title = '编辑项目成员';
                memberLink.setAttribute('aria-label', '编辑项目成员');
                memberLink.innerHTML = '<i class="bi bi-people" aria-hidden="true"></i><span class="visually-hidden">编辑项目成员</span>';
                actions.append(memberLink);
            }
            row.appendChild(actions);
            return row;
        }));
    }

    async function loadProjects() {
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
            if (body) body.replaceChildren();
        }
    }

    function initializeMemberEditor() {
        const title = document.getElementById('member-project-title');
        const memberError = document.getElementById('project-member-error');
        const form = document.getElementById('project-member-form');
        const roleSelect = document.getElementById('project-member-role');
        const userSelect = document.getElementById('project-member-user');
        const addButton = document.getElementById('project-member-add');
        const loading = document.getElementById('project-member-loading');
        const memberBody = document.getElementById('project-member-list');
        if (!title || !memberError || !form || !roleSelect || !userSelect || !addButton || !loading || !memberBody) {
            showError('项目成员页面加载不完整，请刷新后重试');
            return;
        }

        let members = [];
        let saving = false;

        function showMemberError(message = '') {
            memberError.textContent = message;
            memberError.classList.toggle('d-none', !message);
        }

        function setSaving(value) {
            saving = value;
            roleSelect.disabled = value;
            userSelect.disabled = value || !userSelect.value;
            addButton.disabled = value || !userSelect.value;
            if (members.length) renderMembers();
        }

        function memberName(member) {
            return member.display_name || member.username || '已停用账号';
        }

        function renderMembers() {
            loading.classList.add('d-none');
            if (!members.length) {
                const row = document.createElement('tr');
                const cell = document.createElement('td');
                cell.colSpan = 5;
                cell.className = 'text-center text-muted py-5';
                cell.textContent = '暂无项目成员。';
                row.appendChild(cell);
                memberBody.replaceChildren(row);
                return;
            }
            memberBody.replaceChildren(...members.map((member) => {
                const row = document.createElement('tr');
                row.append(
                    textCell(memberName(member)),
                    textCell(member.username),
                    textCell(projectRoleLabels[member.project_role] || member.project_role),
                    textCell(memberStatusLabels[member.status] || member.status),
                );
                const actions = document.createElement('td');
                actions.className = 'text-end';
                const removeButton = document.createElement('button');
                removeButton.type = 'button';
                removeButton.className = 'btn btn-sm btn-outline-danger';
                const roleLabel = projectRoleLabels[member.project_role] || member.project_role;
                const actionVerb = member.project_role === 'project_creator' ? '停用' : '移除';
                removeButton.textContent = actionVerb;
                removeButton.disabled = saving;
                removeButton.title = `${actionVerb}${memberName(member)}的${roleLabel}角色`;
                removeButton.setAttribute('aria-label', removeButton.title);
                removeButton.addEventListener('click', () => removeMember(member));
                actions.appendChild(removeButton);
                row.appendChild(actions);
                return row;
            }));
        }

        async function loadProjectTitle() {
            const response = await window.aiworkerApi.request('/api/creative/projects');
            if (!response.ok) throw new Error(await responseError(response, '读取项目信息失败'));
            const payload = await response.json();
            const project = (payload.items || []).find((item) => String(item.id) === projectId);
            title.textContent = project ? project.name : `项目 ${projectId}`;
        }

        async function loadMembers() {
            loading.classList.remove('d-none');
            const response = await window.aiworkerApi.request(`/api/workflow/projects/${projectId}/members`);
            if (!response.ok) throw new Error(await responseError(response, '读取项目成员失败'));
            members = await response.json();
            renderMembers();
        }

        async function loadCandidates() {
            const role = roleSelect.value;
            userSelect.disabled = true;
            addButton.disabled = true;
            userSelect.replaceChildren(new Option('正在加载候选人...', ''));
            const response = await window.aiworkerApi.request(
                `/api/workflow/projects/${projectId}/member-candidates?project_role=${encodeURIComponent(role)}`,
            );
            if (!response.ok) throw new Error(await responseError(response, '读取候选成员失败'));
            const existingUserIds = new Set(
                members.filter((member) => member.project_role === role).map((member) => String(member.user_id)),
            );
            const candidates = (await response.json()).filter(
                (candidate) => !existingUserIds.has(String(candidate.user_id)),
            );
            if (!candidates.length) {
                userSelect.replaceChildren(new Option('暂无可添加成员', ''));
                userSelect.disabled = true;
                addButton.disabled = true;
                return;
            }
            userSelect.replaceChildren(...candidates.map((candidate) => (
                new Option(`${candidate.display_name || candidate.username}（${candidate.username}）`, candidate.user_id)
            )));
            userSelect.disabled = saving;
            addButton.disabled = saving;
        }

        async function refreshEditor() {
            await loadMembers();
            await loadCandidates();
        }

        async function addMember(event) {
            event.preventDefault();
            if (!userSelect.value || saving) return;
            showMemberError();
            setSaving(true);
            try {
                const response = await window.aiworkerApi.request(`/api/workflow/projects/${projectId}/members`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        user_id: userSelect.value,
                        project_role: roleSelect.value,
                        status: 'active',
                    }),
                });
                if (!response.ok) throw new Error(await responseError(response, '添加项目成员失败'));
                await refreshEditor();
            } catch (reason) {
                showMemberError(reason.message || '添加项目成员失败');
            } finally {
                setSaving(false);
            }
        }

        async function removeMember(member) {
            const roleLabel = projectRoleLabels[member.project_role] || member.project_role;
            const actionVerb = member.project_role === 'project_creator' ? '停用' : '移除';
            if (saving || !window.confirm(`确定${actionVerb}${memberName(member)}的${roleLabel}角色吗？`)) return;
            showMemberError();
            setSaving(true);
            try {
                const response = await window.aiworkerApi.request(
                    `/api/workflow/projects/${projectId}/members/${encodeURIComponent(member.id)}`,
                    {method: 'DELETE'},
                );
                if (!response.ok) throw new Error(await responseError(response, `${actionVerb}项目成员失败`));
                await refreshEditor();
            } catch (reason) {
                showMemberError(reason.message || `${actionVerb}项目成员失败`);
            } finally {
                setSaving(false);
            }
        }

        roleSelect.addEventListener('change', async () => {
            showMemberError();
            try {
                await loadCandidates();
            } catch (reason) {
                showMemberError(reason.message || '读取候选成员失败');
                userSelect.replaceChildren(new Option('候选人加载失败', ''));
            }
        });
        form.addEventListener('submit', addMember);

        Promise.all([loadProjectTitle(), loadMembers()])
            .then(loadCandidates)
            .catch((reason) => {
                showMemberError(reason.message || '读取项目成员失败');
                loading.classList.add('d-none');
                userSelect.replaceChildren(new Option('候选人加载失败', ''));
            });
    }

    if (projectId && canManageMembers) {
        initializeMemberEditor();
    } else if (body) {
        loadProjects();
    }
})();
