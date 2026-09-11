(function () {
    'use strict';

    const elements = {
        body: document.getElementById('user-list'), error: document.getElementById('user-error'),
        formError: document.getElementById('user-form-error'), form: document.getElementById('user-editor-form'),
        title: document.getElementById('user-editor-title'), id: document.getElementById('user-id'),
        username: document.getElementById('user-username'), displayName: document.getElementById('user-display-name'),
        password: document.getElementById('user-password'), passwordField: document.getElementById('user-password-field'),
        roles: document.getElementById('user-role-select'), email: document.getElementById('user-email'),
        phone: document.getElementById('user-phone'), gender: document.getElementById('user-gender'),
        filters: document.getElementById('user-filters'), filterRole: document.getElementById('user-filter-role'),
        filterDepartment: document.getElementById('user-filter-department'),
    };
    if (!elements.body) return;

    const editor = bootstrap.Modal.getOrCreateInstance(document.getElementById('user-editor-modal'));
    const usersApi = '/api/admin/users';
    const rolesApi = '/api/admin/roles';
    const presenceEnabled = elements.body.dataset.presenceEnabled === 'true';
    const genderLabels = { unspecified: '未填写', male: '男', female: '女' };
    let users = [];
    let roles = [];
    let departments = [];
    let presenceByUser = {};
    let presenceAvailable = false;
    let lastPresenceRefreshAt = null;
    let presenceRequestInFlight = false;

    function showError(target, message = '') { target.textContent = message; target.classList.toggle('d-none', !message); }
    async function responseError(response, fallback) { const data = await response.json().catch(() => ({})); return data.detail || data.message || fallback; }
    function selectedRoleIds() { return Array.from(elements.roles.selectedOptions, option => option.value); }
    function filterParameters() { return new URLSearchParams(new FormData(elements.filters)); }
    function formatDate(value) { return value ? String(value).replace('T', ' ').replace(/\.\d+(?=(?:Z|[+-]\d\d(?::?\d\d)?$))/, '').replace(/(?:Z|[+-]\d\d(?::?\d\d)?)$/, '') : '-'; }
    function actionButton(icon, label, style, handler) { const button = document.createElement('button'); button.type = 'button'; button.className = `btn btn-sm ${style}`; button.title = label; button.setAttribute('aria-label', label); const glyph = document.createElement('i'); glyph.className = `bi ${icon}`; button.appendChild(glyph); button.addEventListener('click', handler); return button; }
    function flatten(items, output = []) { for (const item of items) { output.push(item); flatten(item.children || [], output); } return output; }
    function renderRoleOptions(selectedCodes = []) { const selected = new Set(selectedCodes); elements.roles.replaceChildren(...roles.map(role => { const option = new Option(`${role.name} (${role.code})`, role.id); option.selected = selected.has(role.code); return option; })); }
    function renderFilterOptions() {
        const selectedRole = elements.filterRole.value;
        elements.filterRole.replaceChildren(new Option('全部角色', ''), ...roles.map(role => new Option(role.name, role.code)));
        elements.filterRole.value = selectedRole;
        const selectedDepartment = elements.filterDepartment.value;
        elements.filterDepartment.replaceChildren(new Option('全部部门', ''), ...flatten(departments).map(item => new Option(`${'　'.repeat(item.depth || 0)}${item.name}`, item.id)));
        elements.filterDepartment.value = selectedDepartment;
    }
    function roleDisplayName(roleCode) { return roles.find(role => role.code === roleCode)?.name || roleCode; }
    function departmentDisplay(user) { const primary = user.primary_department?.name; const extra = Math.max(0, (user.departments || []).length - (primary ? 1 : 0)); return primary ? `${primary}${extra ? ` +${extra}` : ''}` : '-'; }
    function presenceCell(user) {
        const cell = document.createElement('td');
        cell.className = 'presence-column';
        const presence = presenceAvailable ? presenceByUser[user.id] : null;
        const status = presence?.status || 'unknown';
        const statusLabels = {active: '活跃', idle: '闲置', offline: '离线', unknown: '状态未知'};
        const statusColors = {active: 'text-success', idle: 'text-warning', offline: 'text-secondary', unknown: 'text-secondary'};
        const summary = document.createElement('div');
        summary.className = 'd-flex align-items-center gap-1 text-nowrap';
        const dot = document.createElement('span');
        dot.className = statusColors[status] || 'text-secondary';
        dot.textContent = '●';
        dot.setAttribute('aria-hidden', 'true');
        const label = document.createElement('span');
        label.textContent = statusLabels[status] || '状态未知';
        summary.append(dot, label);
        if (presence?.device_count > 1) {
            const devices = document.createElement('span');
            devices.className = 'small text-muted';
            devices.textContent = `· ${presence.device_count}台`;
            summary.appendChild(devices);
        }

        const detail = document.createElement('div');
        detail.className = 'small text-muted presence-detail';
        if (status === 'active' || status === 'idle') {
            detail.textContent = presence.page || '工作台';
        } else if (status === 'offline') {
            detail.textContent = presence.last_online_at ? `最后在线 ${formatDate(presence.last_online_at)}` : '从未上线';
        } else {
            detail.textContent = lastPresenceRefreshAt
                ? `监测暂不可用 · 上次 ${formatDate(lastPresenceRefreshAt)}`
                : '监测暂不可用';
        }
        cell.title = `${label.textContent}｜${detail.textContent}`;
        cell.append(summary, detail);
        return cell;
    }
    function openUserEditor(user = null) {
        elements.form.reset(); showError(elements.formError); elements.id.value = user?.id || ''; elements.title.textContent = user ? '编辑用户' : '新建用户';
        elements.username.value = user?.username || ''; elements.username.disabled = Boolean(user); elements.displayName.value = user?.display_name || '';
        elements.email.value = user?.email || ''; elements.phone.value = user?.phone || ''; elements.gender.value = user?.gender || 'unspecified';
        elements.password.required = !user; elements.passwordField.classList.toggle('d-none', Boolean(user)); renderRoleOptions(user?.role_codes || []); editor.show();
    }
    function renderUsers() {
        if (!users.length) { const row = document.createElement('tr'); const cell = document.createElement('td'); cell.colSpan = presenceEnabled ? 10 : 9; cell.className = 'text-center text-muted py-4'; cell.textContent = '暂无用户'; row.appendChild(cell); elements.body.replaceChildren(row); return; }
        const statusOrder = {active: 0, idle: 1, offline: 2, unknown: 3};
        const displayUsers = presenceEnabled && presenceAvailable
            ? [...users].sort((left, right) => (statusOrder[presenceByUser[left.id]?.status || 'unknown'] - statusOrder[presenceByUser[right.id]?.status || 'unknown']))
            : users;
        elements.body.replaceChildren(...displayUsers.map(user => {
            const row = document.createElement('tr');
            const values = [user.username, departmentDisplay(user), user.email || '-', user.phone || '-', genderLabels[user.gender] || '未填写', (user.role_codes || []).map(roleDisplayName).join('、') || '-', formatDate(user.created_at), user.status === 'active' ? '启用' : '停用'];
            values.forEach(value => { const cell = document.createElement('td'); cell.textContent = value; row.appendChild(cell); });
            if (presenceEnabled) row.appendChild(presenceCell(user));
            const actions = document.createElement('td'); const group = document.createElement('div'); group.className = 'd-flex gap-1';
            if (user.can_edit !== false) group.append(actionButton('bi-pencil', '编辑用户', 'btn-outline-light', () => openUserEditor(user)), actionButton('bi-diagram-3', '编辑部门归属', 'btn-outline-info', () => { window.location.assign(`/admin/departments?user_id=${encodeURIComponent(user.id)}`); }), actionButton(user.status === 'active' ? 'bi-person-dash' : 'bi-person-check', user.status === 'active' ? '停用用户' : '启用用户', user.status === 'active' ? 'btn-outline-warning' : 'btn-outline-success', () => toggleUserStatus(user)));
            if (user.can_reset_password !== false) group.append(actionButton('bi-key', '重置密码', 'btn-outline-info', () => resetUserPassword(user)));
            if (user.can_delete) group.append(actionButton('bi-trash', '删除用户', 'btn-outline-danger', () => deleteUser(user)));
            actions.appendChild(group); row.appendChild(actions); return row;
        }));
    }
    async function loadUsersAndRoles() {
        showError(elements.error);
        try {
            const requests = [window.aiworkerApi.request(`${usersApi}?${filterParameters().toString()}`), window.aiworkerApi.request(rolesApi), window.aiworkerApi.request('/api/admin/departments')];
            const responses = await Promise.all(requests);
            if (!responses.every(response => response.ok)) throw new Error('用户、角色或部门加载失败');
            users = await responses[0].json(); roles = await responses[1].json(); departments = await responses[2].json(); renderFilterOptions(); renderUsers();
            if (presenceEnabled) void loadPresence();
        } catch (error) { showError(elements.error, error.message || '用户加载失败'); }
    }
    async function loadPresence() {
        if (!presenceEnabled || document.hidden || presenceRequestInFlight) return;
        presenceRequestInFlight = true;
        const controller = new AbortController();
        const timeoutId = window.setTimeout(() => controller.abort(), 4000);
        try {
            const response = await window.aiworkerApi.request('/api/admin/presence', {signal: controller.signal});
            if (!response.ok) throw new Error(await responseError(response, '在线情况加载失败'));
            const payload = await response.json();
            presenceByUser = payload.users || {};
            presenceAvailable = true;
            lastPresenceRefreshAt = payload.refreshed_at || new Date().toISOString();
        } catch (_) {
            presenceAvailable = false;
        } finally {
            window.clearTimeout(timeoutId);
            presenceRequestInFlight = false;
            renderUsers();
        }
    }
    async function toggleUserStatus(user) { const status = user.status === 'active' ? 'disabled' : 'active'; try { const response = await window.aiworkerApi.request(`${usersApi}/${user.id}`, { method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({status}) }); if (!response.ok) throw new Error(await responseError(response, '状态更新失败')); await loadUsersAndRoles(); } catch (error) { showError(elements.error, error.message || '状态更新失败'); } }
    async function resetUserPassword(user) { const password = window.prompt(`为 ${user.username} 设置新密码\n密码需为 10 至 128 个字符，并包含字母、数字和特殊字符`); if (!password) return; try { const response = await window.aiworkerApi.request(`${usersApi}/${user.id}/reset-password`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({password}) }); if (!response.ok) throw new Error(await responseError(response, '密码重置失败')); window.alert('密码已重置，该用户现有会话已退出'); } catch (error) { showError(elements.error, error.message || '密码重置失败'); } }
    async function deleteUser(user) {
        const message = `确定删除账号「${user.username}」（${user.display_name}）吗？\n\n删除后该用户将无法登录系统，且无法恢复。`;
        if (!window.confirm(message)) return;
        try { const response = await window.aiworkerApi.request(`${usersApi}/${user.id}`, {method: 'DELETE'});
            if (!response.ok) throw new Error(await responseError(response, '用户删除失败'));
            await loadUsersAndRoles();
            if (window.aiworkerUi) window.aiworkerUi.showToast({kind: 'success', title: '删除成功', message: `用户「${user.username}」已删除。`});
        } catch (error) { showError(elements.error, error.message || '用户删除失败'); if (window.aiworkerUi) window.aiworkerUi.showToast({kind: 'danger', title: '删除失败', message: error.message || '用户删除失败'}); }
    }
    async function exportUsers(format) { try { const response = await window.aiworkerApi.request(`/api/admin/users/export?${filterParameters().toString()}&format=${format}`); if (!response.ok) throw new Error(await responseError(response, '人员表导出失败')); const link = document.createElement('a'); link.href = URL.createObjectURL(await response.blob()); link.download = `用户人员表.${format}`; link.click(); URL.revokeObjectURL(link.href); } catch (error) { showError(elements.error, error.message || '人员表导出失败'); } }
    elements.form.addEventListener('submit', async event => { event.preventDefault(); showError(elements.formError); const userId = elements.id.value; const submitBtn = document.getElementById('save-user'); if (window.aiworkerUi) window.aiworkerUi.setButtonLoading(submitBtn, {loading: true, label: '保存中…'}); const payload = userId ? {display_name: elements.displayName.value.trim(), role_ids: selectedRoleIds()} : {username: elements.username.value.trim(), display_name: elements.displayName.value.trim(), password: elements.password.value, role_ids: selectedRoleIds()}; Object.assign(payload, {email: elements.email.value.trim() || null, phone: elements.phone.value.trim() || null, gender: elements.gender.value}); try { const response = await window.aiworkerApi.request(userId ? `${usersApi}/${userId}` : usersApi, {method: userId ? 'PATCH' : 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)}); if (!response.ok) throw new Error(await responseError(response, userId ? '用户更新失败' : '用户创建失败')); editor.hide(); if (window.aiworkerUi) window.aiworkerUi.showToast({kind: 'success', title: '保存成功', message: '用户已保存。'}); await loadUsersAndRoles(); } catch (error) { showError(elements.formError, error.message || '用户保存失败'); if (window.aiworkerUi) window.aiworkerUi.showToast({kind: 'danger', title: '保存失败', message: error.message || '用户保存失败'}); } finally { if (window.aiworkerUi) window.aiworkerUi.setButtonLoading(submitBtn, {loading: false}); } });
    elements.filters?.addEventListener('submit', event => { event.preventDefault(); loadUsersAndRoles(); });
    document.getElementById('create-user')?.addEventListener('click', () => openUserEditor());
    document.getElementById('export-users-csv')?.addEventListener('click', () => exportUsers('csv'));
    document.getElementById('export-users-xlsx')?.addEventListener('click', () => exportUsers('xlsx'));
    if (presenceEnabled) {
        window.setInterval(() => { void loadPresence(); }, 5000);
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) void loadPresence();
        });
    }
    loadUsersAndRoles();
})();
