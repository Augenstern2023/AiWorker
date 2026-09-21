(async () => {
    'use strict';

    const overviewKey = 'workspace:overview';
    const analyticsKey = 'workspace:analytics';
    const dashboard = document.getElementById('selection-dashboard');
    const canReadAnalytics = dashboard?.dataset.canReadAnalytics === 'true';

    function setStat(id, value) {
        const el = document.getElementById(id);
        if (el) {
            el.textContent = String(value);
            el.classList.remove('ui-skeleton');
            el.style.display = '';
            el.style.width = '';
            el.style.height = '';
        }
    }

    function showError(message) {
        const el = document.getElementById('dashboard-error');
        if (!el) return;
        el.textContent = message || '';
        el.classList.toggle('d-none', !message);
        if (message) {
            window.aiworkerUi?.announce(message, {priority: 'assertive'});
        }
    }

    let hasFailure = false;

    // 请求 1：候选选题/选题库/已采纳的统计（使用 limit=50 而非 500）
    const overviewRequest = aiworkerUi.requestKey(overviewKey, async (signal) => {
        try {
            const res = await aiworkerApi.request('/api/creative/topics?limit=50&include_lifecycle=false', {signal});
            if (!res.ok) {
                const detail = await aiworkerApi.parseErrorDetail(res);
                throw new Error(detail);
            }
            const data = await res.json();
            const items = data.items || [];
            const adopted = items.filter(
                item => !['pending_review', 'rejected', 'archived'].includes(item.status)
            ).length;
            setStat('stat-candidates', items.length);
            setStat('stat-library', items.length);
            setStat('stat-adopted', adopted);
        } catch (e) {
            if (aiworkerApi.isAbortError(e)) return;
            hasFailure = true;
            // 选题区降级，不阻塞统计区
        }
    });

    // 请求 2：分析统计（独立，不受选题区失败影响）
    let analyticsRequest = Promise.resolve();
    if (canReadAnalytics) {
        analyticsRequest = aiworkerUi.requestKey(analyticsKey, async (signal) => {
            try {
                const res = await aiworkerApi.request('/api/analytics/overview', {signal});
                if (!res.ok) {
                    const detail = await aiworkerApi.parseErrorDetail(res);
                    throw new Error(detail);
                }
                const data = await res.json();
                setStat('stat-drama-count', data.drama_count ?? 0);
                setStat('stat-comment-count', data.comment_count ?? 0);
                setStat('stat-transcript-count', data.transcript_count ?? 0);
                setStat('stat-task-count', data.task_count ?? 0);
            } catch (e) {
                if (aiworkerApi.isAbortError(e)) return;
                hasFailure = true;
                // 统计区独立降级
            }
        });
    } else {
        ['stat-drama-count', 'stat-comment-count', 'stat-transcript-count', 'stat-task-count']
            .forEach(id => setStat(id, '—'));
    }

    await Promise.all([overviewRequest, analyticsRequest]);

    if (hasFailure) {
        showError('部分数据加载失败，已显示可用数据。请刷新重试。');
    }
})();
