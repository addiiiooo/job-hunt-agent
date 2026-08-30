# Deploy — Azure Container Apps

Every command below is the exact one to run. `deploy.sh` runs the whole
sequence; this file is what it does and why, so you can run the steps by hand
or debug one of them.

## Decide this first: does the public URL call the paid API?

A publicly reachable endpoint with no auth that calls Claude on every request is
a bill anyone can run up for you. There is no rate limiting in this app, and a
scraper does not care that it is a portfolio project.

So there are two sensible deployments:

| | `LLM_CLIENT=scripted` | `LLM_CLIENT=anthropic` |
|---|---|---|
| Model responses | canned, over live retrieval | live |
| Cost of public traffic | zero | your tokens, unbounded |
| API key needed | no | yes |
| Page states it is a demo | yes, a banner | no |

**Recommended: the public URL runs `scripted`.** Everything a reviewer looks at
— retrieval, the grounding validator, the trace, `/metrics`, `/health` — is
real; only the model responses are canned, and the page says so. Run the live
one locally, or behind auth, when you want to use it for yourself.

Both are the same image. It is one environment variable.

## Prerequisites

```bash
az --version                      # Azure CLI 2.60+
az login
az account set --subscription "<SUBSCRIPTION_ID>"
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights
```

You do **not** need Docker locally. `az acr build` uploads the build context and
builds the image inside Azure.

## 1. Names

ACR names are globally unique and must be alphanumeric only.

```bash
export RG=job-hunt-agent-rg
export LOCATION=centralindia
export ACR=jobhuntagent$RANDOM
export ENVIRONMENT=job-hunt-agent-env
export APP=job-hunt-agent
export TAG=v1
```

## 2. Resource group and registry

```bash
az group create --name "$RG" --location "$LOCATION"

az acr create \
  --resource-group "$RG" \
  --name "$ACR" \
  --sku Basic \
  --admin-enabled true
```

## 3. Build the image in Azure

```bash
az acr build \
  --registry "$ACR" \
  --image "job-hunt-agent:$TAG" \
  .
```

`.dockerignore` keeps tests, scripts, the extension and the docs out of the
build context, so the image carries the app and its runtime dependencies only.

## 4. Container Apps environment

```bash
az containerapp env create \
  --name "$ENVIRONMENT" \
  --resource-group "$RG" \
  --location "$LOCATION"
```

## 5. Deploy

The API key is a Container Apps **secret**, referenced by the env var as
`secretref:`. It is never baked into the image and never appears in the
container's env-var definition.

```bash
ACR_PASSWORD=$(az acr credential show --name "$ACR" --query "passwords[0].value" --output tsv)

az containerapp create \
  --name "$APP" \
  --resource-group "$RG" \
  --environment "$ENVIRONMENT" \
  --image "$ACR.azurecr.io/job-hunt-agent:$TAG" \
  --registry-server "$ACR.azurecr.io" \
  --registry-username "$ACR" \
  --registry-password "$ACR_PASSWORD" \
  --target-port 8000 \
  --ingress external \
  --min-replicas 1 \
  --max-replicas 3 \
  --cpu 0.5 --memory 1.0Gi \
  --secrets "anthropic-api-key=${ANTHROPIC_API_KEY:-unset}" \
  --env-vars \
      LLM_CLIENT=scripted \
      LOG_LEVEL=INFO \
      DB_PATH=/data/runs.db \
      "ANTHROPIC_API_KEY=secretref:anthropic-api-key"
```

For the live variant, change one value:

```bash
az containerapp update --name "$APP" --resource-group "$RG" \
  --set-env-vars LLM_CLIENT=anthropic
```

To rotate the key without redeploying:

```bash
az containerapp secret set --name "$APP" --resource-group "$RG" \
  --secrets "anthropic-api-key=sk-ant-..."
az containerapp revision restart --name "$APP" --resource-group "$RG" \
  --revision "$(az containerapp revision list -n "$APP" -g "$RG" --query '[0].name' -o tsv)"
```

## 6. Get the URL and verify

```bash
FQDN=$(az containerapp show --name "$APP" --resource-group "$RG" \
  --query properties.configuration.ingress.fqdn --output tsv)
echo "https://$FQDN"

curl -s "https://$FQDN/health" | python3 -m json.tool
curl -s "https://$FQDN/metrics" | python3 -m json.tool
```

`/health` must report `status: ok` with `sqlite`, `corpus` and
`anthropic_api_key` all ok. If `corpus` fails with `TODO: replace`, the corpus
placeholders were never filled in — fix that before showing anyone the URL.

Put the resulting URL at the top of `README.md`.

## 7. Redeploy after a change

```bash
export TAG=v2
az acr build --registry "$ACR" --image "job-hunt-agent:$TAG" .
az containerapp update --name "$APP" --resource-group "$RG" \
  --image "$ACR.azurecr.io/job-hunt-agent:$TAG"
```

## 8. Health probe (optional)

Container Apps ignores the Dockerfile's `HEALTHCHECK` and uses its own probes.
To wire the readiness probe to the real endpoint:

```bash
az containerapp update --name "$APP" --resource-group "$RG" --yaml probe.yaml
```

```yaml
# probe.yaml
properties:
  template:
    containers:
      - name: job-hunt-agent
        probes:
          - type: readiness
            httpGet: { path: /health, port: 8000 }
            initialDelaySeconds: 5
            periodSeconds: 30
```

Because `/health` returns 503 whenever any dependency is unhealthy, a replica
with a broken corpus or a missing key is taken out of rotation rather than
serving broken answers.

## 9. Teardown

```bash
az group delete --name "$RG" --yes --no-wait
```

## Known deployment caveats

- **SQLite is on the container filesystem, which is ephemeral.** Run history
  does not survive a restart or a new revision. That is acceptable for run
  history and unacceptable for anything else; mount Azure Files at `/data`, or
  move to Postgres, if the history has to persist.
- **`min-replicas 1` costs money while idle.** It is set because scale-to-zero
  gives the first visitor a cold start of several seconds, and a hiring manager
  clicking a link is exactly that first visitor.
- **A second replica would break the in-process metrics.** `/metrics` is
  per-process, so with `max-replicas 3` under real load you are reading one
  replica's counters. Fine for a portfolio project, wrong for production; the
  fix is an exporter, not a bigger dictionary.
