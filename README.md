# trading-position-calculator

Locally hosted web app for trading-position calculations.

A LibreOffice Calc `.ods` trading-position calculator, reimplemented as a
single-user web app that runs on your own machine at `http://localhost:<port>`.

See `AGENTS.md` for project scope, calculation rules, and working practices.

## Setup

```bash
uv sync
```

## Develop

```bash
uv run pytest        # run the test suite
uv run ruff check .  # lint
uv run ruff format . # format
```

## Layout

```
src/trading_position_calculator/   # package source
tests/                             # test suite
pyproject.toml                     # project metadata and tool config
```
