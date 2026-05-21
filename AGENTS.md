# AGENTS.md

Project-specific instructions for Codex and other coding agents working in this repository.

These rules supplement global agent instructions. They should not restate general safety rules unless the rule is specific to this project.

## Project Workflow

- Keep Phase status in `docs/plans/phase-*.md`.
- Phase 3 is closed around MySQL + source reference foundation work.
- Phase 3.x is closed around Kafka source-reference e2e, performance, GC, idempotency, config hardening, benchmark, worker readiness, and review hardening.
- Phase 4 owns Redis progress pub/sub, short-TTL idempotency, lease helpers, and rate limiting.
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

## PR Policy

Pull requests that change behavior, architecture, phase status, or operational workflow must include:

- `Summary`: problem solved and main approach.
- `Changes`: main files/modules, schema/message/config changes, and phase-plan updates.
- `Tests`: exact commands run, Docker dependencies, and skipped tests with reasons.
- `Risks / Rollback`: compatibility concerns, known gaps, or fallback path.
- `Docs / Plans`: docs, RFCs, ADRs, READMEs, or phase plans updated.

Use `.github/pull_request_template.md` as the reference format.

For Phase work, PRs should mention the Phase task IDs covered, for example `3.11` or `3.17`.

For cross-component work, PRs should name the verified path, for example `control-plane -> Kafka -> data-plane -> result apply`.

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

## Study Notes

- When a task involves non-obvious debugging, cross-component reasoning, failed attempts, or operational lessons, add a short note under `study/`.
- Prefer updating an existing focused note, for example `study/phase3-sql-kafka-debug.md`, instead of creating many small files.
- Capture what was confusing, how it was diagnosed, what command or test proved the fix, and the reusable rule for next time.
- Do not add `study/` files to git. The directory is intentionally ignored and should stay local.
- Mention in the final response when a study note was updated.

## Phase 4 Current Queue

Continue Phase 4 work in this order unless the user redirects:

1. `4.1` Redis compose and configuration baseline.
2. `4.2` Redis client wrapper and opt-in health / Docker smoke.
3. `4.3` ProgressBus backend abstraction with Redis pub/sub.
4. `4.4` short-TTL idempotency guard.
5. `4.5` Redis lease helper.
6. `4.6` Redis limiter.
7. `4.7` Phase 4 smoke and runbook sync.
