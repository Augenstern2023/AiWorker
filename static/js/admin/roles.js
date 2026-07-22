(async function () {
    const body = document.getElementById('role-list');
    if (!body) return;
    try {
        const response = await window.aiworkerApi.request('/api/admin/roles');
        const roles = await response.json();
        body.replaceChildren(...roles.map((role) => {
            const row = document.createElement('tr');
            for (const value of [role.name, role.code, String((role.permissions || []).length), role.is_enabled ? '启用' : '停用']) {
                const cell = document.createElement('td');
                cell.textContent = value;
                row.appendChild(cell);
            }
            const actions = document.createElement('td');
            actions.textContent = role.is_system ? '系统角色' : '可编辑';
            row.appendChild(actions);
            return row;
        }));
    } catch (error) {
        body.firstElementChild.firstElementChild.textContent = error.message || '角色加载失败';
    }
})();
