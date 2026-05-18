# AGENTS.md

Project-specific instructions for Codex and other coding agents working in this repository.

These rules supplement global agent instructions. They should not restate general safety rules unless the rule is specific to this project.

## Project Workflow

- Keep Phase status in `docs/plans/phase-*.md`.
- Phase 3 is closed around MySQL + source reference foundation work.
- Phase 3.x owns Kafka source-reference e2e, performance, GC, idempotency, config hardening, benchmark, and worker readiness.
- Do not append new Phase 3.x tasks back into `docs/plans/phase-3-data-layer-and-source-ref.md`.
- If a Phase 3.x topic grows beyond a few tasks, split it into a focused `docs/plans/phase-3x-*.md` file.

## Commit Policy

For non-trivial behavior, test, architecture, or phase-status changes, commit messages must include a body with:

```text
Summary:
- ...

Changed:
- ...

Tests:
- ...
```

Use `docs/COMMIT_TEMPLATE.md` as the reference format.

For documentation-only changes, `Tests:` may say `Not run; documentation-only change.`

## Verification Commands

Python control-plane:

```bash
cd control-plane
.venv/bin/python -m pytest ...
.venv/bin/python -m ruff check ...
```

Go data-plane:

```bash
cd data-plane
GOCACHE=/tmp/smh_go_cache go test ./...
```

Docker integration:

```bash
cd deploy
docker compose up -d mysql kafka minio minio-init
```

- Use `RUN_DOCKER_TESTS=1` only for tests that require Docker services.
- Use `RUN_MYSQL_TESTS=1` only for MySQL-specific tests.
- If Docker tests are skipped, mention why in the final response or commit body.

## Directory Boundaries

- `docs/` stores project knowledge, plans, RFCs, ADRs, architecture notes, and templates.
- `study/` stores personal learning notes and is ignored by git.
- `control-plane/` owns FastAPI, SQLAlchemy, Alembic, task state, result apply, and staging source metadata.
- `data-plane/` owns Go worker execution, source resolvers, transports, sinks, and upload pipeline behavior.
- Runtime behavior changes should update code, tests, and the relevant plan or architecture docs when phase status changes.

## Phase 3.x Current Queue

Continue remaining Phase 3.x work in this order unless the user redirects:

1. `3.12` benchmark baseline.
2. `3.18` config profiles.
3. `3.19` worker health / readiness.
4. RFC 0003 review, then decide whether to implement DLQ topics.
