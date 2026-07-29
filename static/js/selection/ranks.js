window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
                async fetchTodayRank() {
                    this.todayRankLoading = true;
                    this.todayRankError = '';
                    const params = new URLSearchParams({
                        platform: this.todayRankPlatform || 'dy',
                        _: String(Date.now())
                    });
                    if (this.todayRankDate) params.set('date', this.todayRankDate);
                    try {
                        const res = await aiworkerApi.request(`/api/rank/today?${params.toString()}`);
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '加载今日榜单失败');
                        if (this.todayRankDate && !(data.available_dates || []).includes(this.todayRankDate)) {
                            this.todayRankDate = '';
                            this.todayRankData = data;
                            await this.fetchTodayRank();
                            return;
                        }
                        this.todayRankData = data;
                        if (this.todayRankPlatform === 'hg') {
                            this.ensureHongguoRoute(true);
                        } else {
                            this.ensureDouyinRoute(true);
                        }
                    } catch (e) {
                        this.todayRankData = null;
                        this.todayRankError = e.message || '加载今日榜单失败';
                    } finally {
                        this.todayRankLoading = false;
                    }
                },

                async refreshTodayRank() {
                    if (this.todayRankPlatform !== 'dy') {
                        await this.fetchTodayRank();
                        return;
                    }
                    if (this.todayRankRefreshing) return;
                    this.todayRankRefreshing = true;
                    this.todayRankRefreshMessage = '正在抓取抖音榜单...';
                    this.todayRankError = '';
                    try {
                        const response = await aiworkerApi.request('/api/jobs', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({
                                platform: 'dy',
                                login_type: 'qrcode',
                                crawler_type: 'rank',
                                keywords: '',
                                start_page: 1,
                                headless: false,
                                save_option: 'json',
                                get_comment: false,
                                get_sub_comment: false,
                                extra: {
                                    DOUYIN_RANK_LIMIT: 50,
                                    DOUYIN_RANK_COMMENT_LIMIT: 10,
                                    DOUYIN_AUTO_ANALYZE_TOP10: true,
                                    DOUYIN_ANALYSIS_TOP_N: 10,
                                    DOUYIN_ANALYSIS_PER_CATEGORY: true,
                                    DOUYIN_ANALYSIS_WITH_LLM: true
                                }
                            })
                        });
                        const data = await response.json().catch(() => ({}));
                        if (!response.ok || !data.job?.job_id) {
                            throw new Error(data.detail || data.message || '抖音榜单抓取任务创建失败');
                        }
                        this.todayRankRefreshJobId = data.job.job_id;
                        this.todayRankRefreshMessage = '抖音榜单抓取任务已启动...';
                        this.pollTodayRankRefresh();
                    } catch (error) {
                        this.todayRankRefreshing = false;
                        this.todayRankRefreshMessage = '';
                        this.todayRankError = error.message || '抖音榜单抓取任务创建失败';
                    }
                },

                async pollTodayRankRefresh() {
                    if (!this.todayRankRefreshJobId) return;
                    try {
                        const response = await aiworkerApi.request(`/api/jobs/${encodeURIComponent(this.todayRankRefreshJobId)}`);
                        const job = await response.json().catch(() => ({}));
                        if (!response.ok) throw new Error(job.detail || '读取抖音榜单任务失败');
                        if (['pending', 'running', 'stopping'].includes(job.status)) {
                            this.todayRankRefreshMessage = job.message || '正在抓取抖音榜单...';
                            window.setTimeout(() => this.pollTodayRankRefresh(), 1000);
                            return;
                        }
                        this.todayRankRefreshing = false;
                        this.todayRankRefreshJobId = '';
                        if (job.status !== 'succeeded') {
                            throw new Error(job.message || '抖音榜单抓取失败');
                        }
                        this.todayRankRefreshMessage = '抖音榜单已更新';
                        await this.fetchJobs();
                        await this.fetchTodayRank();
                    } catch (error) {
                        this.todayRankRefreshing = false;
                        this.todayRankRefreshJobId = '';
                        this.todayRankRefreshMessage = '';
                        this.todayRankError = error.message || '抖音榜单抓取失败';
                    }
                },

                rankAdoptionTitleKey(title) {
                    return String(title || '').normalize('NFKC').toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
                },

                isRankAdoptionPending(item) {
                    const recordId = String(item?.record_id || item?.id || '');
                    return Boolean(recordId && this.rankAdoptingRecordIds[recordId]);
                },

                isRankTitleAdopted(title) {
                    const key = this.rankAdoptionTitleKey(title);
                    return Boolean(key && this.rankAdoptedTitleKeys[key]);
                },

                rankAdoptionFailureMessage(status, detail) {
                    if (status === 409) return detail || '剧目已收录。';
                    if (status === 404) return '榜单剧目不存在或已过期，请刷新榜单后重试。';
                    if (status === 405) return '收录接口尚未生效，请刷新页面或重启 Web 服务。';
                    if (status >= 500) return '收录接口异常，请稍后重试。';
                    return detail || '收录失败，请稍后重试。';
                },

                async adoptRankEntry(item) {
                    const recordId = String(item?.record_id || item?.id || '');
                    if (!recordId) {
                        this.showAdoptionNotice(false, item?.title || '', '榜单记录缺少标识，无法收录。');
                        return;
                    }
                    if (this.isRankAdoptionPending(item) || this.isRankTitleAdopted(item?.title)) return;
                    this.rankAdoptingRecordIds = {...this.rankAdoptingRecordIds, [recordId]: true};
                    try {
                        const response = await aiworkerApi.request('/api/creative/topics/adopt-from-rank', {
                            method: 'POST', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({
                                platform: this.todayRankPlatform || 'dy',
                                date: this.todayRankDate || this.todayRankData?.date || '',
                                record_id: recordId
                            })
                        });
                        const data = await response.json().catch(() => ({}));
                        if (!response.ok) {
                            throw new Error(this.rankAdoptionFailureMessage(response.status, data.detail));
                        }
                        const titleKey = this.rankAdoptionTitleKey(item.title);
                        if (titleKey) this.rankAdoptedTitleKeys = {...this.rankAdoptedTitleKeys, [titleKey]: true};
                        this.showAdoptionNotice(true, data.topic?.title || item.title, '已收录至选题库。');
                    } catch (error) {
                        this.showAdoptionNotice(false, item?.title || '', error.message || '收录接口错误，请稍后重试。');
                    } finally {
                        const { [recordId]: _pending, ...remaining } = this.rankAdoptingRecordIds;
                        this.rankAdoptingRecordIds = remaining;
                    }
                },

                parseTodayRankHash(shouldFetch = false) {
                    const raw = window.location.hash || '';
                    const path = raw.replace(/^#\/?/, '');
                    if (!path) return false;
                    const parts = path.split('/').map(part => decodeURIComponent(part || ''));
                    if (parts[0] !== 'today-rank') return false;
                    const nextPlatform = parts[1] === 'hg' ? 'hg' : 'dy';
                    const platformChanged = this.todayRankPlatform !== nextPlatform;
                    this.todayRankPlatform = nextPlatform;
                    if (nextPlatform === 'hg') {
                        this.todayRankSectionKey = parts[2] || this.todayRankSectionKey;
                        this.todayRankRankingKey = parts[3] || this.todayRankRankingKey;
                        this.ensureHongguoRoute(false);
                    } else {
                        this.todayRankSectionKey = parts[2] || '榜单';
                        this.todayRankRankingKey = '';
                        this.ensureDouyinRoute(false);
                    }
                    if (shouldFetch && platformChanged) this.fetchTodayRank();
                    return true;
                },

                setTodayRankRoute(platform = 'dy', sectionKey = '', rankingKey = '') {
                    const nextPlatform = platform === 'hg' ? 'hg' : 'dy';
                    const platformChanged = this.todayRankPlatform !== nextPlatform;
                    this.todayRankPlatform = nextPlatform;
                    if (nextPlatform === 'hg') {
                        const section = this.resolveHongguoSection(sectionKey || this.todayRankSectionKey);
                        this.todayRankSectionKey = section?.key || sectionKey || '';
                        const ranking = this.resolveHongguoRanking(this.todayRankSectionKey, rankingKey);
                        this.todayRankRankingKey = ranking?.key || rankingKey || '';
                    } else {
                        const section = this.resolveDouyinSection(sectionKey || this.todayRankSectionKey || '榜单');
                        this.todayRankSectionKey = section?.key || sectionKey || '榜单';
                        this.todayRankRankingKey = '';
                    }
                    const target = this.todayRankRouteHash();
                    if (window.location.hash !== target) {
                        window.location.hash = target;
                        if (platformChanged) this.fetchTodayRank();
                    } else {
                        this.fetchTodayRank();
                    }
                },

                todayRankRouteHash() {
                    if (this.todayRankPlatform === 'dy') {
                        const section = this.selectedDouyinSection();
                        const sectionSlug = section?.label || this.todayRankSectionKey || '榜单';
                        return `#/today-rank/dy/${encodeURIComponent(sectionSlug)}`;
                    }
                    const parts = ['#/today-rank', 'hg'];
                    const section = this.selectedHongguoSection();
                    const ranking = this.selectedHongguoRanking();
                    const sectionSlug = section?.label || this.todayRankSectionKey;
                    const rankingSlug = ranking?.label || this.todayRankRankingKey;
                    if (sectionSlug) parts.push(encodeURIComponent(sectionSlug));
                    if (rankingSlug) parts.push(encodeURIComponent(rankingSlug));
                    return parts.join('/');
                },

                todayRankTables() {
                    const data = this.todayRankData || {};
                    return [
                        {key: 'new', title: '新上榜', mode: 'base', items: data.new_entries || []},
                        {key: 'dropped', title: '下榜', mode: 'base', items: data.dropped_entries || []},
                        {key: 'rank-up', title: '排名上升', mode: 'rank', items: data.rank_up || []},
                        {key: 'rank-down', title: '排名下降', mode: 'rank', items: data.rank_down || []},
                        {key: 'heat-growth', title: '热度增长 Top10', mode: 'heat', items: data.heat_growth_top || []},
                        {key: 'like-growth', title: '点赞增长 Top10', mode: 'like', items: data.like_growth_top || []}
                    ];
                },

                todayRankSections() {
                    return this.douyinSections();
                },

                douyinSections() {
                    const data = this.todayRankData || {};
                    if (Array.isArray(data.sections) && data.sections.length) return data.sections;
                    return [{
                        key: '榜单',
                        label: '榜单',
                        summary: data.summary || {},
                        rankings: [{
                            key: '榜单',
                            label: '榜单',
                            summary: data.summary || {},
                            new_entries: data.new_entries || [],
                            dropped_entries: data.dropped_entries || [],
                            rank_up: data.rank_up || [],
                            rank_down: data.rank_down || [],
                            current_entries: data.current_entries || [],
                            columns: data.columns || {}
                        }]
                    }];
                },

                hongguoSections() {
                    const data = this.todayRankData || {};
                    return Array.isArray(data.sections) ? data.sections : [];
                },

                selectedDouyinSection() {
                    return this.resolveDouyinSection(this.todayRankSectionKey || '榜单');
                },

                resolveDouyinSection(sectionKey = '榜单') {
                    const sections = this.douyinSections();
                    return sections.find(section => section.key === sectionKey || section.label === sectionKey)
                        || sections.find(section => section.key === '榜单' || section.label === '榜单')
                        || sections[0]
                        || null;
                },

                ensureDouyinRoute(updateHash = false) {
                    if (this.todayRankPlatform !== 'dy') return;
                    const section = this.resolveDouyinSection(this.todayRankSectionKey || '榜单');
                    this.todayRankSectionKey = section?.key || '榜单';
                    if (updateHash) {
                        const target = this.todayRankRouteHash();
                        if (window.location.hash !== target) window.location.hash = target;
                    }
                },

                todayRankSummary() {
                    if (this.todayRankPlatform === 'dy') {
                        return this.selectedDouyinSection()?.summary || this.todayRankData?.summary || {};
                    }
                    return this.todayRankData?.summary || {};
                },

                hongguoRankings() {
                    return this.selectedHongguoSection()?.rankings || [];
                },

                selectedHongguoSection() {
                    return this.resolveHongguoSection(this.todayRankSectionKey);
                },

                selectedHongguoRanking() {
                    return this.resolveHongguoRanking(this.todayRankSectionKey, this.todayRankRankingKey);
                },

                resolveHongguoSection(sectionKey = '') {
                    const sections = this.hongguoSections();
                    return sections.find(section => section.key === sectionKey || section.label === sectionKey) || sections[0] || null;
                },

                resolveHongguoRanking(sectionKey = '', rankingKey = '') {
                    const section = this.resolveHongguoSection(sectionKey);
                    const rankings = section?.rankings || [];
                    return rankings.find(ranking => ranking.key === rankingKey || ranking.label === rankingKey) || rankings[0] || null;
                },

                ensureHongguoRoute(updateHash = false) {
                    if (this.todayRankPlatform !== 'hg') return;
                    const section = this.resolveHongguoSection(this.todayRankSectionKey);
                    this.todayRankSectionKey = section?.key || '';
                    const ranking = this.resolveHongguoRanking(this.todayRankSectionKey, this.todayRankRankingKey);
                    this.todayRankRankingKey = ranking?.key || '';
                    if (updateHash) {
                        const target = this.todayRankRouteHash();
                        if (window.location.hash !== target) window.location.hash = target;
                    }
                },

                rankingTables(ranking) {
                    const columns = ranking.columns || {};
                    return [
                        {key: 'current', title: '当前榜单', mode: 'current', items: ranking.current_entries || [], columns: this.rankColumnsForTable(columns.current, ranking.current_entries || [], 'current')},
                        {key: 'new', title: '新上榜', mode: 'base', items: ranking.new_entries || [], columns: this.rankColumnsForTable(columns.new, ranking.new_entries || [], 'base')},
                        {key: 'dropped', title: '下榜', mode: 'base', items: ranking.dropped_entries || [], columns: this.rankColumnsForTable(columns.dropped, ranking.dropped_entries || [], 'base')},
                        {key: 'rank-up', title: '排名上升', mode: 'rank', items: ranking.rank_up || [], columns: this.rankColumnsForTable(columns.rank_up, ranking.rank_up || [], 'rank')},
                        {key: 'rank-down', title: '排名下降', mode: 'rank', items: ranking.rank_down || [], columns: this.rankColumnsForTable(columns.rank_down, ranking.rank_down || [], 'rank')}
                    ];
                },

                getRankingActiveTable(rankingKey) {
                    return this.rankingActiveTables[rankingKey] || 'current';
                },

                setRankingActiveTable(rankingKey, tableKey) {
                    this.rankingActiveTables = { ...this.rankingActiveTables, [rankingKey]: tableKey };
                },

                summaryText(summary = {}) {
                    return `上榜 ${summary.new_count || 0} / 下榜 ${summary.dropped_count || 0} / 上升 ${summary.rank_up_count || 0} / 下降 ${summary.rank_down_count || 0}`;
                },

                entryStatus(item = {}, tableKey = '') {
                    if (tableKey === 'dropped') return '下榜';
                    if (item.rank_delta > 0) return `上升 ${item.rank_delta}`;
                    if (item.rank_delta < 0) return `下降 ${Math.abs(item.rank_delta)}`;
                    if (item.previous_rank) return '在榜';
                    return '新上榜';
                },

                entryStatusClass(item = {}, tableKey = '') {
                    if (tableKey === 'dropped') return 'status-dropped';
                    if (item.rank_delta > 0) return 'status-up';
                    if (item.rank_delta < 0) return 'status-down';
                    if (item.previous_rank) return 'status-stable';
                    return 'status-new';
                },

                rankNumberClass(rank) {
                    const number = Number(rank || 0);
                    if (number >= 1 && number <= 3) return `rank-top-${number}`;
                    return '';
                },

                rankColumnsForTable(columns = [], items = [], mode = '') {
                    if (Array.isArray(columns) && columns.length) return columns;
                    const common = [
                        {key: 'rank', label: '排名', kind: 'rank', width: 55},
                        {key: 'title', label: '剧名', kind: 'title', width: 220},
                        {key: 'category', label: '分类', kind: 'category', width: 100},
                        {key: 'status', label: '状态', kind: 'status', width: 100}
                    ];
                    const candidates = [
                        {key: 'subject', label: '标签', kind: 'text', width: 150},
                        {key: 'description', label: '简介', kind: 'text', width: 260},
                        {key: 'score', label: '评分', kind: 'metric', width: 90},
                        {key: 'share_count', label: '分享', kind: 'metric', width: 90},
                        {key: 'like_count', label: '点赞', kind: 'metric', width: 95, icon: 'bi-hand-thumbs-up'},
                        {key: 'favorite_count', label: '收藏', kind: 'metric', width: 95, icon: 'bi-star'},
                        {key: 'heat', label: '热度', kind: 'metric', width: 95, icon: 'bi-fire'},
                        {key: 'comment_count', label: '评论', kind: 'metric', width: 95, icon: 'bi-chat-dots'},
                        {key: 'recommend_index', label: '推荐指数', kind: 'metric', width: 105},
                        {key: 'rating_count', label: '评分人数', kind: 'metric', width: 105},
                        {key: 'expectation_index', label: '期待指数', kind: 'metric', width: 105},
                        {key: 'search_index', label: '热搜指数', kind: 'metric', width: 105},
                        {key: 'max_heat', label: '最高热度', kind: 'metric', width: 105},
                        {key: 'hot_comment', label: '热评', kind: 'text', width: 240},
                        {key: 'rank_duration', label: '在榜时长', kind: 'rank_duration', width: 210}
                    ];
                    const dynamic = candidates.filter(column => items.some(item => {
                        const value = column.key === 'subject' ? item.subject || item.topic_tags : item[column.key];
                        return this.hasRankColumnValue(value);
                    }));
                    const rankChange = mode === 'rank'
                        ? [{key: 'previous_rank', label: '上期排名', kind: 'metric', width: 90}, {key: 'rank_delta', label: '排名变化', kind: 'rank_delta', width: 105}]
                        : [];
                    return [...common, ...dynamic, ...rankChange];
                },

                hasRankColumnValue(value) {
                    if (value === null || value === undefined) return false;
                    if (typeof value === 'string') return !['', '-', '无', '暂无', 'null', 'None'].includes(value.trim());
                    if (Array.isArray(value)) return value.length > 0;
                    return true;
                },

                rankColumnHeaderClass(column = {}) {
                    return column.kind === 'metric' ? 'rank-metric' : '';
                },

                rankColumnCellClass(column = {}) {
                    return column.kind === 'metric' ? 'rank-metric' : '';
                },

                rankColumnStyle(column = {}) {
                    return column.width ? `min-width: ${column.width}px;` : '';
                },

                rankColumnValue(item = {}, column = {}) {
                    const value = item[column.key];
                    if (column.key === 'subject') return value || this.shortTags(item.topic_tags);
                    if (column.kind === 'rank_duration') return this.formatRankDuration(item);
                    if (column.kind === 'text') return this.shortText(value, 64);
                    return this.formatMetric(value);
                },

                formatRankDuration(item = {}) {
                    if (item.rank_duration) return item.rank_duration;
                    const continuous = Number(item.continuous_rank_days || 0);
                    const cumulative = Number(item.cumulative_rank_days || 0);
                    if (!continuous || !cumulative) return '-';
                    return `连续在榜${continuous}天 / 累计在榜${cumulative}天`;
                },

                rankColumnTooltip(item = {}, column = {}, tbl = {}) {
                    if (column.kind === 'status') return this.entryStatus(item, tbl.key);
                    if (column.kind === 'rank_delta') return this.formatRankDelta(item.rank_delta);
                    if (column.kind === 'rank') return String(item.rank || '-');
                    if (column.kind === 'category') return item.board_type || item.category_label || '-';
                    if (column.key === 'subject') return item.subject || this.shortTags(item.topic_tags);
                    const value = item[column.key];
                    return value === null || value === undefined || value === '' ? '-' : String(value);
                },

                rankingTableColspan(tbl = {}) {
                    return Math.max((tbl.columns || []).length, 4);
                },

                todayRankDates() {
                    return this.todayRankData?.available_dates || [];
                },

                formatMetric(value) {
                    if (value === null || value === undefined || value === '') return '缺失';
                    const number = Number(value || 0);
                    if (!Number.isFinite(number)) return '0';
                    if (Math.abs(number) >= 100000000) return `${(number / 100000000).toFixed(2)}亿`;
                    if (Math.abs(number) >= 10000) return `${(number / 10000).toFixed(2)}万`;
                    return Number.isInteger(number) ? String(number) : number.toFixed(2);
                },

                formatDelta(value) {
                    const number = Number(value || 0);
                    if (!Number.isFinite(number) || number === 0) return '0';
                    return `${number > 0 ? '+' : ''}${this.formatMetric(number)}`;
                },

                formatRankDelta(value) {
                    const number = Number(value || 0);
                    if (!Number.isFinite(number) || number === 0) return '0';
                    return number > 0 ? `上升 ${number}` : `下降 ${Math.abs(number)}`;
                },

                shortTags(tags) {
                    const values = Array.isArray(tags) ? tags.filter(Boolean) : [];
                    return values.length ? values.slice(0, 3).join(' / ') : '-';
                },

                shortText(value, maxLength = 18) {
                    const text = String(value || '').trim();
                    return text ? (text.length > maxLength ? `${text.slice(0, maxLength)}...` : text) : '-';
                },

                warningText(warnings) {
                    const labels = {
                        heat_missing: '热度缺失',
                        favorite_missing: '收藏未采集',
                        like_missing: '点赞缺失',
                        comment_missing: '评论缺失',
                        title_missing: '标题缺失',
                        category_missing: '分类缺失'
                    };
                    return (warnings || []).map(item => labels[item] || item).join('；');
                },

                shortLabel(value) {
                    const text = String(value || '');
                    return text.length > 8 ? `${text.slice(0, 8)}...` : text;
                },

}));

document.addEventListener('visibilitychange', () => {
    if (!document.hidden) window.dispatchEvent(new Event('resize'));
});
