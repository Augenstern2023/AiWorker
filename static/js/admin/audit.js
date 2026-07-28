(async function () {
    const body = document.getElementById('audit-list');
    if (!body) return;

    const filters = document.getElementById('audit-filters');
    const fields = {
        actor: document.getElementById('audit-actor'),
        project: document.getElementById('audit-project'),
        action: document.getElementById('audit-action'),
        targetType: document.getElementById('audit-target-type'),
        targetId: document.getElementById('audit-target-id'),
    };
    const actionMetadata = {
        'audit.export': ['导出审计日志', '导出当前筛选范围内的审计记录'],
        'auth.login': ['用户登录', '用户完成身份认证'],
        'auth.logout': ['用户退出', '用户主动结束登录会话'],
        'job.create': ['新建任务', '提交新的爬取或处理任务'],
        'organization.department.create': ['新增部门', '创建组织架构中的部门'],
        'organization.department.rename': ['重命名部门', '修改部门显示名称'],
        'organization.department.move': ['移动部门', '调整部门的上级部门'],
        'organization.department.delete': ['删除部门', '删除没有成员和子部门的部门'],
    };
    const targetMetadata = {
        api_route: ['接口路由', '系统 API 的访问或写入操作'],
        audit_log: ['审计记录', '审计日志导出或查看操作'],
        department: ['部门', '组织架构中的部门'],
        job: ['任务', 'JobManager 中的任务'],
        navigation_item: ['导航项', '系统导航展示配置'],
        role: ['角色', '角色及其权限配置'],
        schedule: ['调度计划', '可追踪的本机调度计划'],
        schedule_run: ['调度运行', '调度计划的一次执行记录'],
        session: ['登录会话', '用户登录或退出的会话'],
        task: ['工作流任务', '待领取、处理或审批的任务'],
        user: ['用户', '系统用户及其角色或部门归属'],
        workflow_task: ['工作流任务', '工作流中的具体任务'],
    };
    let offset = 0;
    const pageSize = 50;
    let total = 0;
    let projectScopeRequired = false;

    function describeAction(action) {
        if (actionMetadata[action]) return actionMetadata[action];
        const [domain] = action.split('.', 1);
        const domainLabels = {
            navigation: '导航配置', organization: '组织架构', role: '角色权限',
            schedule: '调度中心', user: '用户管理', workflow: '工作流',
        };
        return [domainLabels[domain] || action, `系统记录的 ${action} 操作`];
    }

    function describeTargetType(targetType) {
        return targetMetadata[targetType] || [targetType, `系统记录的 ${targetType} 资源`];
    }

    function formatAuditTimestamp(value) {
        if (!value) return '-';
        const timestamp = new Date(value);
        if (Number.isNaN(timestamp.getTime())) return String(value).replace('T', ' ').replace(/\.\d+(?=[+-]\d\d:\d\d$)/, '');
        const pad = (part) => String(part).padStart(2, '0');
        return `${timestamp.getFullYear()}-${pad(timestamp.getMonth() + 1)}-${pad(timestamp.getDate())} ${pad(timestamp.getHours())}:${pad(timestamp.getMinutes())}:${pad(timestamp.getSeconds())}`;
    }

    function replaceOptions(select, items, placeholder, formatItem, { disabled = false } = {}) {
        const previous = select.value;
        select.replaceChildren(new Option(placeholder, ''));
        for (const item of items) {
            const [label, description] = formatItem(item);
            select.add(new Option(`${label} - ${description}`, item.value));
        }
        select.value = items.some((item) => item.value === previous) ? previous : '';
        select.disabled = disabled;
    }

    function filterParameters() {
        const params = new URLSearchParams({ limit: String(pageSize), offset: String(offset) });
        for (const [name, rawValue] of new FormData(filters).entries()) {
            const value = String(rawValue).trim();
            if (!value) continue;
            params.set(name, name.startsWith('created_') ? value.replace('T', ' ') + ':00' : value);
        }
        return params;
    }

    function showListError(message) {
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = 5;
        cell.className = 'text-center text-muted py-4';
        cell.textContent = message;
        row.appendChild(cell);
        body.replaceChildren(row);
    }

    function requiresProjectSelection() {
        return projectScopeRequired && !fields.project.value;
    }

    function showProjectSelectionRequired() {
        total = 0;
        document.getElementById('audit-total').textContent = '请选择一个负责项目后查看审计日志';
        document.getElementById('audit-previous').disabled = true;
        document.getElementById('audit-next').disabled = true;
        showListError('请选择一个负责项目后查看、筛选或导出审计日志');
    }

    async function loadFilterOptions() {
        const params = new URLSearchParams();
        if (fields.project.value) params.set('project_id', fields.project.value);
        if (fields.targetType.value) params.set('target_type', fields.targetType.value);
        const response = await window.aiworkerApi.request(`/api/admin/audit/filter-options?${params.toString()}`);
        if (!response.ok) throw new Error('筛选项加载失败');
        const options = await response.json();
        projectScopeRequired = Boolean(options.project_scope_required);
        replaceOptions(fields.actor, options.actors, '全部操作人', (item) => [item.label, '执行过审计操作的账号']);
        replaceOptions(fields.project, options.projects, '全部项目', (item) => [`项目 ${item.label}`, '该项目已有审计记录']);
        replaceOptions(fields.action, options.actions, '全部操作类型', (item) => describeAction(item.value));
        replaceOptions(fields.targetType, options.target_types, '全部资源类型', (item) => describeTargetType(item.value));
        replaceOptions(
            fields.targetId,
            options.target_ids,
            '全部具体资源',
            (item) => [item.label, '该资源已有审计记录'],
        );
        document.getElementById('audit-project-help').textContent = projectScopeRequired
            ? '请选择本人负责的项目；仅可查看该项目范围内的审计记录。'
            : '仅显示已产生审计记录的项目。';
    }

    async function loadAudit() {
        if (requiresProjectSelection()) {
            showProjectSelectionRequired();
            return;
        }
        try {
            const response = await window.aiworkerApi.request(`/api/admin/audit?${filterParameters().toString()}`);
            if (!response.ok) throw new Error('审计日志加载失败');
            const events = await response.json();
            total = Number(response.headers.get('X-Total-Count') || 0);
            document.getElementById('audit-total').textContent = `共 ${total} 条，第 ${Math.floor(offset / pageSize) + 1} 页`;
            document.getElementById('audit-previous').disabled = offset === 0;
            document.getElementById('audit-next').disabled = offset + events.length >= total;
            body.replaceChildren(...events.map((event) => {
                const row = document.createElement('tr');
                const actionLabel = describeAction(event.action || '-')[0];
                for (const value of [formatAuditTimestamp(event.created_at), event.actor_display_name || event.actor_user_id || '-', actionLabel, `${event.target_type || '-'} ${event.target_id || ''}`.trim(), event.result || '-']) {
                    const cell = document.createElement('td');
                    cell.textContent = value;
                    row.appendChild(cell);
                }
                row.tabIndex = 0;
                row.className = 'cursor-pointer';
                row.addEventListener('click', () => showDetails(event.id));
                return row;
            }));
            if (!events.length) showListError('没有符合当前筛选条件的审计记录');
        } catch (error) {
            showListError(error.message || '审计日志加载失败');
        }
    }

    function detailValue(value) {
        return value === null || value === undefined ? '-' : (typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value));
    }

    async function showDetails(eventId) {
        try {
            const response = await window.aiworkerApi.request(`/api/admin/audit/${encodeURIComponent(eventId)}`);
            if (!response.ok) throw new Error('审计详情加载失败');
            const event = await response.json();
            const fieldsContainer = document.getElementById('audit-details-fields');
            fieldsContainer.replaceChildren();
            for (const [label, key] of [['时间', 'created_at'], ['操作人', 'actor_display_name'], ['动作', 'action'], ['目标', 'target_id'], ['项目', 'project_id'], ['结果', 'result'], ['原因', 'reason'], ['变更前', 'before_summary'], ['变更后', 'after_summary']]) {
                const term = document.createElement('dt');
                term.className = 'col-sm-3';
                term.textContent = label;
                const definition = document.createElement('dd');
                definition.className = 'col-sm-9 text-break';
                definition.textContent = key === 'created_at' ? formatAuditTimestamp(event[key]) : detailValue(event[key]);
                fieldsContainer.append(term, definition);
            }
            document.getElementById('audit-details').classList.remove('d-none');
        } catch (error) {
            showListError(error.message || '审计详情加载失败');
        }
    }

    async function exportAudit(format) {
        if (requiresProjectSelection()) {
            showProjectSelectionRequired();
            return;
        }
        try {
            const params = filterParameters();
            params.set('format', format);
            const response = await window.aiworkerApi.request(`/api/admin/audit/export?${params.toString()}`);
            if (!response.ok) throw new Error('审计日志导出失败');
            const link = document.createElement('a');
            link.href = URL.createObjectURL(await response.blob());
            link.download = `audit-export.${format}`;
            link.click();
            URL.revokeObjectURL(link.href);
        } catch (error) {
            showListError(error.message || '审计日志导出失败');
        }
    }

    document.getElementById('refresh-audit')?.addEventListener('click', async () => {
        try { await loadFilterOptions(); await loadAudit(); } catch (error) { showListError(error.message || '筛选项加载失败'); }
    });
    filters?.addEventListener('submit', (event) => { event.preventDefault(); offset = 0; loadAudit(); });
    fields.project.addEventListener('change', async () => { offset = 0; fields.targetType.value = ''; fields.targetId.value = ''; try { await loadFilterOptions(); await loadAudit(); } catch (error) { showListError(error.message || '筛选项加载失败'); } });
    fields.targetType.addEventListener('change', async () => { offset = 0; fields.targetId.value = ''; try { await loadFilterOptions(); } catch (error) { showListError(error.message || '筛选项加载失败'); } });
    document.getElementById('audit-previous')?.addEventListener('click', () => { offset = Math.max(0, offset - pageSize); loadAudit(); });
    document.getElementById('audit-next')?.addEventListener('click', () => { if (offset + pageSize < total) { offset += pageSize; loadAudit(); } });
    document.getElementById('audit-details-close')?.addEventListener('click', () => document.getElementById('audit-details').classList.add('d-none'));
    document.getElementById('export-audit-csv')?.addEventListener('click', () => exportAudit('csv'));
    document.getElementById('export-audit-xlsx')?.addEventListener('click', () => exportAudit('xlsx'));

    try {
        await loadFilterOptions();
        await loadAudit();
    } catch (error) {
        showListError(error.message || '筛选项加载失败');
    }
})();
