(async function () {
    const body = document.getElementById('audit-list');
    if (!body) return;

    async function loadAudit() {
        try {
            const response = await window.aiworkerApi.request('/api/admin/audit?limit=100');
            const events = await response.json();
            body.replaceChildren(...events.map((event) => {
                const row = document.createElement('tr');
                for (const value of [event.created_at || '-', event.actor_username || event.actor_user_id || '-', event.action || '-', `${event.target_type || '-'} ${event.target_id || ''}`.trim(), event.result || '-']) {
                    const cell = document.createElement('td');
                    cell.textContent = value;
                    row.appendChild(cell);
                }
                return row;
            }));
        } catch (error) {
            body.firstElementChild.firstElementChild.textContent = error.message || '审计日志加载失败';
        }
    }

    document.getElementById('refresh-audit')?.addEventListener('click', loadAudit);
    await loadAudit();
})();
