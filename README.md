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

**Who is this for?**

- **HQ operators** who need to distribute operational data (order KPIs, performance reports, financial statements) to external parties on a regular cadence — without giving them internal network access.
- **External partners, outsourcers, and subsidiaries** who need a clean, scoped workspace to browse and download only the reports that belong to them.
- **Platform engineers** evaluating the design of a multi-component distributed system with traceable architecture decisions.

**The scenario.** Outsourced service providers, franchisees, and partner companies often cannot access the headquarters' internal network. Shared drives, email attachments, and ad-hoc file transfers are the default tools — but they do not provide classification, delivery confirmation, recipient isolation, or operational evidence.

A single misplaced report — a performance scoreboard sent to the wrong outsourcer, an order summary missed in a chat thread — can trigger contract disputes, compliance reviews, and hours of manual reconciliation.

This repository provides a public-reference implementation of a **multi-tenant file delivery platform** built for this cross-boundary scenario:

- HQ selects a folder. Relative paths are preserved.
- A classification engine matches each file to the correct external party and document type.
- HQ reviews and confirms the distribution plan before execution.
- A Go data plane delivers bytes asynchronously to S3-compatible object storage.
- Each external party logs into their own workspace and sees only their files — nothing more.

### Product Gap

Existing tools solve adjacent problems but leave this workflow uncovered:

| Tool | What it does | What it does **not** do |
|---|---|---|
| Shared drives / intranet portals | Internal file sharing | External parties have no account and no access |
| Email attachments | One-off send | No classification, no audit, no recipient isolation across dozens of parties |
| Object storage consoles (S3/MinIO) | Store and serve bytes | No business workflow — no folder intake, no classification, no workspace per recipient |
| MFT / ETL pipelines | Scheduled bulk transfer | Designed for system-to-system integration, not for operator-driven folder upload with preview and confirmation |
| Chat attachments | Quick send | Fragile for recurring batch delivery; no delivery evidence or retry semantics |

These tools can move files. They do not own the business workflow. This repository fills the gap between them: an operator-facing product that owns folder intake, classification, distribution plan review, recoverable delivery, workspace-per-recipient, and operational evidence — end to end.

***

## Demo

![HQ upload to workspace demo](docs/media/demo.gif)

The GIF shows HQ uploading a folder, reviewing the classification plan, and a subsidiary viewing the resulting workspace.

To regenerate after UI changes:

```bash
./examples/demo.sh
```

The script creates a sample folder, renders HQ upload and subsidiary workspace screens in headless Chrome, and writes `docs/media/demo.gif`.

***

## What the Platform Does

### For HQ operators

- **Folder upload desk.** Select a folder in the browser. Files are sent individually — no zip packaging, no integration payload modeling.
- **Classification preview.** Each file is matched against business profiles (target party, document type, destination path). Blocking errors and warnings are visible before delivery begins.
- **Confirm before delivery.** The full distribution plan — which files go to which external party — is reviewed and confirmed in one step.
- **Real-time progress.** SSE pushes task and item-level progress to the browser.
- **Retry failed items.** Individual items or the whole task can be retried without re-uploading.

### For external partners

- **Scoped workspace.** Each external party logs into a workspace filtered to `target_tenant == their_id`. They cannot see files destined for other parties. Unauthorized reads return 404 — existence is not leaked.
- **Browse and download.** Workspace listing, object listing, object detail, and short-TTL presigned download URLs. No bucket access, no backend ACLs to configure.

### For platform operators

- **Observability.** Prometheus RED metrics (Rate/Error/Duration) on HTTP, task lifecycle, delivery publish, source read, sink upload, result apply, and rate limiting. W3C `traceparent` propagation from Python publish through Kafka to Go worker spans.
- **Delivery evidence.** Task events record every state transition with actor attribution. Trace context links the upload trigger, worker execution, and result application into a single distributed trace.
- **Infrastructure.** Local Docker Compose provides MySQL, Kafka, MinIO, Redis, OTel Collector, Prometheus, and Grafana — everything needed to run and observe the full pipeline.

***

## Feature Matrix

| Area | Current capability |
|---|---|
| Folder upload | Browser folder picker; no zip upload API exposed to users |
| Classification | Profile-driven target and document-type matching with preview, warnings, blocking errors, and persisted task items |
| Control/data bridge | `delivery.tasks.v1` and `delivery.results.v1` contracts over file-spool or Kafka |
| Source staging | Control plane builds an internal archive and stages it to MinIO/S3; Go worker reads items by object source reference |
| Sink | Mock sink and S3/MinIO single-part PUT. Multipart/resume and additional sinks are Phase 7+ |
| Redis | Progress pub/sub, short-TTL idempotency guard, result-apply lease, fixed-window worker limiter |
| Observability | Prometheus metrics and W3C `traceparent` from Python publish to Go processing, upload, and result publish |
| Multi-tenancy | Dev actor headers, tenant/user ownership, repository tenant filters, role-derived workspace access scope |
| Workspace read view | Workspace listing, object listing, object detail, short-TTL presigned download URL, and static frontend |

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
- Partner workspace view: `web/public/workspaces.html`

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
    sub[Partner workspace view] --> cp
    cp -- presigned URL --> sub
```

**One sentence.** The Python control plane owns business truth, tenant boundaries, task state, and read authorization; the Go data plane owns byte movement, source/sink protocol adaptation, and worker-side execution telemetry.

Design invariants, failure modes, and write/read path detail: [ARCHITECTURE.md](docs/ARCHITECTURE.md).

***

## For Contributors

### Development checks

```bash
cd control-plane
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest

cd ../data-plane
GOTOOLCHAIN=auto GOCACHE=/tmp/smh_go_cache go test ./...
```

Docker opt-in tests use `RUN_DOCKER_TESTS=1`; MySQL-specific tests use `RUN_MYSQL_TESTS=1`.

Full guidelines: [CONTRIBUTING.md](docs/CONTRIBUTING.md).

### Repository layout

```text
.
├── control-plane/   Python FastAPI control plane
├── data-plane/      Go worker, transport, source, sink, limiter, metrics, tracing
├── web/             Static HQ upload and partner workspace UI
├── deploy/          Docker Compose, Prometheus, Grafana, OTel Collector
├── profiles/        Classification profiles
├── docs/            PRD, architecture, roadmap, RFCs, ADRs, plans
├── examples/        Demo helpers
└── proto/           Reserved cross-language contract area
```

***

## Documentation

| Document | Purpose |
|---|---|
| [docs/PRD.md](docs/PRD.md) | Product scope, users, non-goals, and success criteria |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Current architecture, write/read paths, invariants, and failure modes |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Database entities, constraints, and migration notes |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Release/tag strategy, completed phases, next phase, and deferred work |
| [docs/RFC/](docs/RFC/) | Reviewed technical proposals |
| [docs/ADR/](docs/ADR/) | Accepted architecture decisions |
| [docs/plans/](docs/plans/) | Phase execution plans and acceptance records |
| [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) | Contribution, documentation, PR, and verification rules |

***

## Status

Phase 0–6.5 is complete on `main`: the minimal end-to-end platform is functional and locally smoke-tested. Phase 7 (additional sinks, benchmarks) is next.

See [CHANGELOG.md](CHANGELOG.md) and [docs/ROADMAP.md](docs/ROADMAP.md) for detail.

***

## License

This project is licensed under the [MIT License](LICENSE).
