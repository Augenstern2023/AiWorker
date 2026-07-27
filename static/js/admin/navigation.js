(function () {
    'use strict';

    const body = document.getElementById('navigation-list');
    if (!body) return;

    function textCell(value) {
        const cell = document.createElement('td');
        cell.textContent = value || '';
        return cell;
    }

    function inputCell(value, type = 'text') {
        const cell = document.createElement('td');
        const input = document.createElement('input');
        input.className = 'form-control form-control-sm';
        input.type = type;
        if (type === 'checkbox') input.checked = Boolean(value);
        else input.value = value ?? '';
        cell.appendChild(input);
        return [cell, input];
    }

    function iconButton(icon, label, handler) {
        const button = document.createElement('button');
        button.className = 'btn btn-sm btn-primary';
        button.type = 'button';
        button.title = label;
        button.setAttribute('aria-label', label);
        const glyph = document.createElement('i');
        glyph.className = `bi ${icon}`;
        glyph.setAttribute('aria-hidden', 'true');
        button.appendChild(glyph);
        button.addEventListener('click', handler);
        return button;
    }

    async function requestJson(url, options) {
        const response = await window.aiworkerApi.request(url, options);
        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(data.detail || '请求失败');
        }
        return response.status === 204 ? null : response.json();
    }

    async function load() {
        try {
            const items = await requestJson('/api/admin/navigation');
            body.replaceChildren(...items.map((item) => {
                const row = document.createElement('tr');
                const [labelCell, label] = inputCell(item.label);
                const [iconCell, icon] = inputCell(item.icon);
                const [sortCell, sort] = inputCell(item.sort_order, 'number');
                const [enabledCell, enabled] = inputCell(item.is_enabled, 'checkbox');
                const actionCell = document.createElement('td');
                actionCell.appendChild(iconButton('bi-check-lg', '保存导航配置', async () => {
                    try {
                        await requestJson(`/api/admin/navigation/${encodeURIComponent(item.id)}`, {
                            method: 'PATCH', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({label: label.value, icon: icon.value, sort_order: Number(sort.value), is_enabled: enabled.checked}),
                        });
                        window.AiWorkerToast?.show('导航配置已保存');
                        load();
                    } catch (error) {
                        window.AiWorkerToast?.show(error.message, 'error');
                    }
                }));
                row.append(labelCell, iconCell, textCell(item.route), textCell(item.required_permission), sortCell, enabledCell, actionCell);
                return row;
            }));
        } catch (error) {
            body.replaceChildren();
            const row = document.createElement('tr');
            const cell = document.createElement('td');
            cell.colSpan = 7;
            cell.className = 'text-danger py-3';
            cell.textContent = error.message;
            row.appendChild(cell);
            body.appendChild(row);
        }
    }

    document.getElementById('navigation-refresh')?.addEventListener('click', load);
    load();
})();
