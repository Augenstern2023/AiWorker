(async function () {
    const body = document.getElementById('audit-list');
    if (!body) return;
    const filters = document.getElementById('audit-filters');

    let offset = 0;
    const pageSize = 50;
    let total = 0;

    function filterParameters() {
        const params = new URLSearchParams({limit: String(pageSize), offset: String(offset)});
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
            total = Number(response.headers.get('X-Total-Count') || 0);
            document.getElementById('audit-total').textContent = `共 ${total} 条，第 ${Math.floor(offset / pageSize) + 1} 页`;
            document.getElementById('audit-previous').disabled = offset === 0;
            document.getElementById('audit-next').disabled = offset + events.length >= total;
            body.replaceChildren(...events.map((event) => {
                const row = document.createElement('tr');
                for (const value of [event.created_at || '-', event.actor_display_name || event.actor_user_id || '-', event.action || '-', `${event.target_type || '-'} ${event.target_id || ''}`.trim(), event.result || '-']) {
                    const cell = document.createElement('td');
                    cell.textContent = value;
                    row.appendChild(cell);
                }
                row.tabIndex = 0;
                row.className = 'cursor-pointer';
                row.addEventListener('click', () => showDetails(event.id));
                return row;
            }));
        } catch (error) {
            body.firstElementChild.firstElementChild.textContent = error.message || '审计日志加载失败';
        }
    }

    function detailValue(value) { return value === null || value === undefined ? '-' : (typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)); }
    async function showDetails(eventId) { try { const response = await window.aiworkerApi.request(`/api/admin/audit/${encodeURIComponent(eventId)}`); if (!response.ok) throw new Error('审计详情加载失败'); const event = await response.json(); const fields = document.getElementById('audit-details-fields'); fields.replaceChildren(); for (const [label, key] of [['时间', 'created_at'], ['操作人', 'actor_display_name'], ['动作', 'action'], ['目标', 'target_id'], ['项目', 'project_id'], ['结果', 'result'], ['原因', 'reason'], ['变更前', 'before_summary'], ['变更后', 'after_summary']]) { const term = document.createElement('dt'); term.className = 'col-sm-3'; term.textContent = label; const definition = document.createElement('dd'); definition.className = 'col-sm-9 text-break'; definition.textContent = detailValue(event[key]); fields.append(term, definition); } document.getElementById('audit-details').classList.remove('d-none'); } catch (error) { body.firstElementChild.firstElementChild.textContent = error.message || '审计详情加载失败'; } }

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
        offset = 0;
        loadAudit();
    });
    document.getElementById('audit-previous')?.addEventListener('click', () => { offset = Math.max(0, offset - pageSize); loadAudit(); });
    document.getElementById('audit-next')?.addEventListener('click', () => { if (offset + pageSize < total) { offset += pageSize; loadAudit(); } });
    document.getElementById('audit-details-close')?.addEventListener('click', () => document.getElementById('audit-details').classList.add('d-none'));
    document.getElementById('export-audit-csv')?.addEventListener('click', () => exportAudit('csv'));
    document.getElementById('export-audit-xlsx')?.addEventListener('click', () => exportAudit('xlsx'));
    await loadAudit();
})();
