(function () {
    'use strict';

    const WRITE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

    class ApiError extends Error {
        constructor(message, {status = 0, requestId = null, response = null} = {}) {
            super(message);
            this.name = 'ApiError';
            this.status = status;
            this.requestId = requestId;
            this.response = response;
        }
    }

    class PermissionError extends ApiError {
        constructor(message = '无权执行此操作', {requestId = null, response = null} = {}) {
            super(message, {status: 403, requestId, response});
            this.name = 'PermissionError';
        }
    }

    function csrfToken() {
        return document.querySelector('meta[name="csrf-token"]')?.content || '';
    }

    function storeCsrfToken(token) {
        if (!token) return;
        let meta = document.querySelector('meta[name="csrf-token"]');
        if (!meta) {
            meta = document.createElement('meta');
            meta.name = 'csrf-token';
            document.head.appendChild(meta);
        }
        meta.content = token;
    }

    async function refreshCsrfToken(signal) {
        const response = await fetch('/api/auth/me', {
            method: 'GET',
            credentials: 'same-origin',
            signal,
        });
        if (!response.ok) return '';
        const body = await response.json();
        const token = String(body.csrf_token || '');
        storeCsrfToken(token);
        return token;
    }

    function readRequestId(response) {
        return response.headers.get('X-Request-ID');
    }

    async function parseErrorDetail(response) {
        try {
            const cloned = response.clone();
            const body = await cloned.json();
            if (body.detail) {
                if (typeof body.detail === 'string') return body.detail;
                if (body.detail && typeof body.detail.message === 'string') {
                    return body.detail.message;
                }
                if (Array.isArray(body.detail) && body.detail.length > 0 && body.detail[0].msg) {
                    return body.detail[0].msg;
                }
                return String(body.detail);
            }
            if (body.message) return body.message;
            return `请求失败（HTTP ${response.status}）`;
        } catch (_) {
            return `请求失败（HTTP ${response.status}）`;
        }
    }

    function isAbortError(error) {
        return error?.name === 'AbortError';
    }

    async function request(input, options = {}) {
        const method = String(options.method || 'GET').toUpperCase();
        const headers = new Headers(options.headers || {});
        if (WRITE_METHODS.has(method)) {
            const token = csrfToken();
            if (token) headers.set('X-CSRF-Token', token);
        }

        let response = await fetch(input, {
            ...options,
            signal: options.signal,
            method,
            headers,
            credentials: options.credentials || 'same-origin',
        });

        let requestId = readRequestId(response);

        if (response.status === 403 && WRITE_METHODS.has(method)) {
            const detail = await parseErrorDetail(response);
            if (detail === 'CSRF 校验失败') {
                const refreshedToken = await refreshCsrfToken(options.signal);
                if (refreshedToken) {
                    headers.set('X-CSRF-Token', refreshedToken);
                    response = await fetch(input, {
                        ...options,
                        signal: options.signal,
                        method,
                        headers,
                        credentials: options.credentials || 'same-origin',
                    });
                    requestId = readRequestId(response);
                }
            }
        }

        if (response.status === 401) {
            window.location.assign('/login');
            throw new ApiError('登录已失效，请重新登录后继续。', {status: 401, requestId, response});
        }
        if (response.status === 403) {
            const message = await parseErrorDetail(response);
            const error = new PermissionError(message, {requestId, response});
            window.dispatchEvent(new CustomEvent('aiworker:permission-denied', {
                detail: {message: error.message},
            }));
            throw error;
        }
        return response;
    }

    window.aiworkerApi = {
        ApiError, PermissionError, request,
        isAbortError, readRequestId, parseErrorDetail,
    };
})();
