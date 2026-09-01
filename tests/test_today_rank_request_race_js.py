# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_today_rank_ignores_stale_response_after_platform_change():
    module_path = json.dumps((ROOT / "static/js/selection/ranks.js").as_posix())
    script = textwrap.dedent(
        f"""
        const pending = [];
        global.window = {{
            aiworkerModules: [],
            addEventListener() {{}},
            dispatchEvent() {{}},
            location: {{hash: ''}},
            setTimeout
        }};
        global.document = {{addEventListener() {{}}, hidden: false}};
        global.Event = function Event() {{}};
        global.aiworkerApi = {{
            request(url) {{
                let resolve;
                const response = new Promise(done => {{ resolve = done; }});
                pending.push({{url, resolve}});
                return response;
            }}
        }};
        require({module_path});

        const methods = window.aiworkerModules[0]();
        const state = {{
            ...methods,
            todayRankPlatform: 'dy',
            todayRankDate: '',
            todayRankData: null,
            todayRankLoading: false,
            todayRankError: '',
            todayRankSectionKey: '榜单',
            todayRankRankingKey: ''
        }};

        (async () => {{
            const douyinRequest = methods.fetchTodayRank.call(state);
            state.todayRankPlatform = 'hg';
            const hongguoRequest = methods.fetchTodayRank.call(state);

            pending[1].resolve({{
                ok: true,
                json: async () => ({{platform: 'hg', date: '2026-08-21', available_dates: []}})
            }});
            await hongguoRequest;
            pending[0].resolve({{
                ok: true,
                json: async () => ({{platform: 'dy', date: '2026-08-20', available_dates: []}})
            }});
            await douyinRequest;

            if (state.todayRankData?.platform !== 'hg') {{
                throw new Error(`stale response replaced current rank: ${{state.todayRankData?.platform}}`);
            }}
            if (state.todayRankLoading) {{
                throw new Error('loading state was not cleared for the current request');
            }}
        }})().catch(error => {{
            console.error(error.message);
            process.exitCode = 1;
        }});
        """
    )

    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_today_rank_reuses_platform_cache_and_force_refresh_bypasses_it():
    module_path = json.dumps((ROOT / "static/js/selection/ranks.js").as_posix())
    script = textwrap.dedent(
        f"""
        const requests = [];
        global.window = {{
            aiworkerModules: [],
            addEventListener() {{}},
            dispatchEvent() {{}},
            location: {{hash: ''}},
            setTimeout
        }};
        global.document = {{addEventListener() {{}}, hidden: false}};
        global.Event = function Event() {{}};
        global.aiworkerApi = {{
            async request(url) {{
                requests.push(url);
                const platform = new URL(url, 'http://localhost').searchParams.get('platform');
                return {{
                    ok: true,
                    json: async () => ({{
                        platform,
                        date: platform === 'dy' ? '2026-08-26' : '2026-08-27',
                        available_dates: []
                    }})
                }};
            }}
        }};
        require({module_path});

        const methods = window.aiworkerModules[0]();
        const state = {{
            ...methods,
            todayRankPlatform: 'dy',
            todayRankDate: '',
            todayRankData: null,
            todayRankCache: {{}},
            todayRankInflight: {{}},
            todayRankRequestId: 0,
            todayRankLoading: false,
            todayRankError: '',
            todayRankSectionKey: '榜单',
            todayRankRankingKey: ''
        }};

        (async () => {{
            await methods.fetchTodayRank.call(state);
            state.todayRankPlatform = 'hg';
            await methods.fetchTodayRank.call(state);
            state.todayRankPlatform = 'dy';
            await methods.fetchTodayRank.call(state);

            if (requests.length !== 2) {{
                throw new Error(`cached platform issued another request: ${{requests.length}}`);
            }}
            if (state.todayRankData?.platform !== 'dy') {{
                throw new Error('cached Douyin data was not restored');
            }}

            await methods.fetchTodayRank.call(state, true);
            if (requests.length !== 3) {{
                throw new Error('force refresh did not bypass the client cache');
            }}
            if (!new URL(requests[2], 'http://localhost').searchParams.has('refresh')) {{
                throw new Error('force refresh was not forwarded to the server');
            }}
        }})().catch(error => {{
            console.error(error.message);
            process.exitCode = 1;
        }});
        """
    )

    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_today_rank_newer_force_refresh_wins_same_key_race():
    module_path = json.dumps((ROOT / "static/js/selection/ranks.js").as_posix())
    script = textwrap.dedent(
        f"""
        const pending = [];
        global.window = {{
            aiworkerModules: [], addEventListener() {{}}, dispatchEvent() {{}},
            location: {{hash: ''}}, setTimeout
        }};
        global.document = {{addEventListener() {{}}, hidden: false}};
        global.Event = function Event() {{}};
        global.aiworkerApi = {{
            request() {{
                let resolve;
                const response = new Promise(done => {{ resolve = done; }});
                pending.push({{resolve}});
                return response;
            }}
        }};
        require({module_path});
        const methods = window.aiworkerModules[0]();
        const state = {{
            ...methods, todayRankPlatform: 'dy', todayRankDate: '',
            todayRankData: null, todayRankCache: {{}}, todayRankInflight: {{}},
            todayRankCacheVersion: {{}}, todayRankRequestId: 0,
            todayRankLoading: false, todayRankError: '',
            todayRankSectionKey: '榜单', todayRankRankingKey: ''
        }};

        (async () => {{
            const older = methods.fetchTodayRank.call(state, true);
            const newer = methods.fetchTodayRank.call(state, true);
            pending[1].resolve({{ok: true, json: async () => ({{
                platform: 'dy', date: '2026-08-27', available_dates: [], marker: 'new'
            }})}});
            await newer;
            pending[0].resolve({{ok: true, json: async () => ({{
                platform: 'dy', date: '2026-08-27', available_dates: [], marker: 'old'
            }})}});
            await older;

            if (state.todayRankCache['dy:latest']?.marker !== 'new') {{
                throw new Error('older force refresh overwrote the newer cache');
            }}
            if (state.todayRankData?.marker !== 'new') {{
                throw new Error('older force refresh overwrote the newer view');
            }}
        }})().catch(error => {{ console.error(error.message); process.exitCode = 1; }});
        """
    )

    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr


