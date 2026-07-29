(function () {
    'use strict';

    const body = document.getElementById('navigation-list');
    if (!body) return;

    const accountElements = {
        form: document.getElementById('navigation-accounts-form'),
        title: document.getElementById('navigation-accounts-title'),
        route: document.getElementById('navigation-accounts-route'),
        search: document.getElementById('navigation-account-search'),
        error: document.getElementById('navigation-accounts-error'),
        list: document.getElementById('navigation-account-list'),
        save: document.getElementById('navigation-accounts-save'),
    };
    const accountModal = bootstrap.Modal.getOrCreateInstance(document.getElementById('navigation-accounts-modal'));
    const iconChoices = [
        ['', '无图标'],
        ['bi-funnel', '漏斗'], ['bi-lightbulb', '灯泡'], ['bi-pencil-square', '编辑'],
        ['bi-kanban', '看板'], ['bi-people', '用户'], ['bi-gear', '设置'],
        ['bi-diagram-3', '组织'], ['bi-menu-button-wide', '菜单'], ['bi-house', '首页'],
        ['bi-grid', '网格'], ['bi-folder', '文件夹'], ['bi-file-text', '文档'],
        ['bi-bar-chart', '统计'], ['bi-database', '数据'], ['bi-shield-check', '权限'],
        ['bi-clock-history', '历史'],
    ];
    let navigationItems = [];
    let activeNavigationItem = null;
    let accounts = [];

    function textCell(value, className = '') {
        const cell = document.createElement('td');
        cell.className = className;
        cell.textContent = value || '-';
        return cell;
    }

    function iconButton(icon, label, style, handler) {
        const button = document.createElement('button');
        button.className = `btn btn-sm ${style}`;
        button.type = 'button';
        button.title = label;
        button.setAttribute('aria-label', label);
        const glyph = document.createElement('i');
        glyph.className = `bi ${icon}`;
        glyph.setAttribute('aria-hidden', 'true');
        button.appendChild(glyph);
        button.addEventListener('click', handler);
        return button;
    }

    function setButtonBusy(button, busy) {
        button.disabled = busy;
        button.setAttribute('aria-busy', String(busy));
    }

    function showAccountError(message = '') {
        accountElements.error.textContent = message;
        accountElements.error.classList.toggle('d-none', !message);
    }

    async function requestJson(url, options) {
        const response = await window.aiworkerApi.request(url, options);
        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(data.detail || data.message || '请求失败');
        }
        return response.status === 204 ? null : response.json();
    }

    function presentation(item, changes = {}) {
        return {
            label: item.label,
            icon: item.icon || '',
            sort_order: Number(item.sort_order),
            is_enabled: Boolean(item.is_enabled),
            ...changes,
        };
    }

    async function updateItem(item, changes) {
        return requestJson(`/api/admin/navigation/${encodeURIComponent(item.id)}`, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(presentation(item, changes)),
        });
    }

    function iconCell(item) {
        const cell = document.createElement('td');
        const group = document.createElement('div');
        group.className = 'input-group input-group-sm flex-nowrap';
        const preview = document.createElement('span');
        preview.className = 'input-group-text';
        preview.title = '图标预览';
        const glyph = document.createElement('i');
        glyph.className = `bi ${item.icon || 'bi-square'}`;
        glyph.setAttribute('aria-hidden', 'true');
        preview.appendChild(glyph);
        const select = document.createElement('select');
        select.className = 'form-select form-select-sm';
        select.setAttribute('aria-label', `${item.label}图标`);
        const choices = iconChoices.slice();
        if (item.icon && !choices.some(([value]) => value === item.icon)) choices.unshift([item.icon, '当前图标']);
        select.replaceChildren(...choices.map(([value, label]) => new Option(label, value)));
        select.value = item.icon || '';
        select.addEventListener('change', async () => {
            select.disabled = true;
            try {
                await updateItem(item, {icon: select.value});
                glyph.className = `bi ${select.value}`;
                window.AiWorkerToast?.show('导航图标已保存');
                await load();
            } catch (error) {
                select.value = item.icon || '';
                window.AiWorkerToast?.show(error.message, 'error');
            } finally {
                select.disabled = false;
            }
        });
        group.append(preview, select);
        cell.appendChild(group);
        return cell;
    }

    function orderCell(item) {
        const cell = document.createElement('td');
        const siblings = navigationItems.filter(candidate => (candidate.parent_id || null) === (item.parent_id || null));
        const index = siblings.findIndex(candidate => candidate.id === item.id);
        const group = document.createElement('div');
        group.className = 'btn-group btn-group-sm';
        const move = async (offset, button) => {
            const targetIndex = index + offset;
            if (targetIndex < 0 || targetIndex >= siblings.length) return;
            const reordered = siblings.slice();
            const [current] = reordered.splice(index, 1);
            reordered.splice(targetIndex, 0, current);
            setButtonBusy(button, true);
            try {
                await Promise.all(reordered.map((candidate, position) => {
                    const sortOrder = (position + 1) * 10;
                    return Number(candidate.sort_order) === sortOrder ? Promise.resolve() : updateItem(candidate, {sort_order: sortOrder});
                }));
                window.AiWorkerToast?.show('导航顺序已更新');
                await load();
            } catch (error) {
                window.AiWorkerToast?.show(error.message, 'error');
                await load();
            } finally {
                setButtonBusy(button, false);
            }
        };
        const up = iconButton('bi-arrow-up', `上移${item.label}`, 'btn-outline-secondary', function () { move(-1, this); });
        const down = iconButton('bi-arrow-down', `下移${item.label}`, 'btn-outline-secondary', function () { move(1, this); });
        up.disabled = index <= 0;
        down.disabled = index === siblings.length - 1;
        group.append(up, down);
        cell.appendChild(group);
        return cell;
    }

    function enabledCell(item) {
        const cell = document.createElement('td');
        const wrapper = document.createElement('div');
        wrapper.className = 'form-check form-switch mb-0';
        const input = document.createElement('input');
        input.className = 'form-check-input';
        input.type = 'checkbox';
        input.checked = Boolean(item.is_enabled);
        input.title = item.is_enabled ? `停用${item.label}` : `启用${item.label}`;
        input.setAttribute('aria-label', input.title);
        input.addEventListener('change', async () => {
            input.disabled = true;
            try {
                await updateItem(item, {is_enabled: input.checked});
                window.AiWorkerToast?.show(input.checked ? '导航已启用' : '导航已停用');
                await load();
            } catch (error) {
                input.checked = !input.checked;
                window.AiWorkerToast?.show(error.message, 'error');
            } finally {
                input.disabled = false;
            }
        });
        wrapper.appendChild(input);
        cell.appendChild(wrapper);
        return cell;
    }

    function accountCell(item) {
        const cell = document.createElement('td');
        const label = `配置${item.label}的账号授权，当前${Number(item.authorized_user_count || 0)}个账号`;
        const button = iconButton('bi-person-check', label, 'btn-outline-primary', () => openAccounts(item));
        const count = document.createElement('span');
        count.className = 'ms-1';
        count.textContent = String(Number(item.authorized_user_count || 0));
        button.appendChild(count);
        cell.appendChild(button);
        return cell;
    }

    function renderNavigation() {
        if (!navigationItems.length) {
            const row = document.createElement('tr');
            const cell = textCell('暂无导航配置', 'text-center text-muted py-4');
            cell.colSpan = 7;
            row.appendChild(cell);
            body.replaceChildren(row);
            return;
        }
        body.replaceChildren(...navigationItems.map(item => {
            const row = document.createElement('tr');
            const name = item.parent_id ? `　${item.label}` : item.label;
            row.append(textCell(name), iconCell(item), textCell(item.route, 'text-nowrap'), textCell(item.required_permission), orderCell(item), enabledCell(item), accountCell(item));
            return row;
        }));
    }

    function renderAccounts() {
        const query = accountElements.search.value.trim().toLocaleLowerCase();
        const visible = accounts.filter(account => {
            const searchable = [account.username, account.display_name, ...(account.role_codes || [])].join(' ').toLocaleLowerCase();
            return !query || searchable.includes(query);
        });
        if (!visible.length) {
            const row = document.createElement('tr');
            const cell = textCell(query ? '没有匹配账号' : '暂无可配置账号', 'text-center text-muted py-4');
            cell.colSpan = 4;
            row.appendChild(cell);
            accountElements.list.replaceChildren(row);
            return;
        }
        accountElements.list.replaceChildren(...visible.map(account => {
            const row = document.createElement('tr');
            const identity = account.display_name ? `${account.username}（${account.display_name}）` : account.username;
            row.append(textCell(identity), textCell((account.role_codes || []).join('、') || '-'), textCell(account.status === 'active' ? '启用' : '停用'));
            const accessCell = document.createElement('td');
            const select = document.createElement('select');
            select.className = 'form-select form-select-sm';
            select.disabled = account.is_editable === false;
            select.title = account.disabled_reason || `配置${account.username}的账号例外`;
            select.setAttribute('aria-label', select.title);
            select.replaceChildren(
                new Option('继承角色默认', 'inherit'),
                new Option('明确允许', 'allow'),
                new Option('明确拒绝', 'deny'),
            );
            select.value = account.access_mode || 'inherit';
            select.addEventListener('change', () => { account.access_mode = select.value; });
            accessCell.appendChild(select);
            row.appendChild(accessCell);
            if (account.disabled_reason) row.title = account.disabled_reason;
            return row;
        }));
    }

    async function openAccounts(item) {
        activeNavigationItem = item;
        accounts = [];
        accountElements.title.textContent = `${item.label}账号授权`;
        accountElements.route.textContent = item.route;
        accountElements.search.value = '';
        showAccountError();
        const loadingRow = document.createElement('tr');
        const loadingCell = textCell('正在加载账号...', 'text-center text-muted py-4');
        loadingCell.colSpan = 4;
        loadingRow.appendChild(loadingCell);
        accountElements.list.replaceChildren(loadingRow);
        accountElements.save.disabled = true;
        accountModal.show();
        try {
            const data = await requestJson(`/api/admin/navigation/${encodeURIComponent(item.id)}/accounts`);
            if (activeNavigationItem?.id !== item.id) return;
            accounts = data.accounts || [];
            renderAccounts();
            accountElements.save.disabled = false;
        } catch (error) {
            showAccountError(error.message);
        }
    }

    async function load() {
        try {
            navigationItems = await requestJson('/api/admin/navigation');
            renderNavigation();
        } catch (error) {
            navigationItems = [];
            body.replaceChildren();
            const row = document.createElement('tr');
            const cell = textCell(error.message, 'text-danger py-3');
            cell.colSpan = 7;
            row.appendChild(cell);
            body.appendChild(row);
        }
    }

    accountElements.search.addEventListener('input', renderAccounts);
    accountElements.form.addEventListener('submit', async event => {
        event.preventDefault();
        if (!activeNavigationItem) return;
        showAccountError();
        setButtonBusy(accountElements.save, true);
        try {
            await requestJson(`/api/admin/navigation/${encodeURIComponent(activeNavigationItem.id)}/accounts`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({overrides: accounts.filter(account => account.is_editable !== false).map(account => ({user_id: account.id, access_mode: account.access_mode || 'inherit'}))}),
            });
            window.AiWorkerToast?.show('账号授权已保存');
            accountModal.hide();
            await load();
        } catch (error) {
            showAccountError(error.message);
        } finally {
            setButtonBusy(accountElements.save, false);
        }
    });
    document.getElementById('navigation-accounts-modal').addEventListener('hidden.bs.modal', () => { activeNavigationItem = null; accounts = []; });
    document.getElementById('navigation-refresh')?.addEventListener('click', load);
    load();
})();
