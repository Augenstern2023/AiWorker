(function () {
    'use strict';

    const body = document.getElementById('workflow-bootstrap-topics');
    const error = document.getElementById('workflow-bootstrap-error');
    if (!body) return;

    function showError(message = '') {
        error.textContent = message;
        error.classList.toggle('d-none', !message);
    }

    async function bootstrap(topic, button) {
        button.disabled = true;
        showError();
        try {
            const response = await window.aiworkerApi.request(`/api/creative/topics/${topic.id}/workflow`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: '{}',
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || '创建工作流失败');
            button.innerHTML = '<i class="bi bi-check2"></i>';
            button.title = '工作流已就绪';
        } catch (reason) {
            button.disabled = false;
            showError(reason.message || '创建工作流失败');
        }
    }

    async function load() {
        const response = await window.aiworkerApi.request('/api/creative/topics/workflow-candidates');
        const data = await response.json();
        if (!response.ok) {
            showError(data.detail || '读取选题失败');
            return;
        }
        const items = data.items || [];
        if (!items.length) {
            body.innerHTML = '<tr><td colspan="3" class="text-center text-muted py-4">暂无待决策选题</td></tr>';
            return;
        }
        body.replaceChildren(...items.map((topic) => {
            const row = document.createElement('tr');
            const title = document.createElement('td');
            title.textContent = topic.title;
            const status = document.createElement('td');
            status.textContent = topic.lifecycle_label || '已采纳';
            const action = document.createElement('td');
            action.className = 'text-end';
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn btn-sm btn-primary';
            button.title = '创建工作流';
            button.setAttribute('aria-label', '创建工作流');
            button.innerHTML = '<i class="bi bi-play-circle"></i>';
            button.addEventListener('click', () => bootstrap(topic, button));
            action.appendChild(button);
            row.append(title, status, action);
            return row;
        }));
    }

    load().catch((reason) => showError(reason.message || '读取选题失败'));
})();
