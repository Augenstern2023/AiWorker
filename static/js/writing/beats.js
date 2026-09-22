window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
    selectedBeatOrder: 1,
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
