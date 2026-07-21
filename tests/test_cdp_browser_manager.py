# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

import pytest

from tools.cdp_browser import CDPBrowserManager


@pytest.mark.asyncio
async def test_get_browser_websocket_url_uses_loopback_without_env_proxy(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/test"}

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url, timeout):
            calls.append(("get", url, timeout))
            return FakeResponse()

    monkeypatch.setattr("tools.cdp_browser.httpx.AsyncClient", FakeClient)

    url = await CDPBrowserManager()._get_browser_websocket_url(9222)

    assert url == "ws://127.0.0.1:9222/devtools/browser/test"
    assert calls[0] == ("init", {"trust_env": False})
    assert calls[1][1] == "http://127.0.0.1:9222/json/version"
