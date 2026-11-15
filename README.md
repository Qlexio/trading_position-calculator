# workspace

A small Python project.

## Setup

```bash
uv sync
```

## Develop

```bash
uv run pytest        # run the test suite
uv run ruff check .  # lint
uv run ruff format . # format
uv run workspace     # run the CLI entry point
```

## Layout

```
src/workspace/   # package source
tests/           # test suite
pyproject.toml   # project metadata and tool config
```
