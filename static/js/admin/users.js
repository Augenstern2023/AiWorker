(function () {
    'use strict';

    const elements = {
        body: document.getElementById('user-list'),
        error: document.getElementById('user-error'),
        formError: document.getElementById('user-form-error'),
        form: document.getElementById('user-editor-form'),
        title: document.getElementById('user-editor-title'),
        id: document.getElementById('user-id'),
        username: document.getElementById('user-username'),
        displayName: document.getElementById('user-display-name'),
        password: document.getElementById('user-password'),
        passwordField: document.getElementById('user-password-field'),
        roles: document.getElementById('user-role-select'),
    };
    if (!elements.body) return;

    const editor = bootstrap.Modal.getOrCreateInstance(document.getElementById('user-editor-modal'));
    const teamMode = window.location.pathname.startsWith('/review/');
    const usersApi = teamMode ? '/api/team/users' : '/api/admin/users';
    const rolesApi = teamMode ? '/api/team/roles' : '/api/admin/roles';
    let users = [];
    let roles = [];

    function showError(target, message = '') {
        target.textContent = message;
        target.classList.toggle('d-none', !message);
    }

    async function responseError(response, fallback) {
        const data = await response.json().catch(() => ({}));
        return data.detail || data.message || fallback;
    }

    function selectedRoleIds() {
        return Array.from(elements.roles.selectedOptions, (option) => option.value);
    }

    function actionButton(icon, label, style, handler) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `btn btn-sm ${style}`;
        button.title = label;
        button.setAttribute('aria-label', label);
        button.innerHTML = `<i class="bi ${icon}"></i>`;
        button.addEventListener('click', handler);
        return button;
    }

    function renderRoleOptions(selectedCodes = []) {
        const selected = new Set(selectedCodes);
        const availableRoles = teamMode ? roles.filter((role) => role.can_assign) : roles;
        elements.roles.replaceChildren(...availableRoles.map((role) => {
            const option = document.createElement('option');
            option.value = role.id;
            option.textContent = `${role.name} (${role.code})`;
            option.selected = selected.has(role.code);
            return option;
        }));
    }

    function roleDisplayName(roleCode) {
        return roles.find((role) => role.code === roleCode)?.name || roleCode;
    }

    function openUserEditor(user = null) {
        elements.form.reset();
        showError(elements.formError);
        elements.id.value = user?.id || '';
        elements.title.textContent = user ? '编辑用户' : '新建用户';
        elements.username.value = user?.username || '';
        elements.username.disabled = Boolean(user);
        elements.displayName.value = user?.display_name || '';
        elements.password.required = !user;
        elements.passwordField.classList.toggle('d-none', Boolean(user));
        renderRoleOptions(user?.role_codes || []);
        editor.show();
    }

    function renderUsers() {
        if (!users.length) {
            const row = document.createElement('tr');
            row.innerHTML = '<td colspan="6" class="text-center text-muted py-4">暂无用户</td>';
            elements.body.replaceChildren(row);
            return;
        }
        elements.body.replaceChildren(...users.map((user) => {
            const row = document.createElement('tr');
            const values = [
                user.username,
                user.display_name,
                (user.role_codes || []).map(roleDisplayName).join('、') || '-',
                user.status === 'active' ? '启用' : '停用',
                user.last_login_at || '-',
            ];
            values.forEach((value) => {
                const cell = document.createElement('td');
                cell.textContent = value;
                row.appendChild(cell);
            });
            const actions = document.createElement('td');
            const group = document.createElement('div');
            group.className = 'd-flex gap-1';
            if (user.can_edit !== false) {
                group.append(
                    actionButton('bi-pencil', '编辑用户', 'btn-outline-light', () => openUserEditor(user)),
                    actionButton(
                    user.status === 'active' ? 'bi-person-dash' : 'bi-person-check',
                    user.status === 'active' ? '停用用户' : '启用用户',
                    user.status === 'active' ? 'btn-outline-warning' : 'btn-outline-success',
                    () => window.toggleUserStatus(user),
                    ),
                );
            }
            if (user.can_reset_password !== false) {
                group.append(actionButton('bi-key', '重置密码', 'btn-outline-info', () => resetUserPassword(user)));
            }
            if (user.can_delete) {
                group.append(actionButton('bi-trash', '删除用户', 'btn-outline-danger', () => deleteUser(user)));
            }
            actions.appendChild(group);
            row.appendChild(actions);
            return row;
        }));
    }

    async function loadUsersAndRoles() {
        showError(elements.error);
        try {
            const [usersResponse, rolesResponse] = await Promise.all([
                window.aiworkerApi.request(usersApi),
                window.aiworkerApi.request(rolesApi),
            ]);
            if (!usersResponse.ok) throw new Error(await responseError(usersResponse, '用户加载失败'));
            if (!rolesResponse.ok) throw new Error(await responseError(rolesResponse, '角色加载失败'));
            users = await usersResponse.json();
            roles = await rolesResponse.json();
            renderUsers();
        } catch (error) {
            showError(elements.error, error.message || '用户加载失败');
        }
    }

    window.toggleUserStatus = async function toggleUserStatus(user) {
        const status = user.status === 'active' ? 'disabled' : 'active';
        try {
            const response = await window.aiworkerApi.request(`${usersApi}/${user.id}`, {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({status}),
            });
            if (!response.ok) throw new Error(await responseError(response, '状态更新失败'));
            await loadUsersAndRoles();
        } catch (error) {
            showError(elements.error, error.message || '状态更新失败');
        }
    };

    async function resetUserPassword(user) {
        const password = window.prompt(`为 ${user.username} 设置新密码`);
        if (!password) return;
        try {
            // Admin equivalent: /api/admin/users/${user.id}/reset-password
            const response = await window.aiworkerApi.request(`${usersApi}/${user.id}/reset-password`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({password}),
            });
            if (!response.ok) throw new Error(await responseError(response, '密码重置失败'));
            showError(elements.error);
            window.alert('密码已重置，该用户现有会话已退出');
        } catch (error) {
            showError(elements.error, error.message || '密码重置失败');
        }
    }

    async function deleteUser(user) {
        if (!window.confirm(`确定删除账号 ${user.username}？`)) return;
        try {
            const response = await window.aiworkerApi.request(`${usersApi}/${user.id}`, {method: 'DELETE'});
            if (!response.ok) throw new Error(await responseError(response, '用户删除失败'));
            await loadUsersAndRoles();
        } catch (error) {
            showError(elements.error, error.message || '用户删除失败');
        }
    }

    elements.form.addEventListener('submit', async (event) => {
        event.preventDefault();
        showError(elements.formError);
        const userId = elements.id.value;
        const role_ids = selectedRoleIds();
        const payload = userId
            ? {display_name: elements.displayName.value.trim(), role_ids}
            : {
                username: elements.username.value.trim(),
                display_name: elements.displayName.value.trim(),
                password: elements.password.value,
                role_ids,
            };
        try {
            const response = await window.aiworkerApi.request(
                userId ? `${usersApi}/${userId}` : usersApi,
                {
                    method: userId ? 'PATCH' : 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload),
                },
            );
            if (!response.ok) throw new Error(await responseError(response, userId ? '用户更新失败' : '用户创建失败'));
            editor.hide();
            await loadUsersAndRoles();
        } catch (error) {
            showError(elements.formError, error.message || '用户保存失败');
        }
    });

    document.getElementById('create-user')?.addEventListener('click', () => openUserEditor());
    loadUsersAndRoles();
})();
