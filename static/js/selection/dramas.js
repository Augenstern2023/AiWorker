window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
                async fetchDramaSettings() {
                    try {
                        const res = await aiworkerApi.request('/api/hongguo/drama/settings');
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.message || '读取设置失败');
                        this.dramaAnalysis.settings = data;
                        this.dramaAnalysis.activationCode = '';
                    } catch (e) {
                        this.dramaAnalysis.error = e.message || '读取剧目检索设置失败';
                    }
                },

                async saveDramaSettings() {
                    this.dramaAnalysis.loading = true;
                    this.dramaAnalysis.error = '';
                    try {
                        const payload = {config_url: this.dramaAnalysis.settings.config_url};
                        if (this.dramaAnalysis.activationCode) payload.activation_code = this.dramaAnalysis.activationCode;
                        const res = await aiworkerApi.request('/api/hongguo/drama/settings', {
                            method: 'PUT',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(payload)
                        });
                        const data = await res.json();
                        if (!res.ok || data.status === 'error') throw new Error(data.message || '保存设置失败');
                        this.dramaAnalysis.settings = data;
                        this.dramaAnalysis.activationCode = '';
                        alert('设置已保存');
                    } catch (e) {
                        this.dramaAnalysis.error = e.message || '保存设置失败';
                    } finally {
                        this.dramaAnalysis.loading = false;
                    }
                },

                async testDramaInterface() {
                    this.dramaAnalysis.loading = true;
                    this.dramaAnalysis.error = '';
                    this.dramaAnalysis.interfaceTest = null;
                    try {
                        const payload = {config_url: this.dramaAnalysis.settings.config_url};
                        if (this.dramaAnalysis.activationCode) payload.activation_code = this.dramaAnalysis.activationCode;
                        const res = await aiworkerApi.request('/api/hongguo/drama/test', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(payload)
                        });
                        const data = await res.json();
                        this.dramaAnalysis.interfaceTest = {
                            status: data.status || (res.ok ? 'success' : 'error'),
                            message: data.message || (res.ok ? '接口可用' : '接口不可用')
                        };
                    } catch (e) {
                        this.dramaAnalysis.interfaceTest = {status: 'error', message: e.message || '接口测试失败'};
                    } finally {
                        this.dramaAnalysis.loading = false;
                    }
                },

                async searchDrama() {
                    const title = this.dramaAnalysis.title.trim();
                    if (!title) return;
                    clearTimeout(this.dramaAnalysis.suggestionTimer);
                    this.dramaAnalysis.suggestionRequestId += 1;
                    this.dramaAnalysis.suggestions = [];
                    this.dramaAnalysis.loading = true;
                    this.dramaAnalysis.error = '';
                    this.dramaAnalysis.candidates = [];
                    this.dramaAnalysis.selected = null;
                    this.dramaAnalysis.episodes = [];
                    this.dramaAnalysis.episode = 0;
                    this.dramaAnalysis.searchSource = '';
                    this.dramaAnalysis.fallbackReason = '';
                    try {
                        const res = await aiworkerApi.request('/api/hongguo/drama/search', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({title})
                        });
                        const data = await res.json();
                        if (!res.ok || data.status === 'error') throw new Error(data.message || '搜索失败');
                        this.dramaAnalysis.candidates = data.candidates || [];
                        this.dramaAnalysis.searchSource = data.source || 'hongguo';
                        this.dramaAnalysis.fallbackReason = data.fallback_reason || '';
                        if (!this.dramaAnalysis.candidates.length) this.dramaAnalysis.error = '未找到匹配剧目';
                    } catch (e) {
                        this.dramaAnalysis.error = e.message || '搜索剧目失败';
                    } finally {
                        this.dramaAnalysis.loading = false;
                    }
                },

                onDramaTitleInput() {
                    clearTimeout(this.dramaAnalysis.suggestionTimer);
                    const requestId = ++this.dramaAnalysis.suggestionRequestId;
                    this.dramaAnalysis.suggestions = [];
                    const title = this.dramaAnalysis.title.trim();
                    if (title.length < 2) return;
                    this.dramaAnalysis.suggestionTimer = setTimeout(async () => {
                        this.dramaAnalysis.suggestionLoading = true;
                        try {
                            const res = await aiworkerApi.request('/api/hongguo/drama/suggest', {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({title})
                            });
                            const data = await res.json();
                            if (requestId !== this.dramaAnalysis.suggestionRequestId) return;
                            this.dramaAnalysis.suggestions = res.ok && data.status !== 'error' ? (data.candidates || []) : [];
                        } catch (e) {
                            if (requestId === this.dramaAnalysis.suggestionRequestId) this.dramaAnalysis.suggestions = [];
                        } finally {
                            if (requestId === this.dramaAnalysis.suggestionRequestId) this.dramaAnalysis.suggestionLoading = false;
                        }
                    }, 350);
                },

                chooseDramaSuggestion(suggestion) {
                    this.dramaAnalysis.title = suggestion.title || this.dramaAnalysis.title;
                    this.dramaAnalysis.suggestions = [];
                    this.searchDrama();
                },

                async selectDramaCandidate(candidate) {
                    this.dramaAnalysis.error = '';
                    this.dramaAnalysis.selected = candidate;
                    this.dramaAnalysis.episodes = [];
                    this.dramaAnalysis.episode = 0;
                    if (this.dramaAnalysis.searchSource === 'douyin_fallback') return;
                    this.dramaAnalysis.loading = true;
                    try {
                        const res = await aiworkerApi.request('/api/hongguo/drama/episodes', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({book_id: candidate.book_id})
                        });
                        const data = await res.json();
                        if (!res.ok || data.status === 'error') throw new Error(data.message || '读取剧集失败');
                        this.dramaAnalysis.episodes = data.episodes || [];
                        this.dramaAnalysis.episode = this.dramaAnalysis.episodes[0]?.episode || 0;
                    } catch (e) {
                        this.dramaAnalysis.error = e.message || '读取剧集失败';
                        this.dramaAnalysis.selected = null;
                    } finally {
                        this.dramaAnalysis.loading = false;
                    }
                },

                async startDramaAnalysis() {
                    const selected = this.dramaAnalysis.selected;
                    const isDouyinFallback = this.dramaAnalysis.searchSource === 'douyin_fallback';
                    if (!selected || (isDouyinFallback ? !selected.aweme_id : !this.dramaAnalysis.episode)) return;
                    this.dramaAnalysis.loading = true;
                    this.dramaAnalysis.error = '';
                    try {
                        const endpoint = isDouyinFallback
                            ? '/api/hongguo/drama/douyin-fallback/jobs'
                            : '/api/hongguo/drama/jobs';
                        const jobPayload = isDouyinFallback
                            ? {
                                title: this.dramaAnalysis.title || selected.title,
                                aweme_id: selected.aweme_id,
                                fallback_reason: this.dramaAnalysis.fallbackReason
                            }
                            : {
                                title: selected.title || this.dramaAnalysis.title,
                                book_id: selected.book_id,
                                episode: this.dramaAnalysis.episode
                            };
                        const res = await aiworkerApi.request(endpoint, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(jobPayload)
                        });
                        const data = await res.json();
                        if (!res.ok || data.status === 'error') throw new Error(data.message || '创建下载任务失败');
                        this.dramaAnalysis.jobId = data.job.job_id;
                        this.dramaAnalysis.notifiedStatus = '';
                        this.selectedJobId = data.job.job_id;
                        await this.fetchJobs();
                        await this.fetchLogs();
                        alert(isDouyinFallback
                            ? '抖音降级下载任务已创建，正在下载已确认的视频并生成转写。完成后会弹窗提醒。'
                            : '剧集下载任务已创建，正在下载并生成转写。完成后会弹窗提醒。');
                    } catch (e) {
                        this.dramaAnalysis.error = e.message || '创建下载任务失败';
                    } finally {
                        this.dramaAnalysis.loading = false;
                    }
                },

                dramaJobArtifacts() {
                    return this.jobs.find(job => job.job_id === this.dramaAnalysis.jobId)?.artifacts || [];
                },

                formatDramaDuration(value) {
                    const seconds = Math.max(0, Math.round(Number(value) / 1000));
                    if (!seconds) return '未知';
                    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
                },

                formatDramaBytes(value) {
                    const bytes = Math.max(0, Number(value || 0));
                    if (bytes < 1024) return `${bytes} B`;
                    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
                    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
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
