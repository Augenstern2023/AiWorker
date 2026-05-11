# AiWorker Agent Guide

## Dev Commands

```bash
uv sync                    # Install dependencies (use uv, not pip)
uv run playwright install  # Install browser drivers
uv run main.py --help      # Run crawler, see all CLI options

pytest                     # Run all tests
pytest tests/              # Unit tests
pytest test/               # Integration tests

pre-commit run --all-files  # Run pre-commit hooks (file header checker)
```

## Project Structure

- **Entry point**: `main.py` → `CrawlerFactory` dispatches to `media_platform/{platform}/core.py`
- **Config**: `config/base_config.py` (global) + `config/{platform}_config.py` (per-platform overrides)
- **Platforms**: xhs | dy | ks | bili | wb | tieba | zhihu (current focus: dy)
- **Crawler types**: search | detail | creator
- **Login types**: qrcode | phone | cookie

## Important Conventions

- **Package manager**: Always use `uv` (not pip). `uv sync` syncs from `pyproject.toml`
- **Python version**: 3.11 (see `.python-version`)
- **File headers**: Pre-commit requires copyright headers on all `.py` files via `tools/file_header_manager.py`
- **CDP mode**: Enabled by default in `base_config.py` (`ENABLE_CDP_MODE = True`) — uses user's real Chrome/Edge browser with CDP protocol

## Testing

Two test directories:
- `tests/` — unit tests with fixtures in `conftest.py`
- `test/` — integration tests (some require Redis/MongoDB)

## Key Config Options (`config/base_config.py`)

| Option | Default | Notes |
|--------|---------|-------|
| `PLATFORM` | xhs | Target platform |
| `KEYWORDS` | - | Comma-separated search keywords |
| `LOGIN_TYPE` | qrcode | Login method |
| `CRAWLER_TYPE` | search | search \| detail \| creator |
| `SAVE_DATA_OPTION` | json | csv \| db \| json \| sqlite \| excel |
| `ENABLE_GET_COMMENTS` | True | Comment crawling |
| `ENABLE_CDP_MODE` | True | Use real browser via CDP |

## Data Storage

Supports: CSV, JSON, SQLite, MySQL, MongoDB, Excel. Configured via `SAVE_DATA_OPTION`.
Database tables initialized with `uv run main.py --init_db [sqlite|mysql]`.

## GUI Interfaces

Three GUI options available:

| GUI | Command | Access |
|-----|---------|--------|
| Desktop GUI | `python GUI.py` | tkinter window |
| Web GUI | `python WebGUI.py` | http://localhost:8080 |
| Private GUI | (file with Chinese name) | - |

The `启动*.bat` batch files activate `.venv` and run the respective GUI scripts.