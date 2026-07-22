(async function () {
    const body = document.getElementById('user-list');
    if (!body) return;
    try {
        const response = await window.aiworkerApi.request('/api/admin/users');
        const users = await response.json();
        body.replaceChildren(...users.map((user) => {
            const row = document.createElement('tr');
            for (const value of [user.username, user.display_name, (user.role_codes || []).join('、') || '-', user.status, user.last_login_at || '-']) {
                const cell = document.createElement('td');
                cell.textContent = value;
                row.appendChild(cell);
            }
            const actions = document.createElement('td');
            actions.textContent = user.status === 'active' ? '已启用' : '已停用';
            row.appendChild(actions);
            return row;
        }));
    } catch (error) {
        body.firstElementChild.firstElementChild.textContent = error.message || '用户加载失败';
    }
})();
