#!/bin/sh
set -e

USERNAME=$(jq -r '.username' /data/options.json)
PASSWORD=$(jq -r '.password' /data/options.json)
BASE_URL=$(jq -r '.base_url' /data/options.json)


export ROHLIK_USERNAME="$USERNAME"
export ROHLIK_PASSWORD="$PASSWORD"
export ROHLIK_BASE_URL="$BASE_URL"


echo "DEBUG: Username=$ROHLIK_USERNAME"
echo "DEBUG: Base=$ROHLIK_BASE_URL"
echo "DEBUG: options.json=$(cat /data/options.json)"
exec mcp-proxy --host 0.0.0.0 --port 8811 -- npx @tomaspavlin/rohlik-mcp