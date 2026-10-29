---
id: project-observability-pipeline
title: Project — Observability Pipeline
---

<!-- TODO: replace — placeholder project write-up. -->

## Overview

TODO: replace. Structured logging, metrics and trace collection for a set of
Python services, built so that one request id threads through every log line it
touches.

## Instrumentation

TODO: replace. JSON logs emitted on a single line per event, with a request id
injected by middleware and carried through a context variable into every
downstream call.

## Kubernetes and deployment

TODO: replace. Services deployed to Kubernetes with readiness probes wired to a
per-dependency health endpoint rather than a bare 200.

## Alerting

TODO: replace. Alert conditions defined against p95 latency and error rate by
error class, with the thresholds written down next to the dashboards.
