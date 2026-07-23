(() => {
    const RESOURCE_LABELS = {
        brief: '创作任务书',
        characters: '人物与关系',
        outline: '分集大纲',
        beats: '单集 Beats',
        breakdown: '剧目拆解',
    };

    const FIELD_LABELS = {
        title: '方向名称', protagonist: '新主角', background: '新背景',
        audience: '方向目标受众', core_conflict: '核心冲突', main_goal: '主线目标',
        worldview: '世界观', borrowed_elements: '借鉴元素',
        different_elements: '必须差异化元素', first_three_hooks: '前三集钩子',
        highlights: '整体爽点', revision_suggestions: '修改建议',
        risk_reasons: '风险说明', cost_level: '制作成本',
        project_name: '项目名称', genre: '题材', target_platform: '目标平台',
        target_audience: '目标受众', target_episode_count: '目标集数',
        episode_duration_seconds: '单集时长', difference_note: '差异化修改说明',
    };

    const element = (tag, className = '', text = '') => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== '') node.textContent = text;
        return node;
    };

    const displayValue = (value) => {
        if (value === null || value === undefined || value === '') return '-';
        if (typeof value === 'boolean') return value ? '是' : '否';
        return String(value);
    };

    const appendValue = (container, value) => {
        if (Array.isArray(value)) {
            if (!value.length) {
                container.textContent = '-';
                return;
            }
            const list = element('ul', 'approval-value-list mb-0');
            value.forEach(item => {
                const entry = element('li');
                entry.textContent = typeof item === 'object' && item !== null
                    ? Object.values(item).map(displayValue).join(' · ')
                    : displayValue(item);
                list.appendChild(entry);
            });
            container.appendChild(list);
            return;
        }
        container.textContent = displayValue(value);
    };

    const field = (label, value, width = 'col-md-4') => {
        const wrapper = element('div', width);
        wrapper.appendChild(element('div', 'small text-muted mb-1', label));
        const content = element('div', 'approval-readonly-field');
        appendValue(content, value);
        wrapper.appendChild(content);
        return wrapper;
    };

    const heading = (title) => element('h3', 'h6 border-bottom pb-2 mt-4 mb-3', title);

    const fieldGrid = (fields) => {
        const grid = element('div', 'row g-3');
        fields.forEach(([label, value, width]) => grid.appendChild(field(label, value, width)));
        return grid;
    };

    const table = (headers, rows) => {
        const wrapper = element('div', 'approval-table-wrap');
        const tableElement = element('table', 'table table-sm align-top approval-table');
        const head = element('thead');
        const headRow = element('tr');
        headers.forEach(label => headRow.appendChild(element('th', '', label)));
        head.appendChild(headRow);
        const body = element('tbody');
        rows.forEach(values => {
            const row = element('tr');
            values.forEach(value => {
                const cell = element('td');
                appendValue(cell, value);
                row.appendChild(cell);
            });
            body.appendChild(row);
        });
        tableElement.append(head, body);
        wrapper.appendChild(tableElement);
        return wrapper;
    };

    const renderBrief = (snapshot) => {
        const content = snapshot?.content || {};
        const root = element('div', 'approval-structured-content');
        root.append(
            heading('项目参数'),
            fieldGrid([
                ['项目名称', content.project_name, 'col-md-4'],
                ['题材', content.genre, 'col-md-2'],
                ['目标平台', {dy: '抖音', hg: '红果'}[content.target_platform] || content.target_platform, 'col-md-2'],
                ['目标受众', content.target_audience, 'col-md-2'],
                ['目标集数', content.target_episode_count, 'col-md-1'],
                ['单集时长', content.episode_duration_seconds ? `${content.episode_duration_seconds} 秒` : '', 'col-md-1'],
            ]),
            heading('仿写方向与 Brief'),
            fieldGrid([
                ['方向名称', content.title, 'col-md-3'],
                ['新主角', content.protagonist, 'col-md-3'],
                ['新背景', content.background, 'col-md-3'],
                ['方向目标受众', content.audience, 'col-md-3'],
                ['核心冲突', content.core_conflict, 'col-md-4'],
                ['主线目标', content.main_goal, 'col-md-4'],
                ['世界观', content.worldview, 'col-md-4'],
                ['借鉴元素', content.borrowed_elements, 'col-md-6'],
                ['必须差异化元素', content.different_elements, 'col-md-6'],
                ['前三集钩子', content.first_three_hooks, 'col-md-6'],
                ['整体爽点', content.highlights, 'col-md-6'],
                ['修改建议', content.revision_suggestions, 'col-md-6'],
                ['风险说明', content.risk_reasons, 'col-md-6'],
            ]),
            heading('风险与差异化'),
            fieldGrid([
                ['风险等级', {low: '低', medium: '中', high: '高'}[snapshot.risk_level] || snapshot.risk_level, 'col-md-2'],
                ['风险分', snapshot.risk_score, 'col-md-2'],
                ['制作成本', content.cost_level, 'col-md-2'],
                ['差异化修改说明', snapshot.difference_note, 'col-md-6'],
            ]),
        );
        return root;
    };

    const renderCharacters = (snapshot) => {
        const characters = (snapshot?.characters || []).map(item => item.content || {});
        const relationships = (snapshot?.relationships || []).map(item => item.content || {});
        const root = element('div', 'approval-structured-content');
        root.append(
            heading('人物卡'),
            table(
                ['角色名', '年龄段', '身份', '内在欲望', '秘密', '成长线'],
                characters.map(item => [item.name, item.age_range, item.identity, item.desire, item.secret, item.growth_arc]),
            ),
            heading('角色表现设置'),
            table(
                ['角色名', '外在标签', '台词风格', '造型 Prompt', '声音 Prompt'],
                characters.map(item => [item.name, item.external_tags, item.dialogue_style, item.appearance_prompt, item.voice_prompt]),
            ),
            heading('人物关系'),
            table(
                ['角色 A', '角色 B', '关系', '冲突', '变化弧线'],
                relationships.map(item => [item.from, item.to, item.relation, item.conflict, item.change_arc]),
            ),
        );
        return root;
    };

    const renderOutline = (snapshot) => {
        const episodes = (snapshot?.episodes || []).map(item => ({
            episode_number: item.episode_number,
            ...(item.content || {}),
        }));
        const root = element('div', 'approval-structured-content');
        root.append(
            heading('分集大纲'),
            table(
                ['集', '核心事件', '冲突', '人物推进', '爽点', '反转', '结尾悬念'],
                episodes.map(item => [item.episode_number, item.core_event, item.conflict, item.character_progress, item.highlight, item.reversal, item.cliffhanger]),
            ),
        );
        return root;
    };

    const renderBeats = (snapshot) => {
        const content = snapshot?.content || {};
        const beats = content.beats || [];
        const root = element('div', 'approval-structured-content');
        root.append(
            heading(`第 ${snapshot?.episode_number || '-'} 集 · 单集 Beats`),
            table(
                ['序号', '场景', '人物', '行动目标', '阻力', '情绪变化', '转折', '结尾钩子'],
                beats.map(item => [item.order, item.scene, item.characters, item.goal, item.obstacle, item.emotion_change, item.turn, item.hook]),
            ),
        );
        return root;
    };

    const renderGeneric = (snapshot) => {
        const content = snapshot?.content || snapshot || {};
        const root = element('div', 'approval-structured-content');
        root.appendChild(heading('提交内容'));
        const fields = Object.entries(content)
            .filter(([, value]) => typeof value !== 'object' || Array.isArray(value))
            .map(([key, value]) => [FIELD_LABELS[key] || key, value, 'col-md-6']);
        root.appendChild(fieldGrid(fields));
        Object.entries(content).forEach(([key, value]) => {
            if (!value || Array.isArray(value) || typeof value !== 'object') return;
            root.append(heading(FIELD_LABELS[key] || key), renderGeneric({content: value}));
        });
        return root;
    };

    window.aiworkerApprovalRenderer = {
        resourceTypeLabel(resourceType) {
            return RESOURCE_LABELS[resourceType] || resourceType || '提交内容';
        },
        render(resourceType, snapshot) {
            if (resourceType === 'brief') return renderBrief(snapshot);
            if (resourceType === 'characters') return renderCharacters(snapshot);
            if (resourceType === 'outline') return renderOutline(snapshot);
            if (resourceType === 'beats') return renderBeats(snapshot);
            return renderGeneric(snapshot);
        },
    };
})();
