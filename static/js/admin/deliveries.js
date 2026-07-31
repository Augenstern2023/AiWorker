(async function () {
    'use strict';

    const resources = document.getElementById('generated-resource-list');
    if (!resources) return;

    const stageLabels = {brief: 'Brief', characters: '人物关系', outline: '分集大纲', beats: 'Beats', script: '场次剧本'};
    const resourceStatusLabels = {draft: '草稿', locked: '已锁定', brief_locked: '已锁定', approved: '已锁定', submitted: '已提交', returned: '已退回'};

    function statusMessage(message) {
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = 6;
        cell.className = 'text-center text-muted py-4';
        cell.textContent = message;
        row.appendChild(cell);
        resources.replaceChildren(row);
    }

    function formatsFor(resource) {
        return resource.stage === 'script'
            ? [['txt', 'TXT'], ['json', 'JSON']]
            : [['md', 'Markdown'], ['txt', 'TXT'], ['json', 'JSON']];
    }

    function downloadUrl(resource, format) {
        const params = new URLSearchParams({
            stage: resource.stage,
            resource_id: resource.resource_id,
            format,
        });
        return `/api/creative/projects/${resource.project_id}/generated-content/download?${params.toString()}`;
    }

    function resourceRow(resource) {
        const row = document.createElement('tr');
        const values = [
            resource.project_name,
            stageLabels[resource.stage] || resource.stage,
            `v${resource.resource_version}`,
            resourceStatusLabels[resource.resource_status] || resource.resource_status || '-',
        ];
        for (const value of values) {
            const cell = document.createElement('td');
            cell.textContent = value;
            row.appendChild(cell);
        }

        const formatCell = document.createElement('td');
        const format = document.createElement('select');
        format.className = 'form-select form-select-sm';
        format.setAttribute('aria-label', `${resource.project_name} ${stageLabels[resource.stage] || resource.stage} 下载格式`);
        for (const [value, label] of formatsFor(resource)) {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = label;
            format.appendChild(option);
        }
        formatCell.appendChild(format);
        row.appendChild(formatCell);

        const action = document.createElement('td');
        const link = document.createElement('a');
        link.className = 'btn btn-outline-primary btn-sm';
        link.title = '下载';
        link.innerHTML = '<i class="bi bi-download"></i>';
        link.href = downloadUrl(resource, format.value);
        format.addEventListener('change', () => {
            link.href = downloadUrl(resource, format.value);
        });
        action.appendChild(link);
        row.appendChild(action);
        return row;
    }

    async function refresh() {
        try {
            const response = await window.aiworkerApi.request('/api/creative/generated-resources');
            if (!response.ok) throw new Error('已生成成果加载失败');
            const {resources: generatedResources} = await response.json();
            if (!generatedResources.length) {
                statusMessage('暂无已生成成果');
                return;
            }
            resources.replaceChildren(...generatedResources.map(resourceRow));
        } catch (error) {
            statusMessage(error.message || '已生成成果加载失败');
        }
    }

    document.getElementById('refresh-deliveries')?.addEventListener('click', refresh);
    await refresh();
})();
