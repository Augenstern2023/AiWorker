(async function () {
    const body = document.getElementById('audit-list');
    if (!body) return;
    const filters = document.getElementById('audit-filters');

    function filterParameters() {
        const params = new URLSearchParams({limit: '100'});
        for (const [name, rawValue] of new FormData(filters).entries()) {
            const value = String(rawValue).trim();
            if (!value) continue;
            params.set(name, name.startsWith('created_') ? value.replace('T', ' ') + ':00' : value);
        }
        return params;
    }

    async function loadAudit() {
        try {
            const response = await window.aiworkerApi.request(`/api/admin/audit?${filterParameters().toString()}`);
            if (!response.ok) throw new Error('审计日志加载失败');
            const events = await response.json();
            body.replaceChildren(...events.map((event) => {
                const row = document.createElement('tr');
                for (const value of [event.created_at || '-', event.actor_display_name || event.actor_user_id || '-', event.action || '-', `${event.target_type || '-'} ${event.target_id || ''}`.trim(), event.result || '-']) {
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

    async function exportAudit(format) {
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
            body.firstElementChild.firstElementChild.textContent = error.message || '审计日志导出失败';
        }
    }

    document.getElementById('refresh-audit')?.addEventListener('click', loadAudit);
    filters?.addEventListener('submit', (event) => {
        event.preventDefault();
        loadAudit();
    });
    document.getElementById('export-audit-csv')?.addEventListener('click', () => exportAudit('csv'));
    document.getElementById('export-audit-xlsx')?.addEventListener('click', () => exportAudit('xlsx'));
    await loadAudit();
})();