def test_today_rank_refresh_survives_switch_away_and_back_and_clears_aliases():
    module_path = json.dumps((ROOT / "static/js/selection/ranks.js").as_posix())
    script = textwrap.dedent(
        f"""
        const pending = [];
        global.window = {{
            aiworkerModules: [], addEventListener() {{}}, dispatchEvent() {{}},
            location: {{hash: ''}}, setTimeout
        }};
        global.document = {{addEventListener() {{}}, hidden: false}};
        global.Event = function Event() {{}};
        global.aiworkerApi = {{
            request() {{
                let resolve;
                const response = new Promise(done => {{ resolve = done; }});
                pending.push({{resolve}});
                return response;
            }}
        }};
        require({module_path});
        const methods = window.aiworkerModules[0]();
        const old = {{platform: 'dy', date: '2026-08-26', available_dates: [], marker: 'old'}};
        const state = {{
            ...methods, todayRankPlatform: 'dy', todayRankDate: '2026-08-27',
            todayRankData: old,
            todayRankCache: {{'dy:latest': old, 'dy:2026-08-26': old}},
            todayRankInflight: {{}}, todayRankCacheVersion: {{}}, todayRankRequestId: 0,
            todayRankLoading: false, todayRankError: '',
            todayRankSectionKey: '榜单', todayRankRankingKey: ''
        }};

        (async () => {{
            const refresh = methods.fetchTodayRank.call(state, true);
            state.todayRankPlatform = 'hg';
            state.todayRankDate = '';
            state.todayRankData = {{platform: 'hg', marker: 'hg'}};
            state.todayRankPlatform = 'dy';
            state.todayRankDate = '2026-08-27';

            pending[0].resolve({{ok: true, json: async () => ({{
                platform: 'dy', date: '2026-08-27', available_dates: ['2026-08-27'], marker: 'new'
            }})}});
            await refresh;

            if (state.todayRankData?.marker !== 'new') {{
                throw new Error('refresh result was discarded after switching away and back');
            }}
            if ('dy:latest' in state.todayRankCache || 'dy:2026-08-26' in state.todayRankCache) {{
                throw new Error('force refresh left stale aliases for the same platform');
            }}
            if (state.todayRankCache['dy:2026-08-27']?.marker !== 'new') {{
                throw new Error('force refresh did not cache the refreshed date');
            }}
        }})().catch(error => {{ console.error(error.message); process.exitCode = 1; }});
        """
    )

    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr


