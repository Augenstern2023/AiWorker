(function () {
    'use strict';

    const draftId = new URLSearchParams(window.location.search).get('optimization_draft');
    if (!draftId || !window.aiworkerApi) return;
    const expectedKind = window.location.pathname.endsWith('/beats') ? 'beats' : 'script';

    async function loadDraft() {
        const response = await aiworkerApi.request(`/api/script-optimization-drafts/${encodeURIComponent(draftId)}`);
        const draft = await response.json();
        if (!response.ok || draft.kind !== expectedKind) return;
        const section = document.createElement('section');
        section.className = 'border-top border-bottom py-3 mb-3';
        const heading = document.createElement('h5');
        heading.className = 'mb-2';
        heading.textContent = `AI 优化草稿 · 第 ${draft.episode_number} 集`;
        const summary = document.createElement('div');
        summary.className = 'small text-muted mb-2';
        summary.textContent = '草稿与下方当前版本对照后，再决定采纳。';
        const preview = document.createElement('pre');
        preview.className = 'small mb-0';
        preview.textContent = JSON.stringify(draft.content, null, 2);
        section.append(heading, summary, preview);
        document.querySelector('main')?.prepend(section);
    }

    loadDraft().catch(() => {});
}());
