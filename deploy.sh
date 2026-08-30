#!/usr/bin/env bash
#
# One-command deploy to Azure Container Apps. See DEPLOY.md for what each step
# does. Safe to re-run: it creates what is missing and updates what exists.
#
#   ./deploy.sh                              # scripted mode, no API key needed
#   LLM_CLIENT=anthropic ANTHROPIC_API_KEY=sk-ant-... ./deploy.sh
#
set -euo pipefail

RG="${RG:-job-hunt-agent-rg}"
LOCATION="${LOCATION:-centralindia}"
ENVIRONMENT="${ENVIRONMENT:-job-hunt-agent-env}"
APP="${APP:-job-hunt-agent}"
TAG="${TAG:-v$(date +%Y%m%d%H%M%S)}"
LLM_CLIENT="${LLM_CLIENT:-scripted}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-unset}"

say() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
die() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

command -v az >/dev/null || die "the Azure CLI is not installed — see DEPLOY.md"
az account show >/dev/null 2>&1 || die "not logged in — run 'az login' first"

if [[ "$LLM_CLIENT" == "anthropic" && "$ANTHROPIC_API_KEY" == "unset" ]]; then
  die "LLM_CLIENT=anthropic needs ANTHROPIC_API_KEY in the environment"
fi

# The registry name must be globally unique and alphanumeric. Reuse the one in
# this resource group if it already exists, so re-runs do not sprawl.
if [[ -z "${ACR:-}" ]]; then
  ACR="$(az acr list --resource-group "$RG" --query '[0].name' --output tsv 2>/dev/null || true)"
  ACR="${ACR:-jobhuntagent$RANDOM}"
fi

say "Resource group: $RG ($LOCATION)"
az group create --name "$RG" --location "$LOCATION" --output none

say "Container registry: $ACR"
az acr show --name "$ACR" --resource-group "$RG" --output none 2>/dev/null \
  || az acr create --resource-group "$RG" --name "$ACR" --sku Basic --admin-enabled true --output none

say "Building job-hunt-agent:$TAG inside Azure (no local Docker required)"
az acr build --registry "$ACR" --image "job-hunt-agent:$TAG" . --output none

say "Container Apps environment: $ENVIRONMENT"
az containerapp env show --name "$ENVIRONMENT" --resource-group "$RG" --output none 2>/dev/null \
  || az containerapp env create --name "$ENVIRONMENT" --resource-group "$RG" \
       --location "$LOCATION" --output none

IMAGE="$ACR.azurecr.io/job-hunt-agent:$TAG"
ACR_PASSWORD="$(az acr credential show --name "$ACR" --query 'passwords[0].value' --output tsv)"

if az containerapp show --name "$APP" --resource-group "$RG" --output none 2>/dev/null; then
  say "Updating existing app: $APP"
  az containerapp secret set --name "$APP" --resource-group "$RG" \
    --secrets "anthropic-api-key=$ANTHROPIC_API_KEY" --output none
  az containerapp update --name "$APP" --resource-group "$RG" \
    --image "$IMAGE" \
    --set-env-vars "LLM_CLIENT=$LLM_CLIENT" LOG_LEVEL=INFO DB_PATH=/data/runs.db \
                   "ANTHROPIC_API_KEY=secretref:anthropic-api-key" \
    --output none
else
  say "Creating app: $APP"
  az containerapp create \
    --name "$APP" --resource-group "$RG" --environment "$ENVIRONMENT" \
    --image "$IMAGE" \
    --registry-server "$ACR.azurecr.io" \
    --registry-username "$ACR" --registry-password "$ACR_PASSWORD" \
    --target-port 8000 --ingress external \
    --min-replicas 1 --max-replicas 3 --cpu 0.5 --memory 1.0Gi \
    --secrets "anthropic-api-key=$ANTHROPIC_API_KEY" \
    --env-vars "LLM_CLIENT=$LLM_CLIENT" LOG_LEVEL=INFO DB_PATH=/data/runs.db \
               "ANTHROPIC_API_KEY=secretref:anthropic-api-key" \
    --output none
fi

FQDN="$(az containerapp show --name "$APP" --resource-group "$RG" \
  --query properties.configuration.ingress.fqdn --output tsv)"
URL="https://$FQDN"

say "Waiting for $URL/health"
for _ in $(seq 1 40); do
  if curl -sf "$URL/health" -o /tmp/jha-health.json; then break; fi
  sleep 3
done

if [[ -s /tmp/jha-health.json ]]; then
  python3 -m json.tool < /tmp/jha-health.json
else
  printf '\033[33m! /health did not return 200 — a dependency is unhealthy.\033[0m\n'
  curl -s "$URL/health" | python3 -m json.tool || true
fi

say "Live at $URL"
echo "Put that URL at the top of README.md."
