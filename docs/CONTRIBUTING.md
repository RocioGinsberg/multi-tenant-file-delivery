# Contributing

This repository is organized around small, reviewable phase work. The code is still pre-`v0.1.0`, so contribution quality is mostly about keeping implementation, tests, and public docs aligned.

## Documentation Boundaries

| Change type | Update |
|---|---|
| Product scope, roles, or non-goals | `docs/PRD.md` |
| Current implementation architecture | `docs/ARCHITECTURE.md` and the relevant component README |
| Data entities, constraints, or migrations | `docs/DATA_MODEL.md` plus Alembic migration |
| Phase status, next release, or tag timing | `docs/ROADMAP.md` and `docs/plans/` |
| Accepted architecture decision | `docs/ADR/` |
| Proposed technical design | `docs/RFC/` |
| Public user-facing change | `README.md`, `README_ZH.md`, and `CHANGELOG.md` |

Do not let phase plans become the only source of truth. Plans record work history; architecture docs describe what exists now.

## Pull Requests

Use `.github/pull_request_template.md`. For behavior, architecture, schema, or workflow changes, include:

- Summary: the problem and main approach.
- Changes: modules, schema/message/config changes, and docs touched.
- Tests: exact commands, Docker dependencies, and skipped tests.
- Risks / Rollback: compatibility concerns and fallback path.
- Docs / Plans: README, RFC, ADR, roadmap, or phase plan updates.

Keep PRs scoped to one coherent change. For stacked phase work, each PR should name the phase task IDs it covers.

## Verification

Control plane:

```bash
cd control-plane
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest
```

Data plane:

```bash
cd data-plane
GOTOOLCHAIN=auto GOCACHE=/tmp/smh_go_cache go test ./...
```

Docker integration:

```bash
cd deploy
docker compose up -d mysql kafka minio minio-init redis
cd ../control-plane
RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_observability_docker.py
```

Use `RUN_MYSQL_TESTS=1` for MySQL-specific tests. If Docker tests are skipped, say why in the PR.

## Naming and Domain Language

Use one term per concept:

| Term | Meaning |
|---|---|
| `tenant` | Platform tenant, usually HQ or a subsidiary. |
| `actor` | Current authenticated user context, including tenant, user, and role. |
| `workspace` | Platform-owned logical read container for a target tenant. |
| `physical_object` | Sink receipt metadata for stored bytes. |
| `workspace_object` | Tenant-visible logical file that points to a physical object. |
| `source` | Readable input bytes for the data plane. |
| `sink` | Destination storage or delivery backend. |
| `transport` | Task/result message channel, currently file-spool or Kafka. |

Avoid introducing synonyms such as company, department, bucket view, or file view in code unless they are part of an external API.

## Public-Readiness Rules

- Do not commit `.env`, private credentials, runtime databases, local notes, or generated caches.
- `study/` is intentionally local and ignored.
- MinIO defaults (`minioadmin`) are local development credentials only.
- Before changing GitHub visibility to public, confirm the root `LICENSE`, `CHANGELOG.md`, README demo media, and release smoke status.
