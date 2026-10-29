---
id: resume
title: Resume
---

<!-- Transcribed from Aditya_Sumanth_backend.pdf. Contact details are left out
     on purpose: snippets from this file appear on the result page, and the
     public demo should not publish a phone number. Each "## " heading is one
     retrievable, separately citable chunk. -->

## Summary

Backend engineer with 1+ years building and operating production Python
services — REST APIs with FastAPI, event-driven microservices on AWS and GCP,
and the data layers behind them. Comfortable owning a service end to end: API
and database design, schema and migration work, containerized deploys on Docker
and Kubernetes, CI/CD under Git, and production debugging with structured
logging, metrics and alerting. Daily user of AI-assisted development tools
(Claude Code, GitHub Copilot CLI) across roughly a year of real project work.

## Languages

Python, SQL, Go, shell scripting (Bash), PowerShell.

## Backend and APIs

FastAPI, REST API design, microservices, event-driven services (Pub/Sub,
SQS-style queues), pytest, schema design and migrations.

## Databases

Relational data modeling and SQL optimization, BigQuery, Cosmos DB (NoSQL),
indexing, partitioning, schema design and migrations.

## Cloud

AWS (Lambda, event-driven serverless). GCP (BigQuery, Dataflow, Pub/Sub, Cloud
Storage, Cloud Run, Cloud Composer). Azure (Functions, Cosmos DB).

## Platform and CI/CD

Docker, Kubernetes, Terraform, Git, GitHub Actions, Jenkins, Cloud Build,
Linux.

## Monitoring and incident response

Prometheus, Cloud Logging and Cloud Monitoring, alerting, incident response.

## AI-assisted development

Claude Code, GitHub Copilot CLI and Google Antigravity, used daily for
implementation, refactoring, test generation and review, with outputs validated
by tests and code review. Every generated change is reviewed and tested before
merge.

## Independent backend and platform engineer

Backend / Platform Engineer, Independent, remote, Aug 2025 – present. Contract
engagements and self-directed backend and cloud platform work across AWS, GCP
and Azure; builds are public on GitHub.

- Built a Python backend for financial workloads — a FastAPI service over a
  streaming ingestion path (Cloud Storage → Pub/Sub → Dataflow → BigQuery),
  validated on a 200M-record dataset, with dev and prod environments defined in
  Terraform.
- Engineered a zero-data-loss ingestion service into Cosmos DB — idempotency
  keys and ETag-guarded concurrent writes to make retries safe under
  concurrency, with 99% domain coverage across 195 automated tests.
- Designed relational and NoSQL data models and optimized SQL query
  performance, applying indexing, partitioning and migration practices to keep
  read paths fast as data grew.
- Ran services in production: Docker images and Kubernetes deploys,
  standardized CI/CD pipelines under Git, structured logging, dashboards and
  threshold alerting — closing each incident with a new alert.
- Used AI-assisted development tools (Claude Code, GitHub Copilot CLI) daily
  across these builds for implementation, refactoring and test generation,
  reviewing and testing every generated change before merge.

## Founding engineer at GeniusMediaCo

Founding Engineer, GeniusMediaCo, Jan 2023 – Jun 2025. Part-time, alongside
full-time undergraduate studies; led a team of 3.

- Built and operated event-driven microservices end to end on GCP and AWS with
  Docker, Kubernetes and Terraform, from build through monitoring.
- Automated manual delivery steps with Python and shell scripting, working
  across time zones with clients and reviewing teammates' code.

## Smart Geolocation Attendance System

Event-driven backend on AWS Lambda handling 1,250 events/sec of streaming
traffic, with >97% GPS-spoofing detection.
github.com/addiiiooo/smart-geolocation-attendance

## pulse-api

Go service on Kubernetes: liveness and readiness probes, Prometheus metrics and
graceful SIGTERM draining for zero-downtime rolling updates; distroless,
non-root, multi-arch Docker image. github.com/addiiiooo/multi-cloud-platform

## ciburn

Open-source Python CLI and GitHub Action that analyzes CI cost by joining
workflow YAML to run history. Processed 1,993 public repositories (56,682 jobs,
37,727 API requests, zero errors) and found 7.5% of billed minutes never ran.
github.com/addiiiooo/ciburn

## GCP ETL and orchestration pipeline

Python and SQL batch workflow orchestrated with Airflow DAGs: extract from
Cloud Storage, transform in Dataflow, load BigQuery for downstream analytics.

## Education

Bachelor of Technology (B.Tech), Computer Science and Engineering, SRM
Institute of Science and Technology (SRM-KTR), Chennai, Sept 2021 – Jul 2025.
Coursework: DBMS, Cloud Computing, DevOps, DSA.

## Certifications

Microsoft DP-700 (Fabric Data Engineer) — exam Sept 2026. Building with the
Claude API — Claude Academy, in progress.

## Shopify e-commerce founder

Founder, Shopify e-commerce, Aug 2019 – Mar 2021: stores serving US and EU
customers to five-figure revenue.
