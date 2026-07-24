(async function () {
    'use strict';

    const form = document.getElementById('delivery-create-form');
    const submissionSelect = document.getElementById('delivery-submission');
    const formatSelect = document.getElementById('delivery-format');
    const history = document.getElementById('delivery-history');
    if (!form || !submissionSelect || !formatSelect || !history) return;

    const stageLabels = {brief: 'Brief', characters: '人物关系', outline: '分集大纲', beats: 'Beats'};

    function statusMessage(message) {
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = 7;
        cell.className = 'text-center text-muted py-4';
        cell.textContent = message;
        row.appendChild(cell);
        history.replaceChildren(row);
    }

    async function loadOptions() {
        const response = await window.aiworkerApi.request('/api/admin/deliveries/options');
        if (!response.ok) throw new Error('已完成版本加载失败');
        const {options} = await response.json();
        submissionSelect.replaceChildren();
        const empty = document.createElement('option');
        empty.value = '';
        empty.textContent = options.length ? '请选择版本' : '暂无已完成版本';
        submissionSelect.appendChild(empty);
        for (const item of options) {
            const option = document.createElement('option');
            option.value = item.resource_id;
            option.dataset.projectId = item.project_id;
            option.dataset.stage = item.stage;
            option.dataset.workflowTaskId = item.workflow_task_id;
            option.textContent = `${item.project_name} / ${stageLabels[item.stage] || item.stage} / v${item.resource_version}`;
            submissionSelect.appendChild(option);
        }
    }

    async function loadHistory() {
        const response = await window.aiworkerApi.request('/api/admin/deliveries');
        if (!response.ok) throw new Error('交付记录加载失败');
        const {deliveries} = await response.json();
        if (!deliveries.length) {
            statusMessage('暂无交付记录');
            return;
        }
        history.replaceChildren(...deliveries.map((delivery) => {
            const row = document.createElement('tr');
            const values = [
                new Date(delivery.created_at * 1000).toLocaleString(),
                delivery.project_name,
                stageLabels[delivery.stage] || delivery.stage,
                `资源 v${delivery.resource_version} / 快照 v${delivery.submission_version}`,
                delivery.format.toUpperCase(),
                delivery.fingerprint.slice(0, 12),
            ];
            for (const value of values) {
                const cell = document.createElement('td');
                cell.textContent = value;
                row.appendChild(cell);
            }
            const action = document.createElement('td');
            const link = document.createElement('a');
            link.className = 'btn btn-outline-primary btn-sm';
            link.href = `/api/admin/deliveries/${delivery.id}/download`;
            link.title = '下载';
            link.innerHTML = '<i class="bi bi-download"></i>';
            action.appendChild(link);
            row.appendChild(action);
            return row;
        }));
    }

    async function refresh() {
        try {
            await Promise.all([loadOptions(), loadHistory()]);
        } catch (error) {
            statusMessage(error.message || '成果交付加载失败');
        }
    }

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const option = submissionSelect.selectedOptions[0];
        if (!option?.value) return;
        const response = await window.aiworkerApi.request('/api/admin/deliveries', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                resource_id: option.value,
                project_id: Number(option.dataset.projectId),
                stage: option.dataset.stage,
                format: formatSelect.value,
                workflow_task_id: option.dataset.workflowTaskId,
            }),
        });
        if (!response.ok) {
            const body = await response.json().catch(() => ({}));
            statusMessage(body.detail || '交付生成失败');
            return;
        }
        await refresh();
    });
    document.getElementById('refresh-deliveries')?.addEventListener('click', refresh);
    await refresh();
})();
