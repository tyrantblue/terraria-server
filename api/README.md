# terraria-api

FastAPI backend for the Terraria management panel. Talks to the game server through the
control FIFO (`control/command.fifo`) and reads its console log (`control/output.log`).

## Layout

```
app/
├── main.py        app factory, middleware, unified error handlers, router wiring
├── core/          settings (paths/timeouts/API version) and domain errors
├── schemas/       Pydantic request/response models — the source of the OpenAPI contract
├── services/      everything that is not HTTP
│   ├── console/   FIFO channel (locking + sentinel), log reader, parser, audit log
│   ├── status.py  cached status collector
│   ├── metrics.py cgroup/disk/player sampling for GET /api/v1/metrics
│   ├── world_header.py  .wld header parser (size tier / difficulty / created_at)
│   ├── *_service.py, runtime.py
└── api/           thin routers: paths, status codes, dependency injection only
```

Layering rules (please keep them):

* **routers** do HTTP only — no `sleep`, no file I/O, no command strings, no regex.
* **services** do business logic — never import `fastapi`.
* **schemas** define every request and response shape; responses declare `response_model`.
* Validation is declarative (Pydantic), not copy-pasted between endpoints.

## Working on it

Everything goes through [uv](https://docs.astral.sh/uv/); `uv.lock` is the single source of
truth for dependencies. Dev-only tools live in the `dev` dependency group.

```bash
cd api

uv sync                                          # create/refresh .venv from uv.lock
uv run pytest -q                                 # tests (fake FIFO + fake log, no real server)
uv run python scripts/export_openapi.py          # refresh the contract snapshot
uv run python scripts/export_openapi.py --check  # what CI runs

uv add --dev <package>                           # add a dev dependency (updates uv.lock)
uv run uvicorn app.main:app --reload --port 8080 # run locally
```

The production image uses `uv sync --no-dev` / `uv run --no-dev`, so test dependencies never
ship.

## API contract

Three artifacts keep the frontend in sync (details in `../docs/api-refactor-plan.md` §5):

| Artifact | Purpose |
| --- | --- |
| `openapi.json` | machine-readable snapshot; generate frontend types from it |
| `../docs/api/CHANGELOG.md` | human-readable changes with migration examples |
| `GET /api/meta` | runtime handshake (`api_version`, `min_client_version`, `capabilities`) |

2.0.0 removed the legacy `/api/*` routes, so `GET /api/meta/usage` and the deprecation
middleware are gone too; `deprecations` is still returned but is always `[]`.
CI fails when the snapshot is stale, so a contract change cannot land undocumented.
