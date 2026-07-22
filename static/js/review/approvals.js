(() => {
    const container = document.getElementById('approval-list');
    if (!container) return;

    const button = (label, icon, className, onClick) => {
        const element = document.createElement('button');
        element.type = 'button';
        element.className = `btn btn-sm ${className}`;
        element.innerHTML = `<i class="bi ${icon} me-1" aria-hidden="true"></i>${label}`;
        element.addEventListener('click', onClick);
        return element;
    };

    const writeReview = async (submission, action, reviewComment = '') => {
        if (action === 'return' && !reviewComment.trim()) {
            throw new Error('退回时必须填写修改意见');
        }
        const options = {method: 'POST'};
        if (action === 'return') {
            options.headers = {'Content-Type': 'application/json'};
            options.body = JSON.stringify({review_comment: reviewComment.trim()});
        }
        const response = await aiworkerApi.request(
            `/api/workflow/submissions/${submission.id}/${action}`,
            options,
        );
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || '审批操作失败');
        await loadApprovals();
    };

    const renderSubmission = (task, submission) => {
        const section = document.createElement('section');
        section.className = 'border-bottom py-3';
        const heading = document.createElement('div');
        heading.className = 'd-flex justify-content-between gap-3 mb-2';
        const identity = document.createElement('div');
        const resourceType = document.createElement('strong');
        resourceType.textContent = submission.resource_type;
        const resourceVersion = document.createElement('div');
        resourceVersion.className = 'small text-muted';
        resourceVersion.textContent = `${submission.resource_id} · v${submission.version}`;
        identity.append(resourceType, resourceVersion);
        const status = document.createElement('span');
        status.className = 'badge text-bg-warning';
        status.textContent = '待审批';
        heading.append(identity, status);
        const snapshot = document.createElement('pre');
        snapshot.className = 'small bg-light border p-2 mb-2 overflow-auto';
        snapshot.textContent = JSON.stringify(submission.content_snapshot, null, 2);
        const comment = document.createElement('textarea');
        comment.className = 'form-control form-control-sm mb-2';
        comment.rows = 2;
        comment.placeholder = '退回修改意见（退回时必填）';
        comment.setAttribute('aria-label', '退回修改意见');
        const error = document.createElement('div');
        error.className = 'small text-danger mb-2';
        const actions = document.createElement('div');
        actions.className = 'd-flex justify-content-end gap-2';
        if (task.can_approve) {
            actions.appendChild(button('批准', 'bi-check-lg', 'btn-success', async () => {
                error.textContent = '';
                try { await writeReview(submission, 'approve'); } catch (exc) { error.textContent = exc.message; }
            }));
        }
        if (task.can_return) {
            actions.appendChild(button('退回', 'bi-arrow-return-left', 'btn-outline-danger', async () => {
                error.textContent = '';
                try { await writeReview(submission, 'return', comment.value); } catch (exc) { error.textContent = exc.message; }
            }));
        }
        section.append(heading, snapshot, comment, error, actions);
        return section;
    };

    async function loadApprovals() {
        container.className = 'alert alert-light border';
        container.textContent = '正在加载待审批版本...';
        try {
            const taskResponse = await aiworkerApi.request('/api/workflow/tasks');
            const tasks = await taskResponse.json();
            if (!taskResponse.ok) throw new Error(tasks.detail || '任务加载失败');
            const waiting = (tasks || []).filter(task =>
                task.status === 'waiting_review' && (task.can_approve || task.can_return)
            );
            const rows = [];
            for (const task of waiting) {
                const response = await aiworkerApi.request(`/api/workflow/tasks/${task.id}/submissions`);
                if (!response.ok) continue;
                const submissions = await response.json();
                const submission = submissions.filter(item => item.status === 'submitted').at(-1);
                if (submission) rows.push([task, submission]);
            }
            container.replaceChildren();
            if (!rows.length) {
                container.className = 'alert alert-light border';
                container.textContent = '暂无待审批版本。';
                return;
            }
            container.className = '';
            rows.forEach(([task, submission]) => container.appendChild(renderSubmission(task, submission)));
        } catch (error) {
            container.className = 'alert alert-danger';
            container.textContent = error.message || '审批列表加载失败';
        }
    }

    loadApprovals();
    document.querySelector('[data-workspace-page="review-approvals"]')?.setAttribute('data-ready', 'true');
})();
