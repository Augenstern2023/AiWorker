(async () => {
    const setStat = (id, value) => {
        const element = document.getElementById(id);
        if (element) element.textContent = String(value);
    };
    try {
        const response = await aiworkerApi.request('/api/creative/topics?limit=500');
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || '读取选题统计失败');
        const items = data.items || [];
        const adopted = items.filter(
            item => !['pending_review', 'rejected', 'archived'].includes(item.status)
        ).length;
        setStat('stat-candidates', items.length);
        setStat('stat-library', items.length);
        setStat('stat-adopted', adopted);
    } catch (_error) {
        setStat('stat-candidates', 0);
        setStat('stat-library', 0);
        setStat('stat-adopted', 0);
    }
})();
