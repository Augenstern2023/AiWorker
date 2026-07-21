# 抖音榜单 CDP 探针

本文记录当前针对抖音短剧榜、漫剧榜数据的 Playwright/CDP 探索方案。

## 新增内容

`tools/explore_douyin_rank_cdp.py` 是一个小型 Playwright/CDP 探针。它复用项目里的 CDP 浏览器管理器，打开一个或多个目标页面，监听 `xhr`/`fetch`/JSON 响应，对疑似榜单或列表响应进行打分，并同时保存原始 JSON 和 `summary.json` 索引。

运行方式：

```bash
uv run python tools/explore_douyin_rank_cdp.py --url "https://www.douyin.com/..."
```

常用选项：

```bash
uv run python tools/explore_douyin_rank_cdp.py --manual-wait-seconds 30
uv run python tools/explore_douyin_rank_cdp.py --include-all-json
uv run python tools/explore_douyin_rank_cdp.py --url "短剧热播榜"
```

如果已经拿到真实榜单页的抖音 App 分享链接或 PC 页面 URL，可以通过 `--url` 传入。探针会把捕获结果保存到 `data/douyin/rank_probe/<timestamp>/`。

## 首次探测证据

首次探测打开了以下默认 PC 搜索页：

- `https://www.douyin.com/search/%E7%9F%AD%E5%89%A7%E7%83%AD%E6%92%AD%E6%A6%9C`
- `https://www.douyin.com/search/%E6%BC%AB%E5%89%A7%E7%83%AD%E6%92%AD%E6%A6%9C`

CDP 启动和响应捕获都可以正常工作。从 PC 搜索页中发现的最有价值接口是：

```text
/aweme/v1/web/hot/search/list/
```

这个响应是抖音通用热搜/热榜数据，包含 `share_url`、`trending_list`、`word`、`hot_value`、`max_rank` 等字段。

但这还不能证明已经拿到了短剧或漫剧垂类的热播榜。默认 PC 搜索页主要加载的是全局热榜、AB 配置、资源配置、用户设置和埋点遥测接口。

## 当前结论

Playwright/CDP 直接访问页面并解析响应的策略是可行的。它可以复用真实浏览器上下文，并收集由浏览器发出的、已带签名参数的响应。

不过，要证明并稳定短剧/漫剧垂类榜单接口，仍然需要真实的短剧榜或漫剧榜页面 URL。默认搜索页还不够。

建议下一步从抖音 App 分享、PC 端导航入口，或浏览器手动导航中获取实际榜单页 URL，然后运行：

```bash
uv run python tools/explore_douyin_rank_cdp.py --url "<actual-rank-url>" --manual-wait-seconds 30 --include-all-json
```

确认目标接口后，可以把它封装到 `media_platform/douyin/client.py` 中作为明确的方法，再新增一个 crawler mode 或独立的榜单采集命令。
