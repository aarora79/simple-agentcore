#!/bin/bash
# Get a Cognito bearer token for the AgentCore Gateway MCP server.
# Reads connection info from .deployment_info.json and saves token to .token
#
# Usage:
#   chmod +x get_token.sh
#   ./get_token.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_INFO="$SCRIPT_DIR/.deployment_info.json"

if [ ! -f "$DEPLOY_INFO" ]; then
    echo "ERROR: $DEPLOY_INFO not found. Run deploy_mcp_server.py first."
    exit 1
fi

USER_POOL_ID=$(python3 -c "import json; print(json.load(open('$DEPLOY_INFO'))['user_pool_id'])")
CLIENT_ID=$(python3 -c "import json; print(json.load(open('$DEPLOY_INFO'))['client_id'])")
REGION=$(python3 -c "import json; d=json.load(open('$DEPLOY_INFO')); print(d.get('region', 'us-east-1'))")

# Get client secret from Cognito
CLIENT_SECRET=$(python3 -c "
import boto3
c = boto3.client('cognito-idp', region_name='$REGION')
r = c.describe_user_pool_client(UserPoolId='$USER_POOL_ID', ClientId='$CLIENT_ID')
print(r['UserPoolClient']['ClientSecret'])
")

RESOURCE_SERVER_ID="simple-agentcore-gateway"
POOL_ID_CLEAN=$(echo "$USER_POOL_ID" | tr -d '_')
TOKEN_URL="https://${POOL_ID_CLEAN}.auth.${REGION}.amazoncognito.com/oauth2/token"
SCOPE="${RESOURCE_SERVER_ID}/gateway:read ${RESOURCE_SERVER_ID}/gateway:write"

echo "Requesting token from: $TOKEN_URL"

RESPONSE=$(curl -s -X POST "$TOKEN_URL" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "grant_type=client_credentials" \
    -d "client_id=$CLIENT_ID" \
    -d "client_secret=$CLIENT_SECRET" \
    -d "scope=$SCOPE")

TOKEN=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

if [ -z "$TOKEN" ]; then
    echo "ERROR: Failed to get token. Response:"
    echo "$RESPONSE"
    exit 1
fi

echo "$TOKEN" > "$SCRIPT_DIR/.token"
echo "Token saved to $SCRIPT_DIR/.token"
echo "Token length: ${#TOKEN} characters"
