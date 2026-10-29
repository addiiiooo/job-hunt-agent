---
id: project-gcp-finance-platform
title: Project — GCP Enterprise Finance Platform
---

<!-- Written from the repository itself (README, docs/, dataflow/, infra/,
     tests/), not from the resume. github.com/addiiiooo/gcp-enterprise-finance-platform -->

## Overview

An event-driven financial transaction analytics platform on Google Cloud,
provisioned with Terraform. Transaction CSV files land in Cloud Storage, a
bucket notification publishes to Pub/Sub, a Dataflow (Apache Beam) streaming
job validates the rows and loads them into a partitioned, clustered BigQuery
table, and a FastAPI service on Cloud Run serves analytics over BigQuery.
Python throughout: generator, pipeline and API.

## Ingestion and streaming pipeline

Cloud Storage OBJECT_FINALIZE notifications go to a Pub/Sub topic. The Beam
pipeline reads the subscription, reads each finalized CSV, parses and validates
every row, and streams valid rows into BigQuery with streaming inserts. Rows
missing a required field (transaction id, user id, amount, timestamp) or with
an unparseable decimal amount are rejected, logged and counted on Beam metrics
counters for valid and invalid records. The parse and validate logic lives in a
module with no Beam imports so it can be unit-tested without a runner. Poison
messages go to a Pub/Sub dead-letter topic after a bounded number of delivery
attempts.

## BigQuery data model

The transactions table is time-partitioned on the transaction timestamp and
clustered on category and merchant, with an optional require-partition-filter
setting and partition expiration. Every row is stamped with an ingestion
timestamp. Analytical SQL covers category breakdown, daily trends, monthly
spend, top merchants, payment methods and per-user summaries, all written as
partition-filtered queries.

## Analytics API

A FastAPI service deployed to Cloud Run, structured as routers, schemas and a
BigQuery service layer. It exposes a health endpoint and analytics endpoints for
category breakdown and daily trends that run the partition-filtered SQL against
BigQuery.

## Infrastructure as code

All resources are Terraform: Cloud Storage, Pub/Sub, the Dataflow bucket,
BigQuery, IAM, Cloud Run, Cloud KMS, logging and monitoring, as shared modules
consumed by two isolated root modules, dev and prod, with separate remote-state
prefixes. Prod turns on customer-managed encryption keys and deletion
protection; dev keeps them optional. Shell scripts bootstrap the state bucket,
apply and destroy environments, generate and upload data, and launch the
Dataflow job.

## Security

Dedicated least-privilege service accounts per workload (Dataflow worker,
Cloud Run API), granted resource-scoped roles on the bucket, dataset and
subscription rather than project-wide roles. Cloud KMS customer-managed keys
with automatic rotation encrypt Cloud Storage, BigQuery and Pub/Sub in prod.
Uniform bucket-level access and enforced public-access prevention on every
bucket.

## Observability and alerting

A log-based error metric over Dataflow pipeline errors, with alert policies in
Terraform for three conditions: Pub/Sub backlog (oldest unacked message age
above a threshold), an elevated Cloud Run 5xx response rate, and any pipeline
error log entry. Alerts route to an email notification channel, alongside a log
sink and a monitoring dashboard.

## Testing and status

A seeded synthetic data generator produces realistic transaction CSVs
(1,000,000 transactions over 90 days for 10,000 users by default). Offline unit
tests cover the generator, the parse and validate transforms, schema
consistency between generator and pipeline, and the API health endpoint. A full
pipeline-to-BigQuery run and the BigQuery-backed API responses need a live GCP
project; the repository states this rather than claiming them.
