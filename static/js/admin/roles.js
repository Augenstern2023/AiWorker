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
        permissions: document.getElementById('permission-list'),
    };
    if (!elements.body) return;

    const editor = bootstrap.Modal.getOrCreateInstance(document.getElementById('role-editor-modal'));
    let roles = [];
    let permissionCatalog = [];

    function showError(target, message = '') {
        target.textContent = message;
        target.classList.toggle('d-none', !message);
    }

    async function responseError(response, fallback) {
        const data = await response.json().catch(() => ({}));
        return data.detail || data.message || fallback;
    }

    function actionButton(icon, label, style, handler, disabled = false) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `btn btn-sm ${style}`;
        button.title = label;
        button.setAttribute('aria-label', label);
        button.innerHTML = `<i class="bi ${icon}"></i>`;
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
            for (const [value, label] of [['task', '本人任务'], ['project', '所在项目'], ['managed_projects', '负责项目'], ['all', '全部数据']]) {
                const option = document.createElement('option');
                option.value = value;
                option.textContent = label;
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

    function openRoleEditor(role = null) {
        elements.form.reset();
        showError(elements.formError);
        elements.id.value = role?.id || '';
        elements.title.textContent = role ? '编辑角色权限' : '新建角色';
        elements.code.value = role?.code || '';
        elements.code.disabled = Boolean(role);
        elements.name.value = role?.name || '';
        elements.description.value = role?.description || '';
        renderPermissionEditor(role?.permissions || []);
        editor.show();
    }

    function renderRoles() {
        if (!roles.length) {
            const row = document.createElement('tr');
            row.innerHTML = '<td colspan="5" class="text-center text-muted py-4">暂无角色</td>';
            elements.body.replaceChildren(row);
            return;
        }
        elements.body.replaceChildren(...roles.map((role) => {
            const row = document.createElement('tr');
            for (const value of [role.name, role.code, String((role.permissions || []).length), role.is_enabled ? '启用' : '停用']) {
                const cell = document.createElement('td');
                cell.textContent = value;
                row.appendChild(cell);
            }
            const actions = document.createElement('td');
            const group = document.createElement('div');
            group.className = 'd-flex gap-1';
            group.append(
                actionButton('bi-pencil', '编辑权限', 'btn-outline-light', () => openRoleEditor(role)),
                actionButton('bi-copy', '克隆角色', 'btn-outline-info', () => cloneRole(role)),
                actionButton(
                    role.is_enabled ? 'bi-toggle-off' : 'bi-toggle-on',
                    role.is_enabled ? '停用角色' : '启用角色',
                    role.is_enabled ? 'btn-outline-warning' : 'btn-outline-success',
                    () => window.toggleRoleStatus(role),
                    role.is_system,
                ),
            );
            actions.appendChild(group);
            row.appendChild(actions);
            return row;
        }));
    }

    async function loadRolesAndPermissions() {
        showError(elements.error);
        try {
            const [rolesResponse, permissionsResponse] = await Promise.all([
                window.aiworkerApi.request('/api/admin/roles'),
                window.aiworkerApi.request('/api/admin/permissions'),
            ]);
            if (!rolesResponse.ok) throw new Error(await responseError(rolesResponse, '角色加载失败'));
            if (!permissionsResponse.ok) throw new Error(await responseError(permissionsResponse, '权限目录加载失败'));
            roles = await rolesResponse.json();
            permissionCatalog = await permissionsResponse.json();
            renderRoles();
        } catch (error) {
            showError(elements.error, error.message || '角色加载失败');
        }
    }

    window.toggleRoleStatus = async function toggleRoleStatus(role) {
        try {
            const response = await window.aiworkerApi.request(`/api/admin/roles/${role.id}`, {
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
            const response = await window.aiworkerApi.request(`/api/admin/roles/${role.id}/clone`, {
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

    elements.form.addEventListener('submit', async (event) => {
        event.preventDefault();
        showError(elements.formError);
        const roleId = elements.id.value;
        const payload = {
            name: elements.name.value.trim(),
            description: elements.description.value.trim(),
            permissions: selectedPermissions(),
        };
        if (!roleId) payload.code = elements.code.value.trim();
        try {
            const response = await window.aiworkerApi.request(
                roleId ? `/api/admin/roles/${roleId}` : '/api/admin/roles',
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

    document.getElementById('create-role')?.addEventListener('click', () => openRoleEditor());
    loadRolesAndPermissions();
})();
