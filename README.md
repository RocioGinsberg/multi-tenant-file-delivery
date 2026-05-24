<div align="center">

### Multi-Tenant File Delivery Platform

English / [中文](README_ZH.md)

[Docs](docs/) · [Roadmap](docs/ROADMAP.md) · [Architecture](docs/ARCHITECTURE.md) · [Changelog](CHANGELOG.md)

[![python](https://img.shields.io/badge/python-3.12%2B-blue?style=flat-square)](control-plane/pyproject.toml)
[![go](https://img.shields.io/badge/go-1.25-blue?style=flat-square)](data-plane/go.mod)
[![status](https://img.shields.io/badge/status-v0.1.0--rc-orange?style=flat-square)](docs/ROADMAP.md)
[![license](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

</div>

***

## Overview

This repository is a local-first, public-reference implementation of a **multi-tenant file delivery platform**. HQ users upload a folder, the control plane classifies files into subsidiary workspaces, the Go data plane delivers bytes to S3-compatible object storage, and subsidiaries can only browse and download files that belong to their own workspace.

### Why this exists

HQ-to-subsidiary distribution looks simple until it becomes operational:

- **Folder semantics matter.** Users select a folder, not a pre-built archive, and the platform must preserve relative paths.
- **Classification must be explainable.** Operators need to preview target company, document type, destination path, warnings, and blocking errors before delivery.
- **Upload execution must be recoverable.** Large batches should move through a durable task/result bridge instead of a browser or API process doing every byte transfer.
- **Tenant isolation must be platform-owned.** Subsidiaries must not rely on the storage backend's ACL model to decide what they can see.
- **Progress and failures need evidence.** Redis, Kafka, OpenTelemetry, Prometheus, and Grafana are used where they make the workflow observable and auditable.

### What it gives you

- **HQ upload desk**: browser folder selection, multipart upload, classification preview, confirmation, retry, and SSE progress.
- **Python control plane**: FastAPI, SQLAlchemy, Alembic, task state, tenant-aware repositories, result application, and presigned download authorization.
- **Go data plane**: worker pipeline, file/object source resolvers, file-spool and Kafka transports, mock and S3/MinIO sinks, Redis limiter, metrics, and tracing.
- **Workspace read model**: `workspace`, `physical_object`, and `workspace_object` metadata built from delivery results.
- **Local infrastructure**: Docker Compose for MySQL, Kafka, MinIO, Redis, OTel Collector, Prometheus, and Grafana.
- **Traceable design history**: PRD, RFCs, ADRs, data model, phase plans, and release roadmap.

***

## Status

The current branch is preparing the first public release candidate, **v0.1.0**, after Phase 6.5:

- Phase 0-6.5 implementation is complete.
- A tag should be created only after the audit hardening branch is merged to `main`, public docs are complete, demo media is generated, and release smoke passes.
- Phase 7 is next: more sink adapters, failure-injection sinks, benchmark data, and the first dedup / credential-hardening split.

See [CHANGELOG.md](CHANGELOG.md) and [docs/ROADMAP.md](docs/ROADMAP.md).

***

## Demo

![HQ upload to workspace demo](docs/media/demo.gif)

The GIF is generated from the local static UI flow. To regenerate it after UI changes:

```bash
./examples/demo.sh
```

The script creates a small sample folder, renders HQ upload and subsidiary workspace screens in headless Chrome, and writes `docs/media/demo.gif`.

***

## Feature Matrix

| Area | Current slice |
|---|---|
| Folder upload | Browser folder picker sends individual files as `multipart/form-data` field `files`; no user-facing zip upload channel. |
| Classification | Profile-driven target and document-type matching with preview, warnings, blocking errors, and persisted task items. |
| Control/data bridge | `delivery.tasks.v1` and `delivery.results.v1` contracts over local file-spool or Kafka. |
| Source reference | Control plane can stage the internal source archive to MinIO/S3; Go worker can read item bytes by object source reference. |
| Sink | Mock sink and S3/MinIO single-part PUT. Multipart/resume and additional sinks are Phase 7+. |
| Redis | Progress pub/sub, short-TTL idempotency guard, result-apply lease, and Go worker fixed-window limiter. |
| Observability | Prometheus metrics and W3C `traceparent` propagation from Python publish to Go processing/upload/result publish. |
| Multi-tenancy | Dev actor headers, tenant/user ownership, repository tenant filters, role-derived workspace access scope. |
| Workspace read view | Subsidiary/HQ workspace listing, object listing, object detail, short-TTL presigned download URL, and minimal static page. |

***

## Quick Start

### 1. Start local dependencies

```bash
cd deploy
docker compose up -d mysql kafka minio minio-init redis otel-collector prometheus grafana
```

### 2. Prepare the control plane

```bash
cd ../control-plane
uv sync --dev
cp .env.example .env
.venv/bin/python -m alembic upgrade head
```

### 3. Run the API

```bash
METRICS_ENABLED=true OBSERVABILITY_ENABLED=true \
  .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 4. Run the worker

```bash
cd ../data-plane
GOTOOLCHAIN=auto GOCACHE=/tmp/smh_go_cache go run ./cmd/worker \
  -transport file \
  -source-mode file \
  -sink mock \
  -metrics-enabled \
  -startup-check=false
```

### 5. Open the UI

Serve `web/public` together with `web/css` and `web/js`, proxying `/api/v1` to the control-plane API:

- HQ upload desk: `web/public/index.html`
- Subsidiary workspace view: `web/public/workspaces.html`

For component-specific setup, see [control-plane/README.md](control-plane/README.md), [data-plane/README.md](data-plane/README.md), [web/README.md](web/README.md), and [deploy/README.md](deploy/README.md).

***

## Architecture

```mermaid
flowchart LR
    hq[HQ upload desk] --> cp[FastAPI control plane]
    cp --> db[(MySQL / SQLite)]
    cp --> redis[(Redis)]
    cp -- source archive --> staging[(MinIO / S3 staging)]
    cp -- delivery.tasks.v1 --> transport[(file-spool / Kafka)]
    transport --> worker[Go data-plane worker]
    worker --> staging
    worker --> sink[(mock / S3 / MinIO sink)]
    worker -- delivery.results.v1 --> transport
    transport --> cp
    cp --> workspace[(workspace read model)]
    sub[Subsidiary workspace view] --> cp
    cp -- presigned URL --> sub
```

One sentence: the Python control plane owns business truth, tenant boundaries, task state, and read authorization; the Go data plane owns byte movement, source/sink protocol adaptation, and worker-side execution telemetry.

***

## Key Invariants

- `DELIVERY_BACKEND=go-worker` is the default minimal complete platform path; the Python uploader is legacy compatibility and does not build the workspace read model.
- Folder upload is the only public upload mode; the browser sends selected folder files directly, while the control plane may build an internal archive for source staging.
- HQ workspace access is derived from actor role and owner scope, not from special-casing `tenant_id == "hq"`.
- Subsidiary workspace access is filtered by `workspace.target_tenant_id == actor.tenant_id`; unauthorized object reads return 404.
- `workspace_object.task_id` and `workspace_object.task_item_id` are required, and `task_item_id` is unique for idempotent result application.
- Presigned download URLs are only minted after workspace/object authorization succeeds.
- Redis does not replace Kafka; it supports ephemeral progress, short-TTL idempotency, leases, and rate limiting.

***

## Documentation

| Document | Purpose |
|---|---|
| [docs/PRD.md](docs/PRD.md) | Product scope, users, non-goals, and success criteria. |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Current architecture, write/read paths, observability topology, and invariants. |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Database entities, constraints, and migration notes. |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Release/tag strategy, completed phases, next phase, and deferred work. |
| [docs/RFC/](docs/RFC/) | Reviewed technical proposals. |
| [docs/ADR/](docs/ADR/) | Accepted architecture decisions. |
| [docs/plans/](docs/plans/) | Phase execution plans and acceptance records. |
| [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) | Contribution, documentation, PR, and verification rules. |

***

## Development Checks

```bash
cd control-plane
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest

cd ../data-plane
GOTOOLCHAIN=auto GOCACHE=/tmp/smh_go_cache go test ./...
```

Docker opt-in tests use `RUN_DOCKER_TESTS=1`; MySQL-specific tests use `RUN_MYSQL_TESTS=1`.

***

## Repository Layout

```text
.
├── control-plane/   Python FastAPI control plane
├── data-plane/      Go worker, transport, source, sink, limiter, metrics, tracing
├── web/             Static HQ upload and subsidiary workspace UI
├── deploy/          Docker Compose, Prometheus, Grafana, OTel Collector
├── profiles/        Classification profiles
├── docs/            PRD, architecture, roadmap, RFCs, ADRs, plans
├── examples/        Demo helpers
└── proto/           Reserved cross-language contract area
```

***

## License

This project is licensed under the [MIT License](LICENSE).
