#!/bin/sh
set -e

USERNAME=$(bashio::config 'username')
PASSWORD=$(bashio::config 'password')
BASE_URL=$(bashio::config 'base_url')

export ROHLIK_USERNAME="$USERNAME"
export ROHLIK_PASSWORD="$PASSWORD"
export ROHLIK_BASE_URL="$BASE_URL"

exec mcp-proxy --port 8811 -- npx @tomaspavlin/rohlik-
mcp
