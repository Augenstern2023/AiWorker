window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
    dramaPlayerSettingsKey: 'aiworker.drama-player.settings.v1',
    dramaPlayerProgressPrefix: 'aiworker.drama-player.progress.v1.',

    readDramaPlayerStorage(key, fallback = null) {
        try {
            const value = window.localStorage.getItem(key);
            return value ? JSON.parse(value) : fallback;
        } catch (_) {
            return fallback;
        }
    },

    writeDramaPlayerStorage(key, value) {
        try {
            window.localStorage.setItem(key, JSON.stringify(value));
        } catch (_) {
            // Playback remains usable when browser storage is unavailable.
        }
    },

    dramaPlayerVideo() {
        return document.getElementById('drama-player-video');
    },

    selectedDramaPlayer() {
        return this.dramaPlayer.dramas.find(item => item.id === this.dramaPlayer.selectedDramaId) || null;
    },

    selectedDramaPlayerEpisodes() {
        return this.selectedDramaPlayer()?.episodes || [];
    },

    async fetchDramaPlayerLibrary() {
        const player = this.dramaPlayer;
        player.loading = true;
        player.error = '';
        try {
            const response = await aiworkerApi.request('/api/drama-player/library');
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(payload.detail || '剧库加载失败');
            player.dramas = payload.dramas || [];
            const saved = this.readDramaPlayerStorage(this.dramaPlayerSettingsKey, {});
            if (typeof saved.autoNext === 'boolean') player.autoNext = saved.autoNext;
            if ([0.75, 1, 1.25, 1.5, 2, 3].includes(Number(saved.speed))) player.speed = Number(saved.speed);
            const drama = player.dramas.find(item => item.id === saved.dramaId) || player.dramas[0] || null;
            if (drama) this.selectDramaPlayerDrama(drama, {resume: true, episodeId: saved.episodeId});
        } catch (error) {
            player.dramas = [];
            player.selectedDramaId = '';
            player.selectedEpisodeId = '';
            player.error = error.message || '剧库加载失败，请稍后重试';
        } finally {
            player.loading = false;
        }
    },

    selectDramaPlayerDrama(drama, {resume = false, episodeId = ''} = {}) {
        if (!drama) return;
        this.dramaPlayer.selectedDramaId = drama.id;
        const episode = drama.episodes.find(item => item.id === episodeId) || drama.episodes[0];
        if (episode) this.playDramaPlayerEpisode(episode, {resume});
    },

    playDramaPlayerEpisode(episode, {resume = true} = {}) {
        const player = this.dramaPlayer;
        const drama = this.selectedDramaPlayer();
        if (!drama || !episode) return;
        player.selectedEpisodeId = episode.id;
        player.playbackError = '';
        this.rememberDramaPlayerSettings();
        const video = this.dramaPlayerVideo();
        if (!video) return;
        video.pause();
        video.src = `/api/drama-player/episodes/${encodeURIComponent(episode.id)}/stream`;
        video.playbackRate = Number(player.speed) || 1;
        video.onloadedmetadata = () => {
            if (resume) {
                const savedPosition = Number(this.readDramaPlayerStorage(`${this.dramaPlayerProgressPrefix}${episode.id}`, 0));
                if (savedPosition > 0 && Number.isFinite(video.duration) && savedPosition < video.duration - 2) {
                    video.currentTime = savedPosition;
                }
            }
            video.play().catch(() => {});
        };
        video.load();
    },

    rememberDramaPlayerProgress(video) {
        const now = Date.now();
        if (now - this.dramaPlayer.lastSavedAt < 3000 || !this.dramaPlayer.selectedEpisodeId) return;
        this.dramaPlayer.lastSavedAt = now;
        this.writeDramaPlayerStorage(
            `${this.dramaPlayerProgressPrefix}${this.dramaPlayer.selectedEpisodeId}`,
            Number(video.currentTime || 0)
        );
    },

    rememberDramaPlayerSettings() {
        this.writeDramaPlayerStorage(this.dramaPlayerSettingsKey, {
            dramaId: this.dramaPlayer.selectedDramaId,
            episodeId: this.dramaPlayer.selectedEpisodeId,
            autoNext: Boolean(this.dramaPlayer.autoNext),
            speed: Number(this.dramaPlayer.speed) || 1,
        });
    },

    setDramaPlayerSpeed() {
        const video = this.dramaPlayerVideo();
        if (video) video.playbackRate = Number(this.dramaPlayer.speed) || 1;
        this.rememberDramaPlayerSettings();
    },

    canPlayDramaPlayerRelative(offset) {
        const episodes = this.selectedDramaPlayerEpisodes();
        const index = episodes.findIndex(item => item.id === this.dramaPlayer.selectedEpisodeId);
        return index >= 0 && index + offset >= 0 && index + offset < episodes.length;
    },

    playDramaPlayerRelative(offset) {
        if (!this.canPlayDramaPlayerRelative(offset)) return;
        const episodes = this.selectedDramaPlayerEpisodes();
        const index = episodes.findIndex(item => item.id === this.dramaPlayer.selectedEpisodeId);
        this.playDramaPlayerEpisode(episodes[index + offset], {resume: true});
    },

    handleDramaPlayerEnded() {
        if (this.dramaPlayer.autoNext) this.playDramaPlayerRelative(1);
    },

    handleDramaPlayerError() {
        this.dramaPlayer.playbackError = '当前剧集文件已不可用，请刷新剧库后重试。';
    },
}));
