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


def test_today_hits_ignores_stale_generation_response_after_source_change():
    module_path = json.dumps(
        (ROOT / "static/js/selection/breakdowns.js").as_posix()
    )
    script = textwrap.dedent(
        f"""
        const pending = [];
        const cacheWrites = [];
        global.window = {{
            aiworkerModules: [],
            aiworkerUi: {{
                writeCache(key, value) {{ cacheWrites.push({{key, value}}); }}
            }}
        }};
        global.document = {{querySelector() {{ return null; }}}};
        global.aiworkerApi = {{
            request(_url, options) {{
                let resolve;
                const response = new Promise(done => {{ resolve = done; }});
                pending.push({{options, resolve}});
                return response;
            }}
        }};
        require({module_path});

        const methods = window.aiworkerModules[0]();
        const state = {{
            creative: {{
                platform: 'dy', rankSource: '榜单', loading: false,
                error: '', items: []
            }}
        }};

        (async () => {{
            const douyinRequest = methods.generateCreativeTopics.call(state);
            state.creative.platform = 'hg';
            state.creative.rankSource = '全部';
            const hongguoRequest = methods.generateCreativeTopics.call(state);

            pending[1].resolve({{
                ok: true,
                json: async () => ({{items: [{{title: '红果候选'}}]}})
            }});
            await hongguoRequest;
            pending[0].resolve({{
                ok: true,
                json: async () => ({{items: [{{title: '抖音候选'}}]}})
            }});
            await douyinRequest;

            const titles = state.creative.items.map(item => item.title);
            if (JSON.stringify(titles) !== JSON.stringify(['红果候选'])) {{
                throw new Error(`stale response replaced current items: ${{titles}}`);
            }}
            const keys = cacheWrites.map(write => write.key).sort();
            const expectedKeys = [
                'creative:topics:hits:dy:榜单',
                'creative:topics:hits:hg:全部'
            ].sort();
            if (JSON.stringify(keys) !== JSON.stringify(expectedKeys)) {{
                throw new Error(`responses used the wrong cache keys: ${{keys}}`);
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
