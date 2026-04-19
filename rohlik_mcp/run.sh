#!/bin/sh
set -e

USERNAME=$(jq -r '.username' /data/options.json)
PASSWORD=$(jq -r '.password' /data/options.json)
BASE_URL=$(jq -r '.base_url' /data/options.json)

export ROHLIK_USERNAME="$USERNAME"
export ROHLIK_PASSWORD="$PASSWORD"
export ROHLIK_BASE_URL="$BASE_URL"

exec mcp-proxy --host 0.0.0.0 --port 8811 -- npx @tomaspavlin/rohlik-mcp
