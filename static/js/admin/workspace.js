window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
                async fetchFiles() {
                    try {
                        const res = await aiworkerApi.request(`/api/files?filter=${this.fileFilter}`);
                        this.files = await res.json();
                        this.selectedFilePaths = this.selectedFilePaths.filter(path =>
                            this.files.some(file => file.path === path)
                        );
                        this.applyFileSearch();
                    } catch (e) {
                        this.files = [];
                        this.filteredFiles = [];
                        this.selectedFilePaths = [];
                    }
                },

                scrollToCrawlerSettings() {
                    document.getElementById('crawlerSettings')?.scrollIntoView({behavior: 'smooth', block: 'start'});
                },

                selectedJobPlatform() {
                    const job = this.jobs.find(item => item.job_id === this.selectedJobId);
                    if (job && ['dy', 'hg'].includes(job.platform)) return job.platform;
                    return 'dy';
                },

                async syncAnalyticsJob() {
                    if (!this.selectedJobId) {
                        this.analyticsError = '请选择一个 Job';
                        return;
                    }
                    this.analyticsLoading = true;
                    this.analyticsError = '';
                    this.analyticsSyncResult = null;
                    try {
                        const res = await aiworkerApi.request('/api/analytics/sync', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({
                                job_id: this.selectedJobId,
                                platform: this.selectedJobPlatform(),
                                rebuild: true
                            })
                        });
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || data.message || '同步失败');
                        this.analyticsSyncResult = data;
                        await this.reloadAnalyticsDashboard();
                    } catch (e) {
                        this.analyticsError = e.message || '同步失败';
                    } finally {
                        this.analyticsLoading = false;
                    }
                },

                async fetchAnalyticsAssets() {
                    try {
                        const res = await aiworkerApi.request('/api/analytics/assets');
                        this.analyticsAssets = await res.json();
                    } catch (e) {
                        this.analyticsAssets = {overview: {}, jobs: [], platform_distribution: []};
                    }
                },

                async refreshAnalyticsDashboard(syncCurrent = true) {
                    this.analyticsError = '';
                    if (syncCurrent) {
                        this.analyticsLoading = true;
                        try {
                            const platforms = this.analyticsPlatform ? [this.analyticsPlatform] : ['dy', 'hg'];
                            const res = await aiworkerApi.request('/api/analytics/sync-all', {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({platforms})
                            });
                            const data = await res.json();
                            if (!res.ok) throw new Error(data.detail || data.message || '同步失败');
                            this.analyticsSyncResult = {
                                status: data.status,
                                job_id: this.analyticsPlatform ? `${this.analyticsPlatform} 全部任务 ${data.synced_jobs || 0} 个` : `全部任务 ${data.synced_jobs || 0} 个`,
                                drama_count: data.drama_count || 0,
                                artifact_count: data.artifact_count || 0
                            };
                        } catch (e) {
                            this.analyticsError = e.message || '同步失败';
                        } finally {
                            this.analyticsLoading = false;
                        }
                    }
                    await this.reloadAnalyticsDashboard();
                },

                async syncAllAnalytics() {
                    this.analyticsLoading = true;
                    this.analyticsError = '';
                    try {
                        const res = await aiworkerApi.request('/api/analytics/sync-all', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({platforms: ['dy', 'hg']})
                        });
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || data.message || '同步全部失败');
                        this.analyticsSyncResult = {
                            status: data.status,
                            job_id: `全部任务 ${data.synced_jobs || 0} 个`,
                            drama_count: data.drama_count || 0,
                            artifact_count: data.artifact_count || 0
                        };
                        this.analyticsPlatform = '';
                        await this.reloadAnalyticsDashboard();
                    } catch (e) {
                        this.analyticsError = e.message || '同步全部失败';
                    } finally {
                        this.analyticsLoading = false;
                    }
                },

                async reloadAnalyticsDashboard() {
                    const baseParams = [];
                    if (this.analyticsPlatform) baseParams.push(`platform=${encodeURIComponent(this.analyticsPlatform)}`);
                    const nonce = `_=${Date.now()}`;
                    const commonSuffix = `?${[...baseParams, nonce].join('&')}`;
                    const dramaParams = [...baseParams, nonce];
                    if (this.analyticsDate) dramaParams.push(`date=${encodeURIComponent(this.analyticsDate)}`);
                    const dramaSuffix = `?${dramaParams.join('&')}`;
                    try {
                        const [overviewRes, tagsRes, trendsRes, sentimentRes, dramasRes] = await Promise.all([
                            aiworkerApi.request(`/api/analytics/overview${commonSuffix}`),
                            aiworkerApi.request(`/api/analytics/tags${commonSuffix}`),
                            aiworkerApi.request(`/api/analytics/trends${commonSuffix}&metric=heat`),
                            aiworkerApi.request(`/api/analytics/sentiment${commonSuffix}`),
                            aiworkerApi.request(`/api/analytics/dramas${dramaSuffix}&sort=heat&limit=30`)
                        ]);
                        this.analyticsOverview = await overviewRes.json();
                        this.analyticsTags = await tagsRes.json();
                        this.analyticsTrends = await trendsRes.json();
                        const sentiment = await sentimentRes.json();
                        const dramas = await dramasRes.json();
                        this.analyticsSentiment = sentiment.items || [];
                        this.analyticsDramas = dramas.items || [];
                        this.$nextTick(() => {
                            this.renderAnalyticsCharts();
                            setTimeout(() => this.resizeAnalyticsCharts(), 80);
                        });
                    } catch (e) {
                        this.analyticsOverview = {};
                        this.analyticsTags = {categories: [], selling_points: [], hooks: [], plot_tags: []};
                        this.analyticsTrends = {points: []};
                        this.analyticsSentiment = [];
                        this.analyticsDramas = [];
                    }
                },

                renderAnalyticsCharts() {
                    if (!window.echarts) return;
                    this.renderPieChart('categoryChart', this.analyticsTags.categories || [], '题材');
                    this.renderSentimentChart();
                    this.renderTrendChart();
                },

                resizeAnalyticsCharts() {
                    ['categoryChart', 'trendChart', 'sentimentChart'].forEach(id => {
                        const el = document.getElementById(id);
                        if (!el || !echarts.getInstanceByDom(el)) return;
                        echarts.getInstanceByDom(el).resize();
                    });
                },

                renderPieChart(elementId, rows, name) {
                    const el = document.getElementById(elementId);
                    if (!el) return;
                    const chart = echarts.getInstanceByDom(el) || echarts.init(el);
                    chart.clear();
                    chart.setOption({
                        backgroundColor: 'transparent',
                        tooltip: {trigger: 'item'},
                        series: [{
                            name,
                            type: 'pie',
                            radius: ['40%', '70%'],
                            data: rows.slice(0, 12).map(item => ({name: item.tag || '-', value: item.count || 0})),
                            label: {color: '#f1f4f8'}
                        }]
                    });
                },

                renderBarChart(elementId, rows, name) {
                    const el = document.getElementById(elementId);
                    if (!el) return;
                    const chart = echarts.getInstanceByDom(el) || echarts.init(el);
                    chart.clear();
                    const data = rows.slice(0, 10).reverse();
                    chart.setOption({
                        backgroundColor: 'transparent',
                        tooltip: {trigger: 'axis'},
                        grid: {left: 80, right: 20, top: 20, bottom: 30},
                        xAxis: {type: 'value', axisLabel: {color: '#9aa4b2'}},
                        yAxis: {type: 'category', data: data.map(item => this.shortLabel(item.tag || '-')), axisLabel: {color: '#f1f4f8', width: 72, overflow: 'truncate'}},
                        series: [{name, type: 'bar', data: data.map(item => item.count || 0), itemStyle: {color: '#3b95f6'}}]
                    });
                },

                renderSentimentChart() {
                    const el = document.getElementById('sentimentChart');
                    if (!el) return;
                    const chart = echarts.getInstanceByDom(el) || echarts.init(el);
                    chart.clear();
                    const labels = {positive: '正面', neutral: '中性', negative: '负面'};
                    chart.setOption({
                        backgroundColor: 'transparent',
                        tooltip: {trigger: 'item'},
                        series: [{
                            type: 'pie',
                            radius: '65%',
                            data: (this.analyticsSentiment || []).map(item => ({name: labels[item.sentiment] || item.sentiment, value: item.count || 0})),
                            label: {color: '#f1f4f8'}
                        }]
                    });
                },

                renderTrendChart() {
                    const el = document.getElementById('trendChart');
                    if (!el) return;
                    const chart = echarts.getInstanceByDom(el) || echarts.init(el);
                    chart.clear();
                    const points = this.analyticsTrends.points || [];
                    chart.setOption({
                        backgroundColor: 'transparent',
                        tooltip: {trigger: 'axis'},
                        grid: {left: 55, right: 20, top: 20, bottom: 35},
                        xAxis: {type: 'category', data: points.map(item => item.date), axisLabel: {color: '#9aa4b2'}},
                        yAxis: {type: 'value', axisLabel: {color: '#9aa4b2'}},
                        series: [{name: this.analyticsTrends.metric || 'value', type: 'line', smooth: true, data: points.map(item => item.value || 0), itemStyle: {color: '#20c997'}, areaStyle: {opacity: .12}}]
                    });
                },

                applyFileSearch() {
                    const query = String(this.fileSearch || '').trim().toLowerCase();
                    if (!query) {
                        this.filteredFiles = this.files;
                        return;
                    }
                    this.filteredFiles = this.files.filter(file =>
                        String(file.name || '').toLowerCase().includes(query) ||
                        String(file.path || '').toLowerCase().includes(query)
                    );
                },

                groupedFiles() {
                    const groupOrder = ['视频', '音频', '剧本与文稿', '转写数据', '元数据', '其他文件'];
                    const dramas = new Map();
                    for (const file of this.filteredFiles) {
                        const dramaName = file.drama_name || '其他数据';
                        if (!dramas.has(dramaName)) dramas.set(dramaName, {drama_name: dramaName, count: 0, groups: new Map()});
                        const drama = dramas.get(dramaName);
                        const typeName = file.file_group || '其他文件';
                        if (!drama.groups.has(typeName)) drama.groups.set(typeName, []);
                        drama.groups.get(typeName).push(file);
                        drama.count += 1;
                    }
                    return [...dramas.values()]
                        .sort((left, right) => left.drama_name === '其他数据' ? 1 : (right.drama_name === '其他数据' ? -1 : 0))
                        .map(group => ({
                            drama_name: group.drama_name,
                            count: group.count,
                            file_groups: [...group.groups.entries()]
                                .sort((left, right) => groupOrder.indexOf(left[0]) - groupOrder.indexOf(right[0]))
                                .map(([name, files]) => ({name, files})),
                        }));
                },

                isFileSelected(path) {
                    return this.selectedFilePaths.includes(path);
                },

                allVisibleFilesSelected() {
                    return this.filteredFiles.length > 0 && this.filteredFiles.every(file => this.isFileSelected(file.path));
                },

                startBatchDelete() {
                    this.selectedFilePaths = [];
                    this.batchDeleteMode = true;
                },

                cancelBatchDelete() {
                    this.selectedFilePaths = [];
                    this.batchDeleteMode = false;
                },

                toggleFileSelection(path, selected) {
                    if (selected && !this.isFileSelected(path)) {
                        this.selectedFilePaths.push(path);
                    }
                    if (!selected) {
                        this.selectedFilePaths = this.selectedFilePaths.filter(item => item !== path);
                    }
                },

                toggleAllVisibleFiles(selected) {
                    const visiblePaths = this.filteredFiles.map(file => file.path);
                    if (selected) {
                        this.selectedFilePaths = [...new Set([...this.selectedFilePaths, ...visiblePaths])];
                    } else {
                        this.selectedFilePaths = this.selectedFilePaths.filter(path => !visiblePaths.includes(path));
                    }
                },

                async deleteSelectedFiles() {
                    await this.deleteFiles(this.selectedFilePaths);
                },

                async deleteFiles(paths) {
                    const selectedPaths = [...new Set(paths || [])];
                    if (!selectedPaths.length) return;
                    const label = selectedPaths.length === 1 ? '该文件' : `已选的 ${selectedPaths.length} 个文件`;
                    if (!confirm(`确认删除${label}吗？本地文件将永久移除。`)) return;
                    try {
                        const res = await aiworkerApi.request('/api/files', {
                            method: 'DELETE',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({paths: selectedPaths})
                        });
                        const data = await res.json().catch(() => ({}));
                        if (!res.ok || data.status === 'error') {
                            alert(data.message || '删除失败，请刷新后重试');
                            return;
                        }
                        const deletedPaths = data.deleted_paths || [];
                        this.selectedFilePaths = this.selectedFilePaths.filter(path => !deletedPaths.includes(path));
                        await this.fetchFiles();
                        if (data.status === 'partial_success') {
                            alert(`已删除 ${deletedPaths.length} 个文件，${(data.failed || []).length} 个文件删除失败`);
                        } else {
                            alert(`已删除 ${deletedPaths.length} 个文件`);
                        }
                    } catch (e) {
                        alert('删除失败，请检查服务状态后重试');
                    }
                },

                async openFilePreview(file) {
                    const kind = file.preview_kind || 'none';
                    this.previewFile = {status: 'loading', file, kind, url: '', content: '', message: ''};
                    this.filePreviewModal.show();
                    if (['video', 'audio'].includes(kind)) {
                        this.previewFile.url = `/api/file/preview?path=${encodeURIComponent(file.path)}`;
                        this.previewFile.status = 'success';
                        return;
                    }
                    if (!['text', 'json'].includes(kind)) {
                        this.previewFile.status = 'error';
                        this.previewFile.message = '该文件格式暂不支持在线预览';
                        return;
                    }
                    try {
                        const res = await aiworkerApi.request(`/api/file/content?path=${encodeURIComponent(file.path)}`);
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.message || '读取文件失败');
                        this.previewFile.content = data.content || '';
                        this.previewFile.status = 'success';
                    } catch (e) {
                        this.previewFile.status = 'error';
                        this.previewFile.message = e.message || '读取文件失败';
                    }
                },

                closeFilePreview() {
                    this.previewFile = {status: 'idle', file: null, kind: '', url: '', content: '', message: ''};
                },

                async analyzeAudio(file) {
                    this.audioAnalysis = {status: 'loading', file_name: file.name};
                    this.audioModal.show();
                    try {
                        const res = await aiworkerApi.request('/api/audio/analyze', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({file_path: file.path})
                        });
                        const data = await res.json();
                        if (!res.ok || data.status === 'error') {
                            this.audioAnalysis = {status: 'error', message: data.message || '分析失败'};
                            return;
                        }
                        this.audioAnalysis = data;
                    } catch (e) {
                        this.audioAnalysis = {status: 'error', message: '分析失败'};
                    }
                },

                downloadAudioAnalysis() {
                    if (this.audioAnalysis.status !== 'success') return;
                    const lines = [
                        `文件：${this.audioAnalysis.file_name}`,
                        `时间：${this.audioAnalysis.analysis_time}`,
                        '',
                        '## 摘要',
                        this.audioAnalysis.summary || '',
                        '',
                        '## 关键点',
                        ...(this.audioAnalysis.key_points || []).map(item => `- ${item}`),
                        '',
                        '## 转写文本',
                        this.audioAnalysis.transcript || ''
                    ];
                    const blob = new Blob([lines.join('\n')], {type: 'text/markdown;charset=utf-8'});
                    const url = URL.createObjectURL(blob);
                    const link = document.createElement('a');
                    const stem = String(this.audioAnalysis.file_name || 'audio').replace(/\.[^.]+$/, '');
                    link.href = url;
                    link.download = `${stem}_analysis.md`;
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                    URL.revokeObjectURL(url);
                },

                async fetchConfigForm() {
                    try {
                        const res = await aiworkerApi.request('/api/config/form');
                        const data = await res.json();
                        if (data.status === 'error') {
                            alert(data.message || '加载配置失败');
                            return;
                        }
                        this.configForm = {
                            root: data.root || {},
                            items: (data.items || []).map(item => this.normalizeConfigItem(item))
                        };
                    } catch (e) {
                        alert('加载配置失败');
                    }
                },

                normalizeConfigItem(item = {}) {
                    return {
                        project: item.project || '',
                        '评论不少于': Number(item['评论不少于'] || 0),
                        '搜索关键词': item['搜索关键词'] || '',
                        '屏蔽关键词': item['屏蔽关键词'] || '',
                        '有关视频判定': item['有关视频判定'] || '',
                        '黑名单视频判定': item['黑名单视频判定'] || '',
                        '作品天数': Number(item['作品天数'] || 0),
                        '白名单地区': item['白名单地区'] || '',
                        '黑名单地区': item['黑名单地区'] || ''
                    };
                },

                addConfigItem() {
                    this.configForm.items.push(this.normalizeConfigItem({
                        '评论不少于': 3,
                        '作品天数': 90
                    }));
                },

                removeConfigItem(index) {
                    if (!confirm('删除该项目配置？保存后会写入 data.json。')) return;
                    this.configForm.items.splice(index, 1);
                },

                async saveConfigForm() {
                    const crawlerType = String(this.configForm.root['默认爬取类型'] || '').toLowerCase();
                    const isRank = crawlerType.includes('rank') || crawlerType.includes('榜单');
                    if (!isRank) {
                        if (this.configForm.items.length === 0) {
                            alert('搜索类型至少需要一个项目配置');
                            return;
                        }
                        const missing = this.configForm.items.findIndex(item => !String(item['搜索关键词'] || '').trim());
                        if (missing >= 0) {
                            alert(`第 ${missing + 1} 个项目缺少搜索关键词`);
                            return;
                        }
                    }
                    const res = await aiworkerApi.request('/api/config/form', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(this.configForm)
                    });
                    const data = await res.json();
                    if (!res.ok || data.status !== 'success') {
                        alert(data.message || '保存失败');
                        return;
                    }
                    alert('保存成功');
                    await this.loadConfig();
                    await this.fetchConfigForm();
                    await this.fetchBackups();
                },

                async fetchBackups() {
                    try {
                        const res = await aiworkerApi.request('/api/config/backups');
                        this.backups = await res.json();
                    } catch (e) {
                        this.backups = [];
                    }
                },

                async createBackup() {
                    const res = await aiworkerApi.request('/api/config/backup/create', {method: 'POST'});
                    const data = await res.json();
                    if (data.status !== 'success') alert(data.message || '创建失败');
                    await this.fetchBackups();
                },

                async restoreBackup(filename) {
                    if (!confirm(`恢复备份 ${filename}？当前配置会被覆盖。`)) return;
                    const res = await aiworkerApi.request('/api/config/backup/restore', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({filename})
                    });
                    const data = await res.json();
                    if (data.status !== 'success') alert(data.message || '恢复失败');
                    await this.fetchConfigForm();
                    await this.loadConfig();
                },

                async deleteBackup(filename) {
                    if (!confirm(`删除备份 ${filename}？`)) return;
                    const res = await aiworkerApi.request('/api/config/backup/delete', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({filename})
                    });
                    const data = await res.json();
                    if (data.status !== 'success') alert(data.message || '删除失败');
                    await this.fetchBackups();
                },

                async openProjectModal(filePath, mode) {
                    this.modalMode = mode;
                    this.modalFile = filePath;
                    this.selectedModalProjects = [];
                    this.modalProjects = [];
                    try {
                        const res = await aiworkerApi.request('/api/file/analyze', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({file_path: filePath})
                        });
                        const data = await res.json();
                        this.modalProjects = Object.entries(data)
                            .map(([name, count]) => ({name, count}))
                            .sort((a, b) => a.name.localeCompare(b.name));
                        this.selectedModalProjects = this.modalProjects.map(item => item.name);
                        this.projectModal.show();
                    } catch (e) {
                        alert('分析文件失败');
                    }
                },

                moveProjectUp(index) {
                    if (index <= 0) return;
                    const item = this.modalProjects[index];
                    this.modalProjects[index] = this.modalProjects[index - 1];
                    this.modalProjects[index - 1] = item;
                },

                moveProjectDown(index) {
                    if (index >= this.modalProjects.length - 1) return;
                    const item = this.modalProjects[index];
                    this.modalProjects[index] = this.modalProjects[index + 1];
                    this.modalProjects[index + 1] = item;
                },

                async confirmBatchAction() {
                    if (this.selectedModalProjects.length === 0) {
                        alert('请至少选择一个项目');
                        return;
                    }
                    const projects = this.modalProjects
                        .filter(item => this.selectedModalProjects.includes(item.name))
                        .map(item => item.name);
                    const endpoint = this.modalMode === 'crawl' ? '/api/batch/run' : '/api/export/excel';
                    const res = await aiworkerApi.request(endpoint, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            file_path: this.modalFile,
                            projects,
                            auto_upload: this.autoUpload
                        })
                    });
                    const data = await res.json();
                    alert(data.message || '已提交');
                    this.projectModal.hide();
                },

                async fetchQrcodeStatus() {
                    try {
                        const res = await aiworkerApi.request('/api/qrcode/status');
                        this.qrcodeStatus = await res.json();
                    } catch (e) {}
                },

                async onLoginTypeChange() {
                    if (this.config.login_type === 'qrcode') await this.fetchQrcodeStatus();
                },

                onPlatformChange() {
                    if (this.config.crawler_type === 'rank' && !['dy', 'hg'].includes(this.config.platform)) {
                        this.config.crawler_type = 'search';
                        alert('榜单爬取仅支持抖音和红果，已切换为搜索类型');
                    }
                },

                async startQrcodeLogin() {
                    if (this.isRunning) {
                        alert('爬虫正在运行，请先停止');
                        return;
                    }
                    this.qrcodeLoggingIn = true;
                    try {
                        const res = await aiworkerApi.request('/api/qrcode/login', {method: 'POST'});
                        const data = await res.json();
                        if (data.status === 'success') {
                            this.pollQrcodeStatus();
                        } else {
                            alert(data.message || '启动扫码失败');
                            this.qrcodeLoggingIn = false;
                        }
                    } catch (e) {
                        alert('启动扫码失败');
                        this.qrcodeLoggingIn = false;
                    }
                },

                pollQrcodeStatus() {
                    const timer = setInterval(async () => {
                        await this.fetchQrcodeStatus();
                        if (this.qrcodeStatus.has_state) {
                            clearInterval(timer);
                            this.qrcodeLoggingIn = false;
                            alert('扫码登录成功');
                        }
                    }, 3000);
                    setTimeout(() => {
                        clearInterval(timer);
                        this.qrcodeLoggingIn = false;
                    }, 300000);
                },

                async clearQrcodeLogin() {
                    if (!confirm('清除保存的登录状态？')) return;
                    const res = await aiworkerApi.request('/api/qrcode/clear', {method: 'POST'});
                    const data = await res.json();
                    if (data.status !== 'success') alert(data.message || '清除失败');
                    await this.fetchQrcodeStatus();
                },

                async fetchDramaSettings() {
                    try {
                        const response = await aiworkerApi.request('/api/hongguo/drama/settings');
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.message || '读取设置失败');
                        this.dramaAnalysis.settings = data;
                        this.dramaAnalysis.activationCode = '';
                    } catch (error) {
                        this.dramaAnalysis.error = error.message || '读取剧目检索设置失败';
                    }
                },

                async saveDramaSettings() {
                    this.dramaAnalysis.loading = true;
                    this.dramaAnalysis.error = '';
                    try {
                        const payload = {config_url: this.dramaAnalysis.settings.config_url};
                        if (this.dramaAnalysis.activationCode) payload.activation_code = this.dramaAnalysis.activationCode;
                        const response = await aiworkerApi.request('/api/hongguo/drama/settings', {
                            method: 'PUT',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(payload),
                        });
                        const data = await response.json();
                        if (!response.ok || data.status === 'error') throw new Error(data.message || '保存设置失败');
                        this.dramaAnalysis.settings = data;
                        this.dramaAnalysis.activationCode = '';
                        alert('设置已保存');
                    } catch (error) {
                        this.dramaAnalysis.error = error.message || '保存设置失败';
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
                        const response = await aiworkerApi.request('/api/hongguo/drama/test', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(payload),
                        });
                        const data = await response.json();
                        this.dramaAnalysis.interfaceTest = {
                            status: data.status || (response.ok ? 'success' : 'error'),
                            message: data.message || (response.ok ? '接口可用' : '接口不可用'),
                        };
                    } catch (error) {
                        this.dramaAnalysis.interfaceTest = {status: 'error', message: error.message || '接口测试失败'};
                    } finally {
                        this.dramaAnalysis.loading = false;
                    }
                },

}));