def test_today_rank_reselecting_current_route_does_not_reload_data():
    module_path = json.dumps((ROOT / "static/js/selection/ranks.js").as_posix())
    script = textwrap.dedent(
        f"""
        let requestCount = 0;
        global.window = {{
            aiworkerModules: [],
            addEventListener() {{}},
            dispatchEvent() {{}},
            location: {{hash: '#/today-rank/dy/%E6%A6%9C%E5%8D%95'}},
            setTimeout
        }};
        global.document = {{addEventListener() {{}}, hidden: false}};
        global.Event = function Event() {{}};
        global.aiworkerApi = {{
            async request() {{
                requestCount += 1;
                return {{ok: true, json: async () => ({{platform: 'dy', available_dates: []}})}};
            }}
        }};
        require({module_path});

        const methods = window.aiworkerModules[0]();
        const state = {{
            ...methods,
            todayRankPlatform: 'dy',
            todayRankDate: '',
            todayRankData: {{
                platform: 'dy',
                sections: [{{key: '榜单', label: '榜单', rankings: []}}]
            }},
            todayRankCache: {{}},
            todayRankInflight: {{}},
            todayRankRequestId: 0,
            todayRankLoading: false,
            todayRankError: '',
            todayRankSectionKey: '榜单',
            todayRankRankingKey: ''
        }};

        methods.setTodayRankRoute.call(state, 'dy', '榜单');
        if (requestCount !== 0) {{
            throw new Error('reselecting the current rank route reloaded unchanged data');
        }}
        """
    )

    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_today_rank_only_builds_the_active_table_for_rendering():
    module_path = json.dumps((ROOT / "static/js/selection/ranks.js").as_posix())
    script = textwrap.dedent(
        f"""
        global.window = {{
            aiworkerModules: [],
            addEventListener() {{}},
            dispatchEvent() {{}},
            location: {{hash: ''}},
            setTimeout
        }};
        global.document = {{addEventListener() {{}}, hidden: false}};
        global.Event = function Event() {{}};
        require({module_path});

        const methods = window.aiworkerModules[0]();
        let columnBuilds = 0;
        const state = {{...methods, rankingActiveTables: {{hot: 'current'}}}};
        state.rankColumnsForTable = (...args) => {{
            columnBuilds += 1;
            return methods.rankColumnsForTable.call(state, ...args);
        }};
        const ranking = {{
            key: 'hot',
            columns: {{}},
            current_entries: [{{record_id: 'current'}}],
            new_entries: [{{record_id: 'new'}}],
            dropped_entries: [],
            rank_up: [],
            rank_down: []
        }};

        const tabs = methods.rankingTableTabs.call(state);
        if (tabs.length !== 5 || tabs[0].key !== 'current') {{
            throw new Error('ranking table tabs are incomplete');
        }}
        if (columnBuilds !== 0) {{
            throw new Error('table tab rendering derived data columns');
        }}
        const current = methods.activeRankingTables.call(state, ranking);
        if (current.length !== 1 || current[0].key !== 'current') {{
            throw new Error('rendering included inactive ranking tables');
        }}
        if (columnBuilds !== 1) {{
            throw new Error(`inactive table columns were still derived: ${{columnBuilds}}`);
        }}
        columnBuilds = 0;
        methods.setRankingActiveTable.call(state, 'hot', 'new');
        const next = methods.activeRankingTables.call(state, ranking);
        if (next.length !== 1 || next[0].key !== 'new') {{
            throw new Error('active ranking table did not switch');
        }}
        if (columnBuilds !== 1) {{
            throw new Error(`table switch derived inactive columns: ${{columnBuilds}}`);
        }}
        """
    )

    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
