window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
    selectedBeatOrder: 1,
    beatsEditorRevision: 0,
    refreshBeatsEditor() {
        this.beatsEditorRevision += 1;
    },
    beatsForEditor() {
        void this.beatsEditorRevision;
        return this.selectedProjectBeats()?.content?.beats || [];
    },
    beatsSceneCount(item = this.selectedProjectBeats()) {
        // Keep Alpine's x-for/count bindings reactive when a test or caller
        // returns the original resource object instead of its proxy.
        void this.beatsEditorRevision;
        return Array.isArray(item?.content?.beats) ? item.content.beats.length : 0;
    },
    beatsWithinSceneLimit(item = this.selectedProjectBeats()) {
        return this.beatsSceneCount(item) <= 3;
    },
    beatsSceneLimitMessage(item = this.selectedProjectBeats()) {
        const count = this.beatsSceneCount(item);
        return count > 3
            ? `当前版本有 ${count} 个场次，超过每集最多 3 个场次的限制；请合并或删除后再保存。`
            : `每集最多 3 个场次 · 当前 ${count} 个`;
    },
    selectProjectBeat(order) {
        this.selectedBeatOrder = Number(order || 1);
    },
    addProjectBeat() {
        const item = this.selectedProjectBeats();
        if (!item || !this.creativeResourceEditable(item)) return;
        item.content = item.content || {};
        const beats = Array.isArray(item.content.beats) ? item.content.beats : [];
        if (beats.length >= 3) {
            this.showCreativeNotice?.('每集 Beats 最多 3 个场次，请先删除或合并现有场次。', 'error');
            return;
        }
        const order = beats.length + 1;
        beats.push({
            order,
            scene: `新场次 ${order}`,
            characters: [],
            goal: '',
            obstacle: '',
            emotion_change: '',
            state_change: '',
            cost: '',
            opposition_tactic: '',
            turn: '',
            hook: '',
            continuity_updates: [],
        });
        item.content.beats = beats;
        this.selectedBeatOrder = order;
        this.refreshBeatsEditor();
    },
    removeProjectBeat(order = this.selectedBeatOrder) {
        const item = this.selectedProjectBeats();
        if (!item || !this.creativeResourceEditable(item)) return;
        const beats = Array.isArray(item.content?.beats) ? item.content.beats : [];
        if (beats.length <= 1) {
            this.showCreativeNotice?.('至少保留 1 个场次；如需重建，请重新生成或创建空白版本。', 'error');
            return;
        }
        const index = beats.findIndex((beat) => Number(beat.order) === Number(order));
        if (index < 0) return;
        beats.splice(index, 1);
        beats.forEach((beat, beatIndex) => { beat.order = beatIndex + 1; });
        item.content.beats = beats;
        this.selectedBeatOrder = beats[Math.min(index, beats.length - 1)].order;
        this.refreshBeatsEditor();
    },
    selectedProjectBeat() {
        const beats = this.selectedProjectBeats()?.content?.beats || [];
        return beats.find((beat) => Number(beat.order) === Number(this.selectedBeatOrder))
            || beats[0]
            || null;
    },
}));

document.querySelector('[data-workspace-page="writing-beats"]')?.setAttribute('data-ready', 'true');
