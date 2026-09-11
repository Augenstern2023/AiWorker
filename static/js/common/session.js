(function () {
    'use strict';

    const permissionAlert = document.getElementById('permission-alert');
    const permissionMessage = document.getElementById('permission-alert-message');
    const HEARTBEAT_INTERVAL_MS = 30000;
    const MAX_IDLE_SECONDS = 86400;
    let lastActivityAt = performance.now();
    let heartbeatInFlight = false;

    function presenceTabId() {
        try {
            const storageKey = 'aiworker_presence_tab_id';
            let value = sessionStorage.getItem(storageKey);
            if (!value) {
                value = self.crypto?.randomUUID?.() || `tab-${Date.now()}-${Math.random().toString(36).slice(2)}`;
                sessionStorage.setItem(storageKey, value);
            }
            return value;
        } catch (_) {
            return `tab-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        }
    }

    const tabId = presenceTabId();

    function markActivity() {
        lastActivityAt = performance.now();
    }

    async function sendPresence() {
        if (heartbeatInFlight) return;
        heartbeatInFlight = true;
        const idleSeconds = Math.min(
            MAX_IDLE_SECONDS,
            Math.max(0, (performance.now() - lastActivityAt) / 1000),
        );
        const controller = new AbortController();
        const timeoutId = window.setTimeout(() => controller.abort(), 8000);
        try {
            await fetch('/api/presence/heartbeat', {
                method: 'POST',
                credentials: 'same-origin',
                signal: controller.signal,
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]')?.content || '',
                },
                body: JSON.stringify({
                    tab_id: tabId,
                    path: window.location.pathname,
                    is_visible: document.visibilityState === 'visible',
                    idle_seconds: idleSeconds,
                }),
            });
        } catch (_) {
            // Presence is best-effort and must never interrupt the current page.
        } finally {
            window.clearTimeout(timeoutId);
            heartbeatInFlight = false;
        }
    }

    function showToast(message, type) {
        if (!permissionAlert) return;
        permissionMessage.textContent = message;
        permissionAlert.className = 'alert position-fixed top-0 end-0 m-3 ' + (type === 'error' ? 'alert-danger' : 'alert-info');
        permissionAlert.classList.remove('d-none');
    }

    window.addEventListener('aiworker:permission-denied', (event) => {
        showToast(event.detail?.message || '无权执行此操作', 'error');
    });
    window.addEventListener('aiworker:toast', (event) => {
        showToast(event.detail?.message || '', event.detail?.type || 'info');
    });
    document.getElementById('permission-alert-dismiss')?.addEventListener('click', () => {
        permissionAlert.classList.add('d-none');
    });
    document.getElementById('logout-button')?.addEventListener('click', async () => {
        try {
            await window.aiworkerApi.request('/api/auth/logout', {method: 'POST'});
        } finally {
            window.location.assign('/login');
        }
    });

    ['pointerdown', 'keydown', 'touchstart', 'scroll'].forEach(eventName => {
        window.addEventListener(eventName, markActivity, {passive: true});
    });
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') markActivity();
        void sendPresence();
    });
    window.setInterval(() => {
        void sendPresence();
    }, HEARTBEAT_INTERVAL_MS);
    void sendPresence();

    window.AiWorkerToast = { show: showToast };
})();
