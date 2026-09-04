(function () {
    'use strict';

    var pendingRequests = new Map();
    var cacheStore = new Map();
    var buttonStates = new WeakMap();

    function requestKey(key, factory) {
        cancelRequest(key);
        var controller = new AbortController();
        pendingRequests.set(key, controller);
        return factory(controller.signal).finally(function () {
            if (pendingRequests.get(key) === controller) {
                pendingRequests.delete(key);
            }
        });
    }

    function cancelRequest(key) {
        var controller = pendingRequests.get(key);
        if (controller) {
            controller.abort();
            pendingRequests.delete(key);
        }
    }

    function readCache(key) {
        var entry = cacheStore.get(key);
        if (!entry) return null;
        var now = Date.now();
        if (now < entry.expiresAt) {
            return {value: entry.value, stale: false};
        }
        return {value: entry.value, stale: true};
    }

    function writeCache(key, value, options) {
        var opts = options || {};
        var ttlMs = opts.ttlMs || 30000;
        cacheStore.set(key, {
            value: value,
            expiresAt: Date.now() + ttlMs,
        });
    }

    function invalidateCache(prefix) {
        var keys = [];
        cacheStore.forEach(function (_, key) {
            if (key.indexOf(prefix) === 0) keys.push(key);
        });
        keys.forEach(function (key) { cacheStore.delete(key); });
    }

    function announce(message, options) {
        var region = document.getElementById('ui-live-region');
        if (!region) return;
        var opts = options || {};
        var priority = opts.priority || 'polite';
        region.setAttribute('aria-live', priority);
        region.textContent = '';
        setTimeout(function () {
            region.textContent = message;
        }, 50);
    }

    function showToast(opts) {
        var kind = opts.kind || 'info';
        var title = opts.title || '';
        var message = opts.message || '';

        var region = document.getElementById('ui-toast-region');
        if (!region) return;

        var toastEl = document.createElement('div');
        toastEl.className = 'toast ui-toast toast-' + kind;
        toastEl.setAttribute('role', 'status');

        var header = document.createElement('div');
        header.className = 'toast-header';

        var strong = document.createElement('strong');
        strong.className = 'me-auto';
        strong.textContent = title;
        header.appendChild(strong);

        var closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'btn-close btn-close-white';
        closeBtn.setAttribute('data-bs-dismiss', 'toast');
        closeBtn.setAttribute('aria-label', '关闭');
        header.appendChild(closeBtn);

        toastEl.appendChild(header);

        if (message) {
            var body = document.createElement('div');
            body.className = 'toast-body';
            body.textContent = message;
            toastEl.appendChild(body);
        }

        region.appendChild(toastEl);

        if (message) announce(message);

        var removed = false;
        function removeEl() {
            if (removed) return;
            removed = true;
            if (toastEl.parentNode) toastEl.parentNode.removeChild(toastEl);
        }

        if (window.bootstrap && window.bootstrap.Toast) {
            var bsToast = window.bootstrap.Toast.getOrCreateInstance(toastEl, {delay: 4000});
            toastEl.addEventListener('hidden.bs.toast', removeEl);
            bsToast.show();
        } else {
            toastEl.classList.add('show');
            setTimeout(removeEl, 4000);
        }

        closeBtn.addEventListener('click', removeEl);
    }

    function setButtonLoading(button, opts) {
        var loading = opts.loading;
        if (loading) {
            buttonStates.set(button, {
                disabled: button.disabled,
                text: button.textContent,
                ariaBusy: button.getAttribute('aria-busy'),
            });
            button.disabled = true;
            button.setAttribute('aria-busy', 'true');
            button.textContent = opts.label || '处理中…';
        } else {
            var saved = buttonStates.get(button);
            if (saved) {
                button.disabled = saved.disabled;
                button.textContent = saved.text;
                if (saved.ariaBusy != null) {
                    button.setAttribute('aria-busy', saved.ariaBusy);
                } else {
                    button.removeAttribute('aria-busy');
                }
                buttonStates.delete(button);
            }
        }
    }

    window.aiworkerUi = {
        requestKey: requestKey,
        cancelRequest: cancelRequest,
        readCache: readCache,
        writeCache: writeCache,
        invalidateCache: invalidateCache,
        announce: announce,
        showToast: showToast,
        setButtonLoading: setButtonLoading,
    };
})();
