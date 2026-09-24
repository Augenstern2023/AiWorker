window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
    selectedBeatOrder: 1,
    beatsSceneCount(item = this.selectedProjectBeats()) {
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
    selectedProjectBeat() {
        const beats = this.selectedProjectBeats()?.content?.beats || [];
        return beats.find((beat) => Number(beat.order) === Number(this.selectedBeatOrder))
            || beats[0]
            || null;
    },
}));

document.querySelector('[data-workspace-page="writing-beats"]')?.setAttribute('data-ready', 'true');
