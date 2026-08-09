#!/usr/bin/env bash
# Push app secrets from .env into SSM Parameter Store (SecureString), keeping
# their values out of Terraform state and shell history. Idempotent.
#
# Usage: AWS_PROFILE=<profile> AWS_REGION=<region> ./scripts/put_aws_secrets.sh
set -euo pipefail
cd "$(dirname "$0")/.."

APP=fax-bot
[ -f .env ] || { echo ".env not found" >&2; exit 1; }

get_env() { { grep -E "^$1=" .env || true; } | head -1 | cut -d= -f2-; }

put() {
  local name=$1 value=$2
  if [ -z "$value" ]; then
    echo "SKIP  /$APP/$name (no value)" >&2
    return
  fi
  aws ssm put-parameter --name "/$APP/$name" --type SecureString \
    --value "$value" --overwrite >/dev/null
  echo "OK    /$APP/$name"
}

put TELNYX_API_KEY "$(get_env TELNYX_API_KEY)"
put TELNYX_PUBLIC_KEY "$(get_env TELNYX_PUBLIC_KEY)"
put ANTHROPIC_API_KEY "$(get_env ANTHROPIC_API_KEY)"

ADMIN_TOKEN="$(get_env ADMIN_TOKEN)"
if [ -z "$ADMIN_TOKEN" ]; then
  ADMIN_TOKEN="$(openssl rand -hex 24)"
  echo "generated new ADMIN_TOKEN (retrieve later with:" >&2
  echo "  aws ssm get-parameter --name /$APP/ADMIN_TOKEN --with-decryption --query Parameter.Value --output text)" >&2
fi
put ADMIN_TOKEN "$ADMIN_TOKEN"
