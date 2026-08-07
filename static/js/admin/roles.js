(function () {
    'use strict';

    const elements = {
        body: document.getElementById('role-list'),
        error: document.getElementById('role-error'),
        formError: document.getElementById('role-form-error'),
        form: document.getElementById('role-editor-form'),
        title: document.getElementById('role-editor-title'),
        id: document.getElementById('role-id'),
        code: document.getElementById('role-code'),
        name: document.getElementById('role-name'),
        description: document.getElementById('role-description'),
        claimableTaskTypes: document.getElementById('role-claimable-task-types'),
        permissions: document.getElementById('permission-list'),
        pagesForm: document.getElementById('role-pages-form'),
        pagesRoleId: document.getElementById('role-pages-role-id'),
        pagesRoleName: document.getElementById('role-pages-role-name'),
        pagesSystemNotice: document.getElementById('role-pages-system-notice'),
        pages: document.getElementById('role-pages-list'),
        pagesError: document.getElementById('role-pages-error'),
        filters: document.getElementById('role-filters'),
        total: document.getElementById('role-total'),
    };
    if (!elements.body) return;

    const editor = bootstrap.Modal.getOrCreateInstance(document.getElementById('role-editor-modal'));
    const pagesEditor = bootstrap.Modal.getOrCreateInstance(document.getElementById('role-pages-modal'));
    const teamMode = window.location.pathname.startsWith('/review/');
    const rolesApi = teamMode ? '/api/team/roles' : '/api/admin/roles';
    const permissionsApi = teamMode ? '/api/team/permissions' : '/api/admin/permissions';
    let roles = [];
    let permissionCatalog = [];
    let pageCatalog = [];

    function showError(target, message = '') {
        target.textContent = message;
        target.classList.toggle('d-none', !message);
    }

    async function responseError(response, fallback) {
        const data = await response.json().catch(() => ({}));
        return data.detail || data.message || fallback;
    }

    function filterParameters() {
        return new URLSearchParams(new FormData(elements.filters));
    }

    function formatDate(value) {
        return value
            ? String(value).replace('T', ' ').replace(/\.\d+(?=[+-]\d\d:\d\d$)/, '').replace(/[+-]\d\d:\d\d$/, '')
            : '-';
    }

    function actionButton(icon, label, style, handler, disabled = false) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `btn btn-sm flex-shrink-0 ${style}`;
        button.title = label;
        button.setAttribute('aria-label', label);
        const glyph = document.createElement('i');
        glyph.className = `bi ${icon}`;
        button.appendChild(glyph);
        button.disabled = disabled;
        button.addEventListener('click', handler);
        return button;
    }

    function renderPermissionEditor(grants = []) {
        const grantMap = new Map(grants.map((grant) => [grant.permission_code, grant.data_scope]));
        elements.permissions.replaceChildren(...permissionCatalog.map((permission) => {
            const row = document.createElement('tr');
            const toggleCell = document.createElement('td');
            const toggle = document.createElement('input');
            toggle.type = 'checkbox';
            toggle.className = 'form-check-input permission-toggle';
            toggle.dataset.permissionCode = permission.code;
            toggle.checked = grantMap.has(permission.code);
            toggleCell.appendChild(toggle);

            const labelCell = document.createElement('td');
            labelCell.textContent = permission.label;
            if (permission.dependencies?.length) {
                const dependencies = document.createElement('small');
                dependencies.className = 'd-block text-muted';
                dependencies.textContent = `依赖：${permission.dependencies.join('、')}`;
                labelCell.appendChild(dependencies);
            }
            const codeCell = document.createElement('td');
            codeCell.textContent = permission.code;
            const scopeCell = document.createElement('td');
            const scope = document.createElement('select');
            scope.className = 'form-select form-select-sm permission-scope';
            scope.dataset.permissionCode = permission.code;
            const scopeLabels = {task: '本人任务', project: '所在项目', managed_projects: '负责项目', all: '全部数据'};
            const allowedScopes = permission.allowed_scopes || ['task', 'project', 'managed_projects', 'all'];
            for (const value of allowedScopes) {
                const option = document.createElement('option');
                option.value = value;
                option.textContent = scopeLabels[value] || value;
                option.selected = (grantMap.get(permission.code) || 'task') === value;
                scope.appendChild(option);
            }
            scope.disabled = !toggle.checked;
            toggle.addEventListener('change', () => { scope.disabled = !toggle.checked; });
            scopeCell.appendChild(scope);
            row.append(toggleCell, labelCell, codeCell, scopeCell);
            return row;
        }));
    }

    function selectedPermissions() {
        return Array.from(elements.permissions.querySelectorAll('.permission-toggle:checked'), (toggle) => {
            const scope = elements.permissions.querySelector(`.permission-scope[data-permission-code="${CSS.escape(toggle.dataset.permissionCode)}"]`);
            return {permission_code: toggle.dataset.permissionCode, data_scope: scope.value};
        });
    }

    function renderRolePages(grantedRoutes = [], readOnly = false) {
        const granted = new Set(grantedRoutes);
        elements.pages.replaceChildren(...pageCatalog.map((page) => {
            const wrapper = document.createElement('div');
            wrapper.className = 'col-12 col-md-6';
            const label = document.createElement('label');
            label.className = 'role-page-option d-flex align-items-start gap-3 border rounded p-3 w-100 h-100';
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.className = 'form-check-input role-page-toggle flex-shrink-0 mt-1';
            checkbox.value = page.route;
            checkbox.checked = granted.has(page.route);
            checkbox.disabled = readOnly;
            const copy = document.createElement('span');
            copy.className = 'role-page-option-copy d-block flex-grow-1';
            const text = document.createElement('span');
            text.className = 'd-block fw-semibold';
            text.textContent = page.label;
            const route = document.createElement('small');
            route.className = 'd-block text-muted text-break mt-1';
            route.textContent = page.route;
            copy.append(text, route);
            label.append(checkbox, copy);
            wrapper.appendChild(label);
            return wrapper;
        }));
    }

    function openRolePages(role) {
        const isSystemAdmin = role.code === 'system_admin';
        showError(elements.pagesError);
        elements.pagesForm.reset();
        elements.pagesRoleId.value = role.id;
        elements.pagesRoleName.textContent = `${role.name}（${role.code}）`;
        elements.pagesForm.dataset.systemAdmin = String(isSystemAdmin);
        elements.pagesSystemNotice.hidden = !isSystemAdmin;
        elements.pagesSystemNotice.textContent = isSystemAdmin ? '系统管理员默认拥有全部页面访问权限。' : '';
        elements.pagesForm.querySelector('button[type="submit"]').disabled = isSystemAdmin;
        renderRolePages(isSystemAdmin ? pageCatalog.map((page) => page.route) : (role.page_routes || []), isSystemAdmin);
        pagesEditor.show();
    }

    function openRoleEditor(role = null) {
        elements.form.reset();
        showError(elements.formError);
        elements.id.value = role?.id || '';
        elements.title.textContent = role ? '编辑角色权限' : '新建角色';
        elements.code.value = role?.code || '';
        elements.code.disabled = Boolean(role);
        elements.name.value = role?.name || '';
        elements.description.value = role?.description || '';
        elements.claimableTaskTypes.value = (role?.claimable_task_types || []).join(', ');
        renderPermissionEditor(role?.permissions || []);
        editor.show();
    }

    function renderRoles() {
        if (elements.total) elements.total.textContent = `共 ${roles.length} 个角色`;
        if (!roles.length) {
            const row = document.createElement('tr');
            const cell = document.createElement('td');
            cell.colSpan = teamMode ? 5 : 7;
            cell.className = 'text-center text-muted py-4';
            cell.textContent = '暂无符合条件的角色';
            row.appendChild(cell);
            elements.body.replaceChildren(row);
            return;
        }
        elements.body.replaceChildren(...roles.map((role) => {
            const row = document.createElement('tr');
            const values = teamMode
                ? [role.name, role.code, String((role.permissions || []).length), role.is_enabled ? '启用' : '停用']
                : [role.name, role.code, role.description || '-', String((role.permissions || []).length), role.is_enabled ? '启用' : '停用', formatDate(role.created_at)];
            for (const value of values) {
                const cell = document.createElement('td');
                cell.textContent = value;
                row.appendChild(cell);
            }
            const actions = document.createElement('td');
            actions.className = 'text-nowrap';
            const group = document.createElement('div');
            group.className = 'd-inline-flex flex-nowrap gap-1';
            if (role.can_edit !== false) {
                group.append(
                    actionButton('bi-pencil', '编辑权限', 'btn-outline-light', () => openRoleEditor(role)),
                    ...(!teamMode ? [actionButton('bi-window-stack', '页面授权', 'btn-outline-info', () => openRolePages(role))] : []),
                    actionButton(
                        role.is_system ? 'bi-lock' : (role.is_enabled ? 'bi-toggle-off' : 'bi-toggle-on'),
                        role.is_system ? '系统角色不可停用' : (role.is_enabled ? '停用角色' : '启用角色'),
                        role.is_system ? 'btn-outline-secondary' : (role.is_enabled ? 'btn-outline-warning' : 'btn-outline-success'),
                        () => window.toggleRoleStatus(role),
                        role.is_system,
                    ),
                );
            }
            if (role.can_assign !== false) {
                group.append(actionButton('bi-files', '克隆角色', 'btn-outline-info', () => cloneRole(role)));
            }
            if (role.can_delete) {
                group.append(actionButton('bi-trash', '删除角色', 'btn-outline-danger', () => deleteRole(role)));
            }
            actions.appendChild(group);
            row.appendChild(actions);
            return row;
        }));
    }

    async function loadRolesAndPermissions() {
        showError(elements.error);
        try {
            const roleListUrl = teamMode ? rolesApi : `${rolesApi}?${filterParameters().toString()}`;
            const requests = [
                window.aiworkerApi.request(roleListUrl),
                window.aiworkerApi.request(permissionsApi),
            ];
            if (!teamMode) requests.push(window.aiworkerApi.request('/api/admin/roles/page-catalog'));
            const [rolesResponse, permissionsResponse, pagesResponse] = await Promise.all(requests);
            if (!rolesResponse.ok) throw new Error(await responseError(rolesResponse, '角色加载失败'));
            if (!permissionsResponse.ok) throw new Error(await responseError(permissionsResponse, '权限目录加载失败'));
            if (!teamMode && !pagesResponse.ok) throw new Error(await responseError(pagesResponse, '页面目录加载失败'));
            roles = await rolesResponse.json();
            permissionCatalog = await permissionsResponse.json();
            pageCatalog = teamMode ? [] : await pagesResponse.json();
            renderRoles();
        } catch (error) {
            showError(elements.error, error.message || '角色加载失败');
        }
    }

    window.toggleRoleStatus = async function toggleRoleStatus(role) {
        try {
            const response = await window.aiworkerApi.request(`${rolesApi}/${role.id}`, {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({is_enabled: !role.is_enabled}),
            });
            if (!response.ok) throw new Error(await responseError(response, '角色状态更新失败'));
            await loadRolesAndPermissions();
        } catch (error) {
            showError(elements.error, error.message || '角色状态更新失败');
        }
    };

    async function cloneRole(role) {
        const code = window.prompt('新角色代码');
        if (!code) return;
        const name = window.prompt('新角色名称', `${role.name}副本`);
        if (!name) return;
        try {
            const response = await window.aiworkerApi.request(`${rolesApi}/${role.id}/clone`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({code: code.trim(), name: name.trim()}),
            });
            if (!response.ok) throw new Error(await responseError(response, '角色克隆失败'));
            await loadRolesAndPermissions();
        } catch (error) {
            showError(elements.error, error.message || '角色克隆失败');
        }
    }

    async function deleteRole(role) {
        if (!window.confirm(`确定删除角色 ${role.name}？`)) return;
        try {
            const response = await window.aiworkerApi.request(`${rolesApi}/${role.id}`, {method: 'DELETE'});
            if (!response.ok) throw new Error(await responseError(response, '角色删除失败'));
            await loadRolesAndPermissions();
        } catch (error) {
            showError(elements.error, error.message || '角色删除失败');
        }
    }

    async function exportRoles(format) {
        try {
            const parameters = filterParameters();
            parameters.set('format', format);
            const response = await window.aiworkerApi.request(`/api/admin/roles/export?${parameters.toString()}`);
            if (!response.ok) throw new Error(await responseError(response, '角色表导出失败'));
            const link = document.createElement('a');
            link.href = URL.createObjectURL(await response.blob());
            link.download = `角色权限表.${format}`;
            link.click();
            URL.revokeObjectURL(link.href);
        } catch (error) {
            showError(elements.error, error.message || '角色表导出失败');
        }
    }

    elements.form.addEventListener('submit', async (event) => {
        event.preventDefault();
        showError(elements.formError);
        const roleId = elements.id.value;
        const payload = {
            name: elements.name.value.trim(),
            description: elements.description.value.trim(),
            permissions: selectedPermissions(),
            claimable_task_types: elements.claimableTaskTypes.value.split(',').map((value) => value.trim()).filter(Boolean),
        };
        if (!roleId) payload.code = elements.code.value.trim();
        try {
            const response = await window.aiworkerApi.request(
                roleId ? `${rolesApi}/${roleId}` : rolesApi,
                {
                    method: roleId ? 'PATCH' : 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload),
                },
            );
            if (!response.ok) throw new Error(await responseError(response, roleId ? '角色更新失败' : '角色创建失败'));
            editor.hide();
            await loadRolesAndPermissions();
        } catch (error) {
            showError(elements.formError, error.message || '角色保存失败');
        }
    });

    elements.pagesForm?.addEventListener('submit', async (event) => {
        event.preventDefault();
        showError(elements.pagesError);
        if (elements.pagesForm.dataset.systemAdmin === 'true') return;
        const roleId = elements.pagesRoleId.value;
        const page_routes = Array.from(
            elements.pages.querySelectorAll('.role-page-toggle:checked'),
            (toggle) => toggle.value,
        );
        try {
            const response = await window.aiworkerApi.request(`${rolesApi}/${roleId}/pages`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({page_routes}),
            });
            if (!response.ok) throw new Error(await responseError(response, '页面授权保存失败'));
            pagesEditor.hide();
            await loadRolesAndPermissions();
        } catch (error) {
            showError(elements.pagesError, error.message || '页面授权保存失败');
        }
    });

    document.getElementById('create-role')?.addEventListener('click', () => openRoleEditor());
    elements.filters?.addEventListener('submit', (event) => { event.preventDefault(); loadRolesAndPermissions(); });
    document.getElementById('clear-role-filters')?.addEventListener('click', () => { elements.filters.reset(); loadRolesAndPermissions(); });
    document.getElementById('refresh-roles')?.addEventListener('click', loadRolesAndPermissions);
    document.getElementById('export-roles-csv')?.addEventListener('click', () => exportRoles('csv'));
    document.getElementById('export-roles-xlsx')?.addEventListener('click', () => exportRoles('xlsx'));
    loadRolesAndPermissions();
})();
