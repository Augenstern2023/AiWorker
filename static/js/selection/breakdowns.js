window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
                async updateTopicLibrary(topic, status) {
                    const res = await aiworkerApi.request(`/api/creative/topics/${topic.id}`, {
                        method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({status})
                    });
                    const data = await res.json();
                    if (!res.ok) { this.topicLibrary.error = data.detail || '更新选题状态失败'; return; }
                    Object.assign(topic, data);
                },

                createTopicLibraryBreakdown(topic) {
                    return this.startTranscriptBreakdown(topic, 'topic-library');
                },

                async openTopicTranscriptPreview(topic) {
                    this.topicTranscriptPreview = {topic, episodes: [], selectedEpisode: null, loading: true, error: ''};
                    this.topicTranscriptPreviewModal.show();
                    try {
                        const res = await aiworkerApi.request(`/api/creative/topics/${topic.id}/transcripts`);
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '读取剧目转写失败');
                        this.topicTranscriptPreview.episodes = data.episodes || [];
                        this.topicTranscriptPreview.selectedEpisode = this.topicTranscriptPreview.episodes[0]?.episode_number || null;
                    } catch (e) {
                        this.topicTranscriptPreview.error = e.message || '读取剧目转写失败';
                    } finally {
                        this.topicTranscriptPreview.loading = false;
                    }
                },

                selectedTopicTranscript() {
                    return this.topicTranscriptPreview.episodes.find(
                        item => Number(item.episode_number) === Number(this.topicTranscriptPreview.selectedEpisode)
                    ) || null;
                },

                canMoveTopicTranscript(offset) {
                    const episodes = this.topicTranscriptPreview.episodes;
                    const index = episodes.findIndex(
                        item => Number(item.episode_number) === Number(this.topicTranscriptPreview.selectedEpisode)
                    );
                    const target = index + Number(offset || 0);
                    return index >= 0 && target >= 0 && target < episodes.length;
                },

                moveTopicTranscript(offset) {
                    if (!this.canMoveTopicTranscript(offset)) return;
                    const episodes = this.topicTranscriptPreview.episodes;
                    const index = episodes.findIndex(
                        item => Number(item.episode_number) === Number(this.topicTranscriptPreview.selectedEpisode)
                    );
                    this.topicTranscriptPreview.selectedEpisode = episodes[index + Number(offset)].episode_number;
                },

                transcriptSourceLabel(source) {
                    return {script: '剧本转写', transcript: '语音转写', text: '文本转写'}[source] || '剧目转写';
                },

                async startTranscriptBreakdown(topic, source, forceRegenerate = false) {
                    if (!topic?.id) return;
                    this.breakdownGeneration = {
                        topic,
                        source,
                        jobId: '',
                        status: 'pending',
                        message: '正在检查已下载的剧目脚本',
                        progress: {completed: 0, total: 1},
                        coverage: [],
                        requiresTranscription: false,
                        notifiedStatus: '',
                        error: '',
                        startedAt: Date.now(),
                        elapsedSeconds: 0
                    };
                    try {
                        const task = await this.resolveCreativeWorkflowTask('breakdown', null, topic.id);
                        const res = await aiworkerApi.request('/api/creative/breakdowns', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({topic_id: topic.id, force_regenerate: forceRegenerate, workflow_task_id: task.id})
                        });
                        const data = await res.json();
                        const detail = data.detail;
                        if (res.status === 409 && detail?.code === 'transcript_required') {
                            this.breakdownGeneration.status = 'blocked';
                            this.breakdownGeneration.message = detail.message;
                            this.transcriptRequiredModal.show();
                            return;
                        }
                        if (!res.ok || data.status === 'error') {
                            throw new Error(typeof detail === 'string' ? detail : detail?.message || data.message || '生成拆解失败');
                        }
                        if (data.status === 'existing' && data.breakdown) {
                            this.breakdownGeneration.status = 'existing';
                            this.breakdownGeneration.message = '已加载最新拆解版本';
                            this.breakdownGeneration.startedAt = 0;
                            if (source === 'topic-library') this.topicLibrary.breakdown = data.breakdown;
                            else this.creative.breakdown = data.breakdown;
                            this.openBreakdownEditor(source, topic.title, data.breakdown);
                            return;
                        }
                        this.breakdownGeneration.jobId = data.job.job_id;
                        this.breakdownGeneration.status = data.job.status || 'pending';
                        this.breakdownGeneration.message = data.requires_transcription
                            ? '已发现下载视频，正在生成剧本转写'
                            : (data.job.message || '已加载剧目脚本，正在生成个性化拆解');
                        this.breakdownGeneration.progress = data.job.progress || {completed: 0, total: 1};
                        this.breakdownGeneration.coverage = data.coverage || [];
                        this.breakdownGeneration.requiresTranscription = Boolean(data.requires_transcription);
                        this.breakdownGeneration.startedAt = Number(data.job.started_at || 0) > 0
                            ? Number(data.job.started_at) * 1000
                            : Date.now();
                        this.breakdownGenerationProgressModal.show();
                        if (source === 'topic-library') await this.fetchTopicLibrary();
                        if (source === 'creative') await this.fetchCreativeTopics();
                        await this.fetchJobs();
                    } catch (e) {
                        const message = e.message || '生成拆解失败';
                        this.breakdownGeneration.status = 'failed';
                        this.breakdownGeneration.message = message;
                        this.breakdownGeneration.error = message;
                        if (source === 'topic-library') this.topicLibrary.error = message;
                        else this.creative.error = message;
                        this.breakdownGenerationProgressModal.show();
                    }
                },

                async syncBreakdownGenerationProgress() {
                    if (!this.breakdownGeneration.jobId) return;
                    const job = this.jobs.find(item => item.job_id === this.breakdownGeneration.jobId);
                    if (!job) return;
                    this.breakdownGeneration.status = job.status;
                    this.breakdownGeneration.message = job.message || this.breakdownGeneration.message;
                    this.breakdownGeneration.progress = job.progress || this.breakdownGeneration.progress || {};
                    if (!this.breakdownGeneration.startedAt && Number(job.started_at || 0) > 0) {
                        this.breakdownGeneration.startedAt = Number(job.started_at) * 1000;
                    }
                    this.breakdownGeneration.elapsedSeconds = Math.max(
                        Number(this.breakdownGeneration.elapsedSeconds || 0),
                        Number(job.progress?.elapsed_seconds || 0)
                    );
                    if (job.progress?.coverage?.length) this.breakdownGeneration.coverage = job.progress.coverage;
                    if (!['succeeded', 'failed', 'cancelled'].includes(job.status) || this.breakdownGeneration.notifiedStatus === job.status) return;
                    this.breakdownGeneration.notifiedStatus = job.status;
                    if (job.status !== 'succeeded') {
                        this.breakdownGeneration.error = `拆解生成${job.status === 'cancelled' ? '已取消' : '失败'}：${job.message || '请查看任务日志'}`;
                        return;
                    }
                    let completedJob = job;
                    if (!(completedJob.events || []).length) {
                        const detailResponse = await aiworkerApi.request(`/api/jobs/${job.job_id}`);
                        if (detailResponse.ok) completedJob = await detailResponse.json();
                    }
                    const finished = [...(completedJob.events || [])].reverse().find(event => event.event === 'job_finished' && event.payload?.breakdown_id);
                    const breakdownId = finished?.payload?.breakdown_id;
                    if (!breakdownId) {
                        this.breakdownGeneration.error = '生成任务已结束，但未返回拆解版本，请查看任务日志。';
                        return;
                    }
                    try {
                        const res = await aiworkerApi.request(`/api/creative/breakdowns/${breakdownId}`);
                        const record = await res.json();
                        if (!res.ok) throw new Error(record.detail || '读取生成结果失败');
                        if (this.breakdownGeneration.source === 'topic-library') {
                            this.topicLibrary.breakdown = record;
                            await this.fetchTopicLibrary();
                        } else {
                            this.creative.breakdown = record;
                            await this.fetchCreativeTopics();
                        }
                        const source = this.breakdownGeneration.source;
                        const title = this.breakdownGeneration.topic?.title || record.content?.title;
                        this.breakdownGenerationProgressModal.hide();
                        setTimeout(() => this.openBreakdownEditor(source, title, record), 200);
                    } catch (e) {
                        this.breakdownGeneration.error = e.message || '读取生成结果失败';
                    }
                },

                breakdownGenerationRunning() {
                    return ['pending', 'running', 'stopping'].includes(this.breakdownGeneration.status);
                },

                breakdownGenerationRetryable() {
                    return ['failed', 'cancelled'].includes(this.breakdownGeneration.status);
                },

                breakdownGenerationIndeterminate() {
                    if (!this.breakdownGenerationRunning()) return false;
                    const progress = this.breakdownGeneration.progress || {};
                    if (progress.indeterminate) return true;
                    return Number(progress.completed || 0) === 0 && Number(progress.total || 0) <= 1;
                },

                breakdownGenerationProgressLabel() {
                    if (!this.breakdownGenerationIndeterminate()) return `${this.breakdownGenerationProgressPercent()}%`;
                    const seconds = Math.max(
                        Number(this.breakdownGeneration.elapsedSeconds || 0),
                        Number(this.breakdownGeneration.progress?.elapsed_seconds || 0)
                    );
                    return `已等待 ${this.formatBreakdownGenerationElapsed(seconds)}`;
                },

                formatBreakdownGenerationElapsed(seconds) {
                    const safeSeconds = Math.max(0, Math.floor(Number(seconds || 0)));
                    const minutes = Math.floor(safeSeconds / 60);
                    const remainder = safeSeconds % 60;
                    return minutes ? `${minutes}分${remainder}秒` : `${remainder}秒`;
                },

                tickBreakdownGenerationElapsed() {
                    if (!this.breakdownGenerationRunning()) return;
                    if (!this.breakdownGeneration.startedAt) this.breakdownGeneration.startedAt = Date.now();
                    this.breakdownGeneration.elapsedSeconds = Math.max(
                        Number(this.breakdownGeneration.elapsedSeconds || 0),
                        Math.floor((Date.now() - this.breakdownGeneration.startedAt) / 1000)
                    );
                },

                breakdownGenerationProgressPercent() {
                    const progress = this.breakdownGeneration.progress || {};
                    const total = Number(progress.total || 0);
                    if (total) return Math.min(100, Math.round(Number(progress.completed || 0) * 100 / total));
                    return this.breakdownGeneration.status === 'succeeded' ? 100 : 0;
                },

                async openBreakdownTaskLog() {
                    const jobId = this.breakdownGeneration.jobId;
                    this.breakdownGenerationProgressModal.hide();
                    window.location.assign(`/tasks${jobId ? `?job_id=${encodeURIComponent(jobId)}` : ''}`);
                },

                regenerateCurrentBreakdown() {
                    const record = this.breakdownEditor.record;
                    if (!record?.topic_id || this.breakdownGenerationRunning()) return;
                    if (!confirm('确定重新分析已下载脚本并生成一个新拆解版本吗？')) return;
                    const topic = this.breakdownGeneration.topic?.id === record.topic_id
                        ? this.breakdownGeneration.topic
                        : {id: record.topic_id, title: this.breakdownEditor.title};
                    const source = this.breakdownEditor.source || 'topic-library';
                    this.breakdownEditorModal.hide();
                    setTimeout(() => this.startTranscriptBreakdown(topic, source, true), 350);
                },

                dramaDownloadTitleKey(value) {
                    return String(value || '').normalize('NFKC').toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
                },

                findDramaDownloadJob(title = '') {
                    const titleKey = this.dramaDownloadTitleKey(title);
                    return this.jobs.find(job => {
                        const crawlerType = job.crawler_type || job.job?.crawler_type || '';
                        const jobTitle = job.job?.extra?.title || '';
                        return ['drama_download', 'douyin_fallback_analysis'].includes(crawlerType)
                            && ['pending', 'running', 'stopping'].includes(job.status)
                            && (!titleKey || this.dramaDownloadTitleKey(jobTitle) === titleKey);
                    }) || null;
                },

                restoreDramaDownloadJob(topic, job, showModal = true) {
                    if (!job) return false;
                    const crawlerType = job.crawler_type || job.job?.crawler_type || '';
                    const extra = job.job?.extra || {};
                    const source = crawlerType === 'douyin_fallback_analysis' ? 'douyin_fallback' : 'hongguo';
                    this.dramaDownload = {
                        title: topic?.title || extra.title || '', topic: topic || null, source,
                        fallbackReason: extra.fallback_reason || '', candidates: [], selectedCandidate: null,
                        selectedAwemeIds: [], allDouyinCandidates: false, episodes: [], selectedEpisodes: [],
                        allEpisodes: Boolean(extra.all_episodes), searching: false, episodesLoading: false,
                        starting: false, error: '', jobId: job.job_id, jobStatus: job.status || 'running',
                        progress: job.progress || {}, progressMessage: job.message || '下载任务正在运行',
                        paused: Boolean(job.paused), finished: false, background: !showModal
                    };
                    if (showModal) this.dramaDownloadModal.show();
                    this.scrollDramaDownloadToTop();
                    return true;
                },

                restoreLatestActiveDramaDownload() {
                    if (this.dramaDownload.jobId) return;
                    const job = this.findDramaDownloadJob();
                    if (job) this.restoreDramaDownloadJob(null, job, false);
                },

                scrollDramaDownloadToTop() {
                    this.$nextTick(() => {
                        const body = document.querySelector('#dramaDownloadModal .modal-body');
                        if (body) body.scrollTop = 0;
                    });
                },

                dramaDownloadStatusLabel() {
                    if (this.dramaDownload.starting) return '正在启动';
                    if (this.dramaDownload.paused) return '已暂停';
                    return {pending: '等待执行', running: '下载中', stopping: '正在停止', succeeded: '已完成', failed: '失败', cancelled: '已取消'}[this.dramaDownload.jobStatus] || '下载中';
                },

                dramaDownloadStatusClass() {
                    if (this.dramaDownload.paused) return 'bg-warning text-dark';
                    return {pending: 'bg-secondary', running: 'bg-primary', stopping: 'bg-warning text-dark', succeeded: 'bg-success', failed: 'bg-danger', cancelled: 'bg-secondary'}[this.dramaDownload.jobStatus] || 'bg-primary';
                },

                dramaDownloadCurrentDescription() {
                    const current = Number(this.dramaDownload.progress?.current || 0);
                    if (current > 0) return this.dramaDownload.source === 'douyin_fallback' ? `当前处理第 ${current} 个视频` : `当前处理第 ${current} 集`;
                    const completed = Number(this.dramaDownload.progress?.completed || 0);
                    const total = Number(this.dramaDownload.progress?.total || 0);
                    return total > 0 ? `已完成 ${completed} / ${total}` : '';
                },

                async openTopicDownload(topic) {
                    await this.fetchJobs();
                    const runningJob = this.findDramaDownloadJob(topic?.title || '');
                    if (runningJob) {
                        this.restoreDramaDownloadJob(topic, runningJob, true);
                        return;
                    }
                    this.dramaDownload = {title: topic.title || '', topic, source: 'hongguo', fallbackReason: '', candidates: [], selectedCandidate: null, selectedAwemeIds: [], allDouyinCandidates: false, episodes: [], selectedEpisodes: [], allEpisodes: false, searching: true, episodesLoading: false, starting: false, error: '', jobId: '', jobStatus: '', progress: {}, progressMessage: '', paused: false, finished: false, background: false};
                    this.dramaDownloadModal.show();
                    try {
                        const res = await aiworkerApi.request('/api/hongguo/drama/download/search', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: this.dramaDownload.title})});
                        const data = await res.json();
                        this.dramaDownload.source = data.source || 'hongguo';
                        this.dramaDownload.fallbackReason = data.fallback_reason || '';
                        if (!res.ok || data.status === 'error') throw new Error(data.message || '剧目检索失败');
                        this.dramaDownload.candidates = data.candidates || [];
                        if (this.dramaDownload.source === 'douyin_fallback' && this.dramaDownload.candidates.length) {
                            this.dramaDownload.selectedCandidate = this.dramaDownload.candidates[0];
                            const firstDownloadable = this.dramaDownload.candidates.find(candidate => !candidate.is_paid);
                            this.dramaDownload.selectedAwemeIds = firstDownloadable ? [firstDownloadable.aweme_id] : [];
                            this.syncDouyinDownloadSelection();
                        }
                        if (!this.dramaDownload.candidates.length) this.dramaDownload.error = this.dramaDownload.source === 'douyin_fallback' ? '红果和抖音都没有找到可用视频，请确认剧名后重试' : '没有找到剧名完全匹配的红果剧目，请确认剧名后重试';
                        this.dramaDownload.searching = false;
                        // Hongguo has a book/episode flow, but Douyin fallback always
                        // requires an explicit user choice to avoid downloading an
                        // unrelated promotion or clip that happens to match the title.
                        if (this.dramaDownload.candidates.length === 1 && this.dramaDownload.source !== 'douyin_fallback') await this.selectDramaDownloadCandidate(this.dramaDownload.candidates[0]);
                    } catch (e) {
                        this.dramaDownload.error = e.message || '剧目检索失败';
                    } finally {
                        this.dramaDownload.searching = false;
                    }
                },

                async selectDramaDownloadCandidate(candidate) {
                    this.dramaDownload.selectedCandidate = candidate;
                    this.dramaDownload.episodes = [];
                    this.dramaDownload.selectedEpisodes = [];
                    this.dramaDownload.allEpisodes = false;
                    if (this.dramaDownload.source === 'douyin_fallback') {
                        this.dramaDownload.episodesLoading = false;
                        this.dramaDownload.error = '';
                        return;
                    }
                    this.dramaDownload.episodesLoading = true;
                    this.dramaDownload.error = '';
                    try {
                        const res = await aiworkerApi.request('/api/hongguo/drama/episodes', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({book_id: candidate.book_id})});
                        const data = await res.json();
                        if (!res.ok || data.status === 'error') throw new Error(data.message || '读取剧集失败');
                        this.dramaDownload.episodes = data.episodes || [];
                    } catch (e) {
                        this.dramaDownload.error = e.message || '读取剧集失败';
                    } finally {
                        this.dramaDownload.episodesLoading = false;
                    }
                },

                toggleAllDramaEpisodes() {
                    if (this.dramaDownload.allEpisodes) this.dramaDownload.selectedEpisodes = this.downloadableDramaEpisodes().map(item => item.episode);
                    else this.dramaDownload.selectedEpisodes = [];
                },

                isDramaEpisodeDownloaded(episode) {
                    return episode.download_status?.status === 'downloaded';
                },

                downloadableDramaEpisodes() {
                    return this.dramaDownload.episodes.filter(episode => !this.isDramaEpisodeDownloaded(episode));
                },

                dramaSelectedEpisodesForDownload() {
                    if (this.dramaDownload.allEpisodes) return this.downloadableDramaEpisodes().map(item => item.episode);
                    return this.dramaDownload.selectedEpisodes.filter(episode => !this.dramaDownload.episodes.find(item => Number(item.episode) === Number(episode) && this.isDramaEpisodeDownloaded(item)));
                },

                toggleAllDouyinDownloadCandidates() {
                    const downloadableIds = this.dramaDownload.candidates.filter(candidate => !candidate.is_paid).map(candidate => candidate.aweme_id);
                    this.dramaDownload.selectedAwemeIds = this.dramaDownload.allDouyinCandidates ? downloadableIds : [];
                },

                syncDouyinDownloadSelection() {
                    const downloadableIds = this.dramaDownload.candidates.filter(candidate => !candidate.is_paid).map(candidate => candidate.aweme_id);
                    this.dramaDownload.allDouyinCandidates = downloadableIds.length > 0 && downloadableIds.every(id => this.dramaDownload.selectedAwemeIds.includes(id));
                },

                selectedDouyinEpisodeSummary() {
                    const labels = this.dramaDownload.candidates
                        .filter(candidate => this.dramaDownload.selectedAwemeIds.includes(candidate.aweme_id) && candidate.episode_label)
                        .map(candidate => candidate.episode_label);
                    const unique = [...new Set(labels)];
                    return unique.length ? `集数：${unique.join('、')}` : '';
                },

                async startDramaDownload() {
                    const isDouyinFallback = this.dramaDownload.source === 'douyin_fallback';
                    const episodeNumbers = this.dramaSelectedEpisodesForDownload();
                    if (isDouyinFallback ? !this.dramaDownload.selectedAwemeIds.length : !this.dramaDownload.selectedCandidate || !episodeNumbers.length) return;
                    this.dramaDownload.starting = true;
                    this.dramaDownload.error = '';
                    this.dramaDownload.progressMessage = '正在创建下载任务…';
                    this.dramaDownload.progress = {completed: 0, total: isDouyinFallback ? this.dramaDownload.selectedAwemeIds.length : episodeNumbers.length, percent: 0, downloaded: 0, skipped_paid: 0, failed: 0};
                    try {
                        const task = await this.resolveCreativeWorkflowTask('source_package', null, this.dramaDownload.topic?.id || null);
                        const endpoint = isDouyinFallback ? '/api/hongguo/drama/douyin-fallback/jobs' : '/api/hongguo/drama/download/jobs';
                        const payload = isDouyinFallback
                            ? {title: this.dramaDownload.title, aweme_ids: this.dramaDownload.selectedAwemeIds, fallback_reason: this.dramaDownload.fallbackReason || '', workflow_task_id: task.id}
                            : {title: this.dramaDownload.title, book_id: this.dramaDownload.selectedCandidate.book_id, episodes: episodeNumbers, all_episodes: this.dramaDownload.allEpisodes && episodeNumbers.length === this.dramaDownload.episodes.length, workflow_task_id: task.id};
                        const res = await aiworkerApi.request(endpoint, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
                        const data = await res.json();
                        if (!res.ok || data.status === 'error') throw new Error(data.message || '创建下载任务失败');
                        this.dramaDownload.jobId = data.job.job_id;
                        this.dramaDownload.jobStatus = data.job.status || 'pending';
                        this.dramaDownload.progress = {...this.dramaDownload.progress, ...(data.job.progress || {})};
                        this.dramaDownload.progressMessage = isDouyinFallback ? '抖音降级下载任务已开始' : '下载任务已开始';
                        this.scrollDramaDownloadToTop();
                        await this.fetchTopicLibrary();
                        await this.fetchCreativeTopics();
                        await this.fetchJobs();
                    } catch (e) {
                        this.dramaDownload.error = e.message || '创建下载任务失败';
                    } finally {
                        this.dramaDownload.starting = false;
                    }
                },

                syncDramaDownloadProgress() {
                    if (!this.dramaDownload.jobId) return;
                    const job = this.jobs.find(item => item.job_id === this.dramaDownload.jobId);
                    if (!job) return;
                    this.dramaDownload.jobStatus = job.status || this.dramaDownload.jobStatus;
                    this.dramaDownload.paused = Boolean(job.paused);
                    this.dramaDownload.progress = {...(this.dramaDownload.progress || {}), ...(job.progress || {})};
                    this.dramaDownload.progressMessage = job.message || this.dramaDownload.progressMessage;
                    if (job.status === 'succeeded') {
                        if (!this.dramaDownload.finished) {
                            alert(`${job.message || (this.dramaDownload.source === 'douyin_fallback' ? '抖音降级视频解析完成' : '剧集下载完成')}，文件已保存到数据管理。`);
                            this.fetchTopicLibrary();
                            this.fetchCreativeTopics();
                        }
                        this.dramaDownload.finished = true;
                    } else if (['failed', 'cancelled'].includes(job.status)) {
                        if (!this.dramaDownload.finished) alert(`剧集下载${job.status === 'cancelled' ? '已取消' : '失败'}：${job.message || ''}`);
                        this.dramaDownload.finished = true;
                    }
                },

                async pauseDramaDownload() {
                    if (!this.dramaDownload.jobId) return;
                    const res = await aiworkerApi.request(`/api/jobs/${this.dramaDownload.jobId}/pause`, {method: 'POST'});
                    const data = await res.json();
                    if (!res.ok || data.status === 'error') { this.dramaDownload.error = data.message || '暂停失败'; return; }
                    this.dramaDownload.paused = true;
                },

                async resumeDramaDownload() {
                    if (!this.dramaDownload.jobId) return;
                    const res = await aiworkerApi.request(`/api/jobs/${this.dramaDownload.jobId}/resume`, {method: 'POST'});
                    const data = await res.json();
                    if (!res.ok || data.status === 'error') { this.dramaDownload.error = data.message || '继续失败'; return; }
                    this.dramaDownload.paused = false;
                },

                async cancelDramaDownload() {
                    if (!this.dramaDownload.jobId) return;
                    if (!confirm('确定取消当前下载任务吗？已完成的集数会保留。')) return;
                    await aiworkerApi.request(`/api/jobs/${this.dramaDownload.jobId}/stop`, {method: 'POST'});
                },

                async openDramaDownloadFiles() {
                    this.dramaDownloadModal.hide();
                    window.location.assign('/selection/dramas');
                },

                openBreakdownEditor(source, title, record) {
                    const fieldGuide = record?.field_guide || [];
                    this.breakdownEditor = {
                        source,
                        title: title || record?.content?.title || '未命名选题',
                        record,
                        content: this.normalizeBreakdownEditorContent(record?.content, fieldGuide),
                        fieldGuide,
                        saving: false,
                        error: ''
                    };
                    this.breakdownEditorModal.show();
                },

                syncBreakdownEditorRecord(record) {
                    const fieldGuide = record?.field_guide || this.breakdownEditor.fieldGuide || [];
                    this.breakdownEditor.record = record;
                    this.breakdownEditor.content = this.normalizeBreakdownEditorContent(record?.content, fieldGuide);
                    this.breakdownEditor.fieldGuide = fieldGuide;
                    if (this.breakdownEditor.source === 'topic-library') {
                        this.topicLibrary.breakdown = record;
                    } else {
                        this.creative.breakdown = record;
                    }
                },

                normalizeBreakdownEditorContent(content, fieldGuide) {
                    const source = content && typeof content === 'object' ? content : {};
                    return (fieldGuide || []).reduce((result, item) => {
                        const value = source[item.key];
                        result[item.key] = item.type === '数组'
                            ? (Array.isArray(value) ? value.map(entry => String(entry ?? '')).filter(Boolean) : [])
                            : String(value ?? '');
                        return result;
                    }, {});
                },

                addBreakdownArrayItem(key) {
                    if (!Array.isArray(this.breakdownEditor.content[key])) this.breakdownEditor.content[key] = [];
                    this.breakdownEditor.content[key].push('');
                },

                removeBreakdownArrayItem(key, index) {
                    this.breakdownEditor.content[key]?.splice(index, 1);
                },

                async saveBreakdownEditor() {
                    const record = this.breakdownEditor.record;
                    if (!record || record.status !== 'draft') return false;
                    const content = this.normalizeBreakdownEditorContent(this.breakdownEditor.content, this.breakdownEditor.fieldGuide);
                    this.breakdownEditor.saving = true;
                    this.breakdownEditor.error = '';
                    try {
                        const res = await aiworkerApi.request(`/api/creative/breakdowns/${record.id}`, {
                            method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({content})
                        });
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '保存拆解失败');
                        this.syncBreakdownEditorRecord(data);
                        return true;
                    } catch (e) {
                        this.breakdownEditor.error = e.message || '保存拆解失败';
                        return false;
                    } finally {
                        this.breakdownEditor.saving = false;
                    }
                },

                breakdownRecordStatusLabel(status) {
                    return {
                        draft: '草稿', submitted: '待审批', approved: '已批准',
                        returned: '已退回', superseded: '已替代', locked: '已批准'
                    }[status] || status || '草稿';
                },

                async submitBreakdownEditor() {
                    const record = this.breakdownEditor.record;
                    if (!record || record.status !== 'draft') return;
                    if (!await this.saveBreakdownEditor()) return;
                    this.breakdownEditor.saving = true;
                    try {
                        const tasksResponse = await aiworkerApi.request('/api/workflow/tasks');
                        const tasks = await tasksResponse.json();
                        if (!tasksResponse.ok) throw new Error(tasks.detail || '读取审批任务失败');
                        const task = (tasks || []).find(item =>
                            item.task_type === 'breakdown'
                            && ['assigned', 'in_progress', 'returned'].includes(item.status)
                            && Number(item.topic_id) === Number(record.topic_id)
                        );
                        if (!task) throw new Error('当前没有可提交的拆解任务，请先领取或开始任务');
                        if (['assigned', 'returned'].includes(task.status)) {
                            const startResponse = await aiworkerApi.request(`/api/workflow/tasks/${task.id}/start`, {method: 'POST'});
                            const started = await startResponse.json();
                            if (!startResponse.ok) throw new Error(started.detail || '开始拆解任务失败');
                            task.status = started.status;
                        }
                        const res = await aiworkerApi.request('/api/creative/workflow/submit', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({
                                workflow_task_id: task.id,
                                resource_type: 'breakdown',
                                resource_id: String(record.id),
                            }),
                        });
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '提交拆解失败');
                        record.status = 'submitted';
                        if (this.breakdownEditor.source === 'topic-library') await this.fetchTopicLibrary();
                        if (this.breakdownEditor.source === 'creative') await this.fetchCreativeTopics();
                        alert('拆解版本已提交审批。');
                    } catch (e) {
                        this.breakdownEditor.error = e.message || '提交拆解失败';
                    } finally {
                        this.breakdownEditor.saving = false;
                    }
                },

                async saveTopicLibraryBreakdown() {
                    return this.saveBreakdownEditor();
                },

                async lockTopicLibraryBreakdown() {
                    return this.submitBreakdownEditor();
                },

                async generateCreativeTopics() {
                    this.creative.loading = true;
                    this.creative.error = '';
                    try {
                        const res = await aiworkerApi.request('/api/creative/topics/generate', {
                            method: 'POST', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({platform: this.creative.platform, limit: 10})
                        });
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '生成选题失败');
                        this.creative.items = data.items || [];
                    } catch (e) {
                        this.creative.error = e.message || '生成选题失败';
                    } finally {
                        this.creative.loading = false;
                    }
                },

                async updateCreativeTopic(topic, status) {
                    const res = await aiworkerApi.request(`/api/creative/topics/${topic.id}`, {
                        method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({status})
                    });
                    const data = await res.json();
                    if (!res.ok) { alert(data.detail || '更新失败'); return; }
                    Object.assign(topic, data);
                },

                createCreativeBreakdown(topic) {
                    return this.startTranscriptBreakdown(topic, 'creative');
                },

                async submitCreativeBreakdown() {
                    return this.submitBreakdownEditor();
                },

}));

document.querySelector('[data-workspace-page="selection-breakdowns"]')?.setAttribute('data-ready', 'true');
