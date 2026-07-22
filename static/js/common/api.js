(function () {
    'use strict';

    const WRITE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

    class PermissionError extends Error {
        constructor(message = '无权执行此操作') {
            super(message);
            this.name = 'PermissionError';
        }
    }

    function csrfToken() {
        return document.querySelector('meta[name="csrf-token"]')?.content || '';
    }

    async function request(input, options = {}) {
        const method = String(options.method || 'GET').toUpperCase();
        const headers = new Headers(options.headers || {});
        if (WRITE_METHODS.has(method)) {
            const token = csrfToken();
            if (token) headers.set('X-CSRF-Token', token);
        }

        const response = await fetch(input, {
            ...options,
            method,
            headers,
            credentials: options.credentials || 'same-origin',
        });
        if (response.status === 401) {
            window.location.assign('/login');
            throw new Error('登录状态已失效');
        }
        if (response.status === 403) {
            const error = new PermissionError();
            window.dispatchEvent(new CustomEvent('aiworker:permission-denied', {
                detail: {message: error.message},
            }));
            throw error;
        }
        return response;
    }

    window.aiworkerApi = {PermissionError, request};
})();
