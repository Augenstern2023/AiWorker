(async () => {
    const setStat = (id, value) => {
        const element = document.getElementById(id);
        if (element) element.textContent = String(value);
    };
    const [topics, analytics] = await Promise.all([
        aiworkerApi.request('/api/creative/topics?limit=500').then(async response => {
            if (!response.ok) throw new Error('读取选题统计失败');
            return response.json();
        }).catch(() => null),
        aiworkerApi.request('/api/analytics/overview').then(async response => {
            if (!response.ok) throw new Error('读取数据资产统计失败');
            return response.json();
        }).catch(() => null),
    ]);

    const items = topics?.items || [];
    const adopted = items.filter(
        item => !['pending_review', 'rejected', 'archived'].includes(item.status)
    ).length;
    setStat('stat-candidates', items.length);
    setStat('stat-library', items.length);
    setStat('stat-adopted', adopted);
    setStat('stat-drama-count', analytics?.drama_count || 0);
    setStat('stat-comment-count', analytics?.comment_count || 0);
    setStat('stat-transcript-count', analytics?.transcript_count || 0);
    setStat('stat-task-count', analytics?.task_count || 0);
})();
