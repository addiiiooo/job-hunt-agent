---
id: project-azure-ingestion-platform
title: Project — Azure Enterprise Data Ingestion Platform
---

<!-- Written from the repository itself (README, docs/adr, docs/performance.md,
     docs/production-readiness.md), not from the resume.
     github.com/addiiiooo/azure-enterprise-data-ingestion-platform -->

## Overview

A serverless ingestion pipeline on Azure for warehouse inventory CSV uploads
from hundreds of branches. Files land in Blob Storage, move through four
event-driven stages (intake, validate, load, ledger) and end as current-state
documents in Cosmos DB, with a per-file audit trail and machine-readable error
reports. The engineering focus is correctness under concurrency and failure:
at-least-once event delivery, duplicate uploads, out-of-order files, corrupt
CSVs and mid-pipeline crashes are handled by design and verified by tests.
Python 3.11, mypy strict, ruff.

## Architecture

Four single-purpose Azure Functions connected by Event Grid, with immutable
blob containers as stage boundaries: landing → intake → accepted → validate →
validated → load → Cosmos DB, and the Cosmos change feed fanning changes out to
an append-blob ledger. Because every stage input is an immutable blob, replaying
a stage means re-emitting one event, and replay is the sanctioned remedy for
every failure mode in the runbook. Business logic lives in a pure-Python package
with no Azure SDK imports, enforced by an AST-based test; the Functions layer
holds only trigger and SDK adapters.

## Idempotency and concurrency

Three layers of idempotency: event-level (create-if-absent on a deterministic
file identity, SHA-256 of container, path and ETag), content-level (SHA-256 of
the blob content, so a re-upload of identical content never reaches load) and
row-level (deterministic document ids). Stale-write protection is a pure
newer-instant-wins policy executed as point read, decide, then a conditional
write with an ETag precondition, so an out-of-order older upload cannot
overwrite newer state. Concurrency needs no leases: every side effect is
idempotent and the final state transition is ETag-guarded, so the loser of a
race reports already-processed. Transient ordering races propagate to Event
Grid for retry and eventual dead-lettering.

## Validation and error reporting

Versioned declarative schema contracts (columns, types, ranges, forbidden
characters) interpreted by a separate engine, layered as structural, contract
and semantic checks (SKU and supplier existence against reference data).
Threshold-based partial ingestion: valid rows load and invalid rows are
quarantined unless the file's error rate exceeds a threshold, 5% by default with
per-branch overrides. Twenty stable, machine-readable error codes, and a report
written for every file. Validation writes a manifest with row count and SHA-256
before the rows file, and load verifies both — an artifact integrity gate.

## Cosmos DB data model

Containers partitioned by access pattern: inventory by branch id (current state
per branch and SKU), a file registry by file id (per-file state machine, counts,
history and content-hash claims), reference data by type, and change-feed
leases. Record-level history is appended to per-branch, per-day NDJSON append
blobs, keeping history at blob prices rather than Cosmos prices.

## Observability

Structured logging with flat dimensions (stage, outcome, file id, branch id,
duration) mapping to Application Insights custom dimensions, with the file id
as the correlation id in every log line and artifact path. OpenTelemetry is
configured only when an Application Insights connection string is present. Five
KQL queries (file journey, stage outcomes, quarantine rate by branch, latency
percentiles, stale-write detection), an Azure Monitor workbook, and three alert
rules: Event Grid dead-lettered count, a quarantine-rate spike and an
artifact-integrity failure.

## Security and infrastructure

Identity-based connections only, with system-assigned managed identity and
RBAC; a Pester test asserts generated app settings contain no key material.
gitleaks secret scanning and pip-audit in CI. TLS 1.2 minimum, public blob
access disabled, Cosmos continuous backup. Deployment is PowerShell over Az
modules with convergent, re-runnable functions and WhatIf support; resource
names are derived with SHA-256 because PowerShell randomizes string hash codes
per process — a real defect caught by a determinism test.

## Testing and performance

191 unit tests and 4 integration tests against Azurite, plus 16 Pester
assertions. 99% line coverage of the core ingestion package, with an 85% floor
in CI. Fault injection at every port-operation index asserts a bit-identical
final state after a crash at any point; a poison-file corpus and
property-based round-trip tests with Hypothesis cover malformed input and
serialization of money, timestamps and unicode. Measured on one core: about
26,600 rows/s on the validate path and 58,500 rows/s on the load parse path,
with flat memory (10.6 MB peak for a 100k-row file) because rows stream through
a spool file.

## Status

A complete, tested implementation that has not been deployed to a live Azure
subscription. The repository separates what is verified mechanically, what is
modelled (the Cosmos request-unit profile and the end-to-end latency target)
and a first-deploy checklist for what only a live subscription can prove.
