window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
    filteredProjectOutlines() {
        const items = this.currentProjectOutlines();
        const query = String(this.creativeWorkspace.outlineQuery || '').trim().toLocaleLowerCase();
        if (!query) return items;
        return items.filter((item) => {
            const episode = String(Number(item.episode_number || 0));
            const event = String(item.content?.core_event || '').toLocaleLowerCase();
            return episode.includes(query) || event.includes(query);
        });
    },

    outlineEpisodeExpanded(episodeNumber) {
        const expanded = this.creativeWorkspace.expandedOutlineEpisodes || {};
        return Boolean(expanded[String(Number(episodeNumber))]);
    },

    toggleOutlineEpisode(episodeNumber) {
        const key = String(Number(episodeNumber));
        if (!this.creativeWorkspace.expandedOutlineEpisodes) {
            this.creativeWorkspace.expandedOutlineEpisodes = {};
        }
        this.creativeWorkspace.expandedOutlineEpisodes[key] = !this.outlineEpisodeExpanded(episodeNumber);
    },

    expandAllOutlineEpisodes() {
        if (!this.creativeWorkspace.expandedOutlineEpisodes) {
            this.creativeWorkspace.expandedOutlineEpisodes = {};
        }
        this.filteredProjectOutlines().forEach((item) => {
            this.creativeWorkspace.expandedOutlineEpisodes[String(Number(item.episode_number))] = true;
        });
    },

    collapseAllOutlineEpisodes() {
        if (!this.creativeWorkspace.expandedOutlineEpisodes) {
            this.creativeWorkspace.expandedOutlineEpisodes = {};
        }
        this.filteredProjectOutlines().forEach((item) => {
            this.creativeWorkspace.expandedOutlineEpisodes[String(Number(item.episode_number))] = false;
        });
    },

    resetOutlineEpisodeCards() {
        this.creativeWorkspace.outlineQuery = '';
        this.creativeWorkspace.expandedOutlineEpisodes = {};
    },

    syncOutlineEpisodeCardContext(project) {
        const contextKey = project
            ? JSON.stringify([
                project.id || '',
                project.locked_brief_id || '',
                project.character_batch_id || '',
                project.outline_batch_id || '',
            ])
            : '';
        if (contextKey === String(this.creativeWorkspace.outlineCardContextKey || '')) return;
        this.creativeWorkspace.outlineCardContextKey = contextKey;
        this.resetOutlineEpisodeCards();
    },
}));

document.querySelector('[data-workspace-page="writing-outlines"]')?.setAttribute('data-ready', 'true');
