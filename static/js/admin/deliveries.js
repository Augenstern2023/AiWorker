(async function () {
    'use strict';

    const resources = document.getElementById('generated-resource-list');
    const search = document.getElementById('generated-resource-search');
    const stageFilter = document.getElementById('generated-resource-stage-filter');
    const statusFilter = document.getElementById('generated-resource-status-filter');
    const generatedAfter = document.getElementById('generated-resource-generated-after');
    const generatedBefore = document.getElementById('generated-resource-generated-before');
    const count = document.getElementById('generated-resource-count');
    const resetFilters = document.getElementById('reset-generated-resource-filters');
    if (!resources || !search || !stageFilter || !statusFilter || !generatedAfter || !generatedBefore || !count || !resetFilters) return;

    const stageLabels = {brief: 'Brief', characters: '人物关系', outline: '分集大纲', beats: 'Beats', script: '场次剧本'};
    const resourceStatusLabels = {draft: '草稿', locked: '已锁定', brief_locked: '已锁定', approved: '已锁定', submitted: '已提交', returned: '已退回'};
    let generatedResources = [];

    function statusMessage(message) {
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = 7;
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

    function formatGeneratedAt(timestamp) {
        const value = Number(timestamp || 0);
        if (!value) return '-';
        return new Intl.DateTimeFormat('zh-CN', {
            year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
        }).format(new Date(value * 1000));
    }

    function isWithinGeneratedDateRange(resource) {
        const timestamp = Number(resource.generated_at || 0) * 1000;
        if (!timestamp) return !generatedAfter.value && !generatedBefore.value;
        const after = generatedAfter.value ? new Date(`${generatedAfter.value}T00:00:00`).getTime() : -Infinity;
        const before = generatedBefore.value ? new Date(`${generatedBefore.value}T23:59:59.999`).getTime() : Infinity;
        return timestamp >= after && timestamp <= before;
    }

    function resourceRow(resource) {
        const row = document.createElement('tr');
        const values = [
            resource.project_name,
            stageLabels[resource.stage] || resource.stage,
            `v${resource.resource_version}`,
            resourceStatusLabels[resource.resource_status] || resource.resource_status || '-',
            formatGeneratedAt(resource.generated_at),
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

    function applyFilters() {
        const query = search.value.trim().toLocaleLowerCase();
        const filtered = generatedResources.filter((resource) => {
            if (stageFilter.value && resource.stage !== stageFilter.value) return false;
            if (statusFilter.value) {
                const matchesLocked = statusFilter.value === 'locked'
                    && ['locked', 'brief_locked', 'approved'].includes(resource.resource_status);
                if (!matchesLocked && resource.resource_status !== statusFilter.value) return false;
            }
            if (!isWithinGeneratedDateRange(resource)) return false;
            if (!query) return true;
            const searchable = [
                resource.project_name,
                resource.stage,
                stageLabels[resource.stage],
                resource.resource_status,
                resourceStatusLabels[resource.resource_status],
                `v${resource.resource_version}`,
                formatGeneratedAt(resource.generated_at),
            ].filter(Boolean).join(' ').toLocaleLowerCase();
            return searchable.includes(query);
        });
        count.textContent = `显示 ${filtered.length} / ${generatedResources.length} 条成果`;
        if (!filtered.length) {
            statusMessage('没有符合条件的成果');
            return;
        }
        resources.replaceChildren(...filtered.map(resourceRow));
    }

    async function refresh() {
        try {
            const response = await window.aiworkerApi.request('/api/creative/generated-resources');
            if (!response.ok) throw new Error('已生成成果加载失败');
            const payload = await response.json();
            generatedResources = payload.resources || [];
            if (!generatedResources.length) {
                count.textContent = '显示 0 / 0 条成果';
                statusMessage('暂无已生成成果');
                return;
            }
            applyFilters();
        } catch (error) {
            count.textContent = '';
            statusMessage(error.message || '已生成成果加载失败');
        }
    }

    search.addEventListener('input', applyFilters);
    stageFilter.addEventListener('change', applyFilters);
    statusFilter.addEventListener('change', applyFilters);
    generatedAfter.addEventListener('change', applyFilters);
    generatedBefore.addEventListener('change', applyFilters);
    resetFilters.addEventListener('click', () => {
        search.value = '';
        stageFilter.value = '';
        statusFilter.value = '';
        generatedAfter.value = '';
        generatedBefore.value = '';
        applyFilters();
    });
    document.getElementById('refresh-deliveries')?.addEventListener('click', refresh);
    await refresh();
})();
