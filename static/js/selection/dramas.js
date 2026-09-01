window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
                dramaReviewStatusLabel() {
                    return {queued: '等待采集', running: '采集中', completed: '已完成', failed: '失败'}[this.dramaReviews.status] || '未开始';
                },

                async startDramaReviewCapture() {
                    const state = this.dramaReviews;
                    const title = String(state.title || '').trim();
                    const limit = Number(state.limit || 0);
                    if (!title) {
                        state.error = '请输入剧名';
                        return;
                    }
                    if (!state.allReviews && (!Number.isInteger(limit) || limit < 1 || limit > 10000)) {
                        state.error = '数量限制需为 1 到 10000 的整数';
                        return;
                    }
                    state.loading = true;
                    state.error = '';
                    state.items = [];
                    const topicKey = this.dramaReviewTopicKey(state.topic);
                    const requestState = {...state};
                    try {
                        const endpoint = this.isDouyinDramaReview(state) ? '/api/douyin/reviews/jobs' : '/api/hongguo/reviews/capture';
                        const payload = this.isDouyinDramaReview(state)
                            ? {topic_id: state.topic?.id, title, limit, all_reviews: Boolean(state.allReviews)}
                            : {title, limit, all_reviews: Boolean(state.allReviews)};
                        const response = await aiworkerApi.request(endpoint, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(payload)
                        });
                        const data = await this.parseDramaReviewResponse(response);
                        if (!response.ok) throw new Error(data.detail || data.message || '剧评检索失败');
                        const jobState = {...requestState, loading: false, polling: false};
                        this.applyDramaReviewCapture(data, jobState);
                        await this.pollDramaReviewCapture(jobState);
                    } catch (error) {
                        if (this.dramaReviewTopicKey(this.dramaReviews.topic) === topicKey) this.dramaReviews.error = error.message || '剧评检索失败';
                    } finally {
                        if (this.dramaReviewTopicKey(this.dramaReviews.topic) === topicKey) this.dramaReviews.loading = false;
                    }
                },

                downloadDramaReviews() {
                    if (!this.dramaReviews.jobId || this.dramaReviews.status !== 'completed') return;
                    const state = this.dramaReviews;
                    const root = this.isDouyinDramaReview(state) ? '/api/douyin/reviews/jobs' : '/api/hongguo/reviews/jobs';
                    window.open(`${root}/${encodeURIComponent(state.jobId)}/download?format=${encodeURIComponent(state.format || 'csv')}`, '_blank', 'noopener');
                },

                async searchDrama() {
                    const title = this.dramaAnalysis.title.trim();
                    if (!title) return;
                    const requestId = (Number(this.dramaAnalysis.searchRequestId) || 0) + 1;
                    this.dramaAnalysis.searchRequestId = requestId;
                    this.dramaAnalysis.seriesRequestId = (Number(this.dramaAnalysis.seriesRequestId) || 0) + 1;
                    this.dramaAnalysis.loading = true;
                    this.dramaAnalysis.loadingMessage = '正在检索剧目来源…';
                    this.dramaAnalysis.seriesLoading = false;
                    this.dramaAnalysis.error = '';
                    this.dramaAnalysis.candidates = [];
                    this.dramaAnalysis.providers = [];
                    this.dramaAnalysis.captureCandidates = [];
                    this.dramaAnalysis.thirdPartyError = '';
                    this.dramaAnalysis.captureError = '';
                    this.dramaAnalysis.topic = null;
                    this.dramaAnalysis.selected = null;
                    this.dramaAnalysis.episodes = [];
                    this.dramaAnalysis.episode = 0;
                    this.dramaAnalysis.searchSource = '';
                    this.dramaAnalysis.fallbackReason = '';
                    this.dramaReviews.platform = this.dramaAnalysis.platform;
                    try {
                        const endpoint = this.dramaAnalysis.platform === 'dy' ? '/api/douyin/drama/search' : '/api/hongguo/drama/download/search';
                        const res = await aiworkerApi.request(endpoint, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({title})
                        });
                        const data = await res.json();
                        if (requestId !== this.dramaAnalysis.searchRequestId) return;
                        if (!res.ok || data.status === 'error') throw new Error(data.message || '搜索失败');
                        this.dramaAnalysis.candidates = data.candidates || [];
                        this.dramaAnalysis.providers = data.providers || [];
                        this.dramaAnalysis.captureCandidates = data.capture_candidates || [];
                        this.dramaAnalysis.thirdPartyError = data.third_party_error || '';
                        this.dramaAnalysis.captureError = data.capture_error || '';
                        this.dramaAnalysis.searchSource = data.source || (this.dramaAnalysis.platform === 'dy' ? 'douyin' : 'hongguo_capture');
                        this.dramaAnalysis.fallbackReason = data.fallback_reason || '';
                        if (!this.dramaAnalysis.candidates.length && !this.dramaAnalysis.captureCandidates.length) this.dramaAnalysis.error = data.message || '未找到匹配剧目';
                    } catch (e) {
                        if (requestId === this.dramaAnalysis.searchRequestId) {
                            this.dramaAnalysis.error = e.message || '搜索剧目失败';
                        }
                    } finally {
                        if (requestId === this.dramaAnalysis.searchRequestId) {
                            this.dramaAnalysis.loading = false;
                        }
                    }
                },

                isDouyinDramaSearch() {
                    return this.dramaAnalysis.platform === 'dy' || this.dramaAnalysis.searchSource === 'douyin';
                },

                captureStatusLabel(status) {
                    return {
                        ready_clear: '明文可下载',
                        ready_encrypted: '已抓包，可解密',
                        waiting_capture: '等待抓包',
                        expired: '地址已过期，请重新播放',
                        paid: '付费剧集（跳过）'
                    }[status] || '等待抓包';
                },

                selectedDramaEpisode() {
                    return this.dramaAnalysis.episodes.find(
                        item => Number(item.episode) === Number(this.dramaAnalysis.episode)
                    ) || null;
                },

                changeDramaSearchPlatform() {
                    this.dramaAnalysis.searchRequestId = (Number(this.dramaAnalysis.searchRequestId) || 0) + 1;
                    this.dramaAnalysis.seriesRequestId = (Number(this.dramaAnalysis.seriesRequestId) || 0) + 1;
                    this.dramaAnalysis.loading = false;
                    this.dramaAnalysis.seriesLoading = false;
                    this.dramaAnalysis.candidates = [];
                    this.dramaAnalysis.providers = [];
                    this.dramaAnalysis.captureCandidates = [];
                    this.dramaAnalysis.thirdPartyError = '';
                    this.dramaAnalysis.captureError = '';
                    this.dramaAnalysis.topic = null;
                    this.dramaAnalysis.selected = null;
                    this.dramaAnalysis.episodes = [];
                    this.dramaAnalysis.episode = 0;
                    this.dramaAnalysis.searchSource = '';
                    this.dramaAnalysis.fallbackReason = '';
                    this.dramaAnalysis.error = '';
                    this.dramaReviews.platform = this.dramaAnalysis.platform;
                },

                async selectDramaCandidate(candidate) {
                    const requestId = (Number(this.dramaAnalysis.seriesRequestId) || 0) + 1;
                    this.dramaAnalysis.seriesRequestId = requestId;
                    this.dramaAnalysis.error = '';
                    this.dramaAnalysis.selected = candidate;
                    this.dramaAnalysis.episodes = [];
                    this.dramaAnalysis.episode = 0;
                    if (this.isDouyinDramaSearch()) return;
                    this.dramaAnalysis.seriesLoading = true;
                    try {
                        const res = await aiworkerApi.request('/api/hongguo/drama/episodes', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({
                                book_id: candidate.book_id || candidate.series_ref,
                                series_ref: candidate.series_ref || candidate.book_id,
                                title: candidate.title || this.dramaAnalysis.title,
                                source_platform: candidate.source_platform || candidate.provider_id || 'hongguo_capture',
                                provider_id: candidate.provider_id || candidate.source_platform || 'hongguo_capture'
                            })
                        });
                        const data = await res.json();
                        if (requestId !== this.dramaAnalysis.seriesRequestId) return;
                        if (!res.ok || data.status === 'error') throw new Error(data.message || '读取剧集失败');
                        this.dramaAnalysis.episodes = data.episodes || [];
                        const firstSelectable = this.dramaAnalysis.episodes.find(item => !this.isDramaEpisodePaid(item));
                        this.dramaAnalysis.episode = firstSelectable?.episode || 0;
                    } catch (e) {
                        if (requestId === this.dramaAnalysis.seriesRequestId) {
                            this.dramaAnalysis.error = e.message || '读取剧集失败';
                            this.dramaAnalysis.selected = null;
                        }
                    } finally {
                        if (requestId === this.dramaAnalysis.seriesRequestId) {
                            this.dramaAnalysis.seriesLoading = false;
                        }
                    }
                },

                async startDramaAnalysis() {
                    const selected = this.dramaAnalysis.selected;
                    const isDouyinSearch = this.isDouyinDramaSearch();
                    if (!selected || (isDouyinSearch ? !selected.aweme_id : !this.dramaAnalysis.episode) || this.isDramaEpisodePaid(this.selectedDramaEpisode())) return;
                    this.dramaAnalysis.loading = true;
                    this.dramaAnalysis.loadingMessage = '正在采纳选题并创建下载任务…';
                    this.dramaAnalysis.error = '';
                    try {
                        const endpoint = isDouyinSearch
                            ? '/api/hongguo/drama/douyin-fallback/jobs'
                            : '/api/hongguo/drama/download/jobs';
                        const jobPayload = isDouyinSearch
                            ? {
                                title: this.dramaAnalysis.title || selected.title,
                                aweme_id: selected.aweme_id,
                                fallback_reason: this.dramaAnalysis.fallbackReason
                            }
                            : {
                                title: selected.title || this.dramaAnalysis.title,
                                book_id: selected.book_id || selected.series_ref,
                                series_ref: selected.series_ref || selected.book_id,
                                episodes: [this.dramaAnalysis.episode],
                                all_episodes: false,
                                source_platform: selected.source_platform || selected.provider_id || 'hongguo_capture',
                                provider_id: selected.provider_id || selected.source_platform || 'hongguo_capture'
                            };
                        const res = await aiworkerApi.request(endpoint, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(jobPayload)
                        });
                        const data = await res.json();
                        if (!res.ok || data.status === 'error') throw new Error(data.detail || data.message || '创建下载任务失败');
                        if (data.topic) this.dramaAnalysis.topic = data.topic;
                        this.dramaAnalysis.jobId = data.job.job_id;
                        this.dramaAnalysis.notifiedStatus = '';
                        this.selectedJobId = data.job.job_id;
                        await this.fetchJobs();
                        await this.fetchLogs();
                        const sourceName = this.dramaProviderLabel(selected.provider_id || selected.source_platform);
                        alert(isDouyinSearch
                            ? '抖音下载任务已创建，正在下载已确认的视频并生成转写。完成后会弹窗提醒。'
                            : (selected.provider_id || selected.source_platform) === 'hongguo_capture'
                                ? `剧集下载任务已创建。请在红果 App 播放第 ${this.dramaAnalysis.episode} 集，系统将在等待时限内获取新抓包并下载。`
                                : `已创建 ${sourceName} 下载任务。`);
                    } catch (e) {
                        this.dramaAnalysis.error = e.message || '创建下载任务失败';
                    } finally {
                        this.dramaAnalysis.loading = false;
                    }
                },

                isDramaCandidateSelected(candidate) {
                    const selected = this.dramaAnalysis.selected;
                    if (!selected || !candidate) return false;
                    const provider = candidate.provider_id || candidate.source_platform || 'hongguo_capture';
                    const selectedProvider = selected.provider_id || selected.source_platform || 'hongguo_capture';
                    const seriesRef = candidate.series_ref || candidate.book_id || candidate.aweme_id;
                    const selectedSeriesRef = selected.series_ref || selected.book_id || selected.aweme_id;
                    return provider === selectedProvider && seriesRef === selectedSeriesRef;
                },

                dramaProviderLabel(providerId) {
                    const id = providerId || 'hongguo_capture';
                    if (id === 'hongguo_capture') return '抓包来源';
                    return this.dramaAnalysis.providers.find(item => item.provider_id === id)?.display_name || id;
                },

                isDramaEpisodePaid(episode) {
                    return Boolean(episode?.is_paid) || episode?.capture_status === 'paid';
                },

                dramaEpisodeStatusLabel(episode) {
                    if (this.isDramaEpisodePaid(episode)) return episode?.paid_reason || '付费剧集（跳过）';
                    return (this.dramaAnalysis.selected?.provider_id || this.dramaAnalysis.selected?.source_platform) === 'hongguo_capture'
                        ? this.captureStatusLabel(episode?.capture_status)
                        : '可下载';
                },

                dramaJobArtifacts() {
                    return this.jobs.find(job => job.job_id === this.dramaAnalysis.jobId)?.artifacts || [];
                },

                dramaAnalysisArtifactName(item) {
                    return String(item?.path || '').replace(/\\/g, '/').split('/').pop() || '未命名文件';
                },

                isDramaAnalysisVideoArtifact(item) {
                    return /\.(mp4|webm|m4v|mov|ogv)$/i.test(this.dramaAnalysisArtifactName(item));
                },

                openDramaAnalysisVideo(item) {
                    if (!this.isDramaAnalysisVideoArtifact(item) || !item?.path) return;
                    this.dramaAnalysis.videoPreviewPath = item.path;
                    this.dramaAnalysis.videoPreviewUrl = `/api/file/preview?path=${encodeURIComponent(item.path)}`;
                    this.dramaAnalysis.videoPreviewError = '';
                    this.$nextTick(() => document.getElementById('dramaAnalysisVideo')?.play().catch(() => {}));
                },

                closeDramaAnalysisVideo() {
                    document.getElementById('dramaAnalysisVideo')?.pause();
                    this.dramaAnalysis.videoPreviewPath = '';
                    this.dramaAnalysis.videoPreviewUrl = '';
                    this.dramaAnalysis.videoPreviewError = '';
                },

                dramaCurrentJob() {
                    return this.jobs.find(job => job.job_id === this.dramaAnalysis.jobId) || null;
                },

                dramaJobStatusLabel(status) {
                    return {
                        queued: '排队中',
                        running: '进行中',
                        paused: '已暂停',
                        succeeded: '已完成',
                        failed: '失败',
                        cancelled: '已取消'
                    }[status] || '等待更新';
                },

                formatDramaDuration(value) {
                    const seconds = Math.max(0, Math.round(Number(value) / 1000));
                    if (!seconds) return '未知';
                    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
                },

                notifyDramaJobCompletion() {
                    const job = this.jobs.find(item => item.job_id === this.dramaAnalysis.jobId);
                    if (!job || !['succeeded', 'failed', 'cancelled'].includes(job.status)) return;
                    const marker = `${job.job_id}:${job.status}`;
                    if (this.dramaAnalysis.notifiedStatus === marker) return;
                    this.dramaAnalysis.notifiedStatus = marker;
                    if (job.status === 'succeeded') {
                        const artifactCount = (job.artifacts || []).length;
                        const source = job.job?.crawler_type === 'douyin_fallback_analysis' ? '抖音降级视频' : '剧目';
                        alert(`${source}下载与解析完成，已生成 ${artifactCount} 个本地文件。`);
                        return;
                    }
                    alert(`剧目下载未完成：${job.message || job.status}`);
                },

}));

document.querySelector('[data-workspace-page="selection-dramas"]')?.setAttribute('data-ready', 'true');
