# Simple AgentCore examples

Simple examples for deploying agents and MCP servers on Amazon Bedrock AgentCore.

## Contents

```
simple-agent/          - HTTP agent on AgentCore Runtime (tested, working)
simple-a2a-agent/      - A2A protocol agent on AgentCore Runtime (tested, working)
simple-mcp/            - MCP server on AgentCore Gateway (tested, working)
```

| Folder | Protocol | Auth | What it does |
|--------|----------|------|-------------|
| simple-agent/ | HTTP | IAM (SigV4) or Cognito (JWT) | Calculator agent using BedrockAgentCoreApp |
| simple-a2a-agent/ | A2A | IAM (SigV4) | Same calculator agent with A2A server, agent card discovery |
| simple-mcp/ | MCP | Cognito JWT | Geolocation API (ipwho.is) exposed as MCP tools |

## Prerequisites

- Python 3.11+
- AWS credentials configured with access to Amazon Bedrock AgentCore
- `uv` package manager installed

## Setup

```bash
uv sync
```

## Usage

Agent names must start with a letter and contain only letters, numbers, and underscores (no hyphens).

### simple-agent/ (HTTP Agent)

Supports two authentication modes: IAM (SigV4, default) and Cognito (JWT bearer tokens).

> **Registry Integration**: Generate an agent card for the [mcp-gateway-registry](https://github.com/agentic-community/mcp-gateway-registry) using the Claude Code skill at [`.claude/skills/generate-agent-card/SKILL.md`](.claude/skills/generate-agent-card/SKILL.md). Run `/generate-agent-card simple-agent/` in Claude Code to create a registry-compatible agent card JSON.

```bash
cd simple-agent

# Deploy with IAM auth (default)
uv run python deploy_agent.py --agent-name my_simple_agent --region us-east-1

# Deploy with Cognito auth (auto-creates User Pool, App Client, test user)
uv run python deploy_agent.py --agent-name my_simple_agent --region us-east-1 --auth cognito

# Invoke with IAM auth
uv run python deploy_agent.py --agent-name my_simple_agent --region us-east-1 --invoke-only --prompt "What is 42 * 17?"

# Invoke with Cognito auth (auto-refreshes bearer token)
uv run python deploy_agent.py --agent-name my_simple_agent --region us-east-1 --auth cognito --invoke-only --prompt "What is 42 * 17?"

# Setup Cognito only (saves config to .cognito_config.json)
uv run python deploy_agent.py --agent-name my_simple_agent --region us-east-1 --setup-cognito

# Check status
uv run python deploy_agent.py --agent-name my_simple_agent --region us-east-1 --status-only

# Delete agent and Cognito resources
uv run python deploy_agent.py --agent-name my_simple_agent --region us-east-1 --delete
```

### simple-a2a-agent/ (A2A Agent)

Same calculator agent but using the A2A protocol. This enables agent card discovery and agent-to-agent communication. Uses a separate `client.py` for invocation (A2A agents require A2A-formatted messages, not the starter toolkit's invoke method).

The `client.py` reads the agent ARN automatically from `.bedrock_agentcore.yaml` (created after deployment), so no hardcoding is needed.

```bash
cd simple-a2a-agent

# Full deployment (configure with A2A protocol, launch, wait for READY)
uv run python deploy_a2a_agent.py --agent-name my_a2a_agent --region us-east-1

# Invoke the agent via A2A client (reads ARN from .bedrock_agentcore.yaml)
uv run python client.py --prompt "What is 100 / 4 + 25?"

# Get the agent card (pretty printed, saved to agent_card.json)
uv run python client.py --agent-card-only

# Override agent ARN if needed
uv run python client.py --agent-arn <AGENT_ARN> --prompt "What is 2 + 2?"

# Check status
uv run python deploy_a2a_agent.py --agent-name my_a2a_agent --region us-east-1 --status-only

# Delete the agent
uv run python deploy_a2a_agent.py --agent-name my_a2a_agent --region us-east-1 --delete
```

#### Getting the A2A Agent Card

Since this is an A2A agent, the agent card is available directly from the runtime endpoint at `/.well-known/agent.json`. You can derive the full URL from the agent ARN:

```bash
cd simple-a2a-agent

# Step 1: Get the agent ARN and region from .bedrock_agentcore.yaml
# The file is created after deployment, values are nested under the agent name
AGENT_ARN=$(python3 -c "
import yaml
cfg = yaml.safe_load(open('.bedrock_agentcore.yaml'))
agent = cfg['agents'][cfg['default_agent']]
print(agent['bedrock_agentcore']['agent_arn'])
")
REGION=$(python3 -c "
import yaml
cfg = yaml.safe_load(open('.bedrock_agentcore.yaml'))
agent = cfg['agents'][cfg['default_agent']]
print(agent['aws']['region'])
")
ESCAPED_ARN=$(python3 -c "from urllib.parse import quote; print(quote('${AGENT_ARN}', safe=''))")

# Step 2: The runtime URL (this is the agent endpoint)
RUNTIME_URL="https://bedrock-agentcore.${REGION}.amazonaws.com/runtimes/${ESCAPED_ARN}/invocations/"
echo "Agent endpoint: ${RUNTIME_URL}"

# Step 3: The agent card URL is at /.well-known/agent.json relative to the endpoint
AGENT_CARD_URL="${RUNTIME_URL}.well-known/agent.json"
echo "Agent card URL: ${AGENT_CARD_URL}"

# Step 4: Fetch the agent card (requires SigV4 auth, use client.py)
uv run python client.py --agent-card-only
```

Note: The agent card endpoint requires SigV4 authentication (same as agent invocation). Use `client.py --agent-card-only` which handles auth automatically and saves the card to `agent_card.json`.

> **Registry Integration**: Alternatively, you can generate a registry-compatible agent card using the Claude Code skill at [`.claude/skills/generate-agent-card/SKILL.md`](.claude/skills/generate-agent-card/SKILL.md) (`/generate-agent-card simple-a2a-agent/`). This is useful for registering with [mcp-gateway-registry](https://github.com/agentic-community/mcp-gateway-registry), but since this is an A2A agent the card can be fetched directly from the live endpoint as shown above.

### simple-mcp/ (MCP Server on Gateway)

Deploys ipwho.is (free HTTPS geolocation API) as an MCP server on AgentCore Gateway with Cognito JWT authorization.

Note: Gateway names use hyphens (not underscores), unlike agent names.

> **Registry Integration**: Generate an MCP server card for the [mcp-gateway-registry](https://github.com/agentic-community/mcp-gateway-registry) using the Claude Code skill at [`.claude/skills/generate-server-card/SKILL.md`](.claude/skills/generate-server-card/SKILL.md). Run `/generate-server-card simple-mcp/` in Claude Code to create a registry-compatible server card JSON.

```bash
cd simple-mcp

# Full deployment (IAM role, Cognito, Gateway, S3 upload, target, invoke)
uv run python deploy_mcp_server.py --gateway-name geo-mcp --region us-east-1

# Invoke only (requires prior deployment, reads saved deployment info)
uv run python deploy_mcp_server.py --gateway-name geo-mcp --region us-east-1 --invoke-only

# Custom prompt
uv run python deploy_mcp_server.py --gateway-name geo-mcp --region us-east-1 --invoke-only \
    --prompt "Where is IP address 1.1.1.1 located?"

# Delete and redeploy from scratch
uv run python deploy_mcp_server.py --gateway-name geo-mcp --region us-east-1 --delete
uv run python deploy_mcp_server.py --gateway-name geo-mcp --region us-east-1

# Delete all resources (gateway, credential provider, Cognito, IAM role)
uv run python deploy_mcp_server.py --gateway-name geo-mcp --region us-east-1 --delete

# Refresh Cognito token only (standalone script)
./get_token.sh
```

#### Generated files (all gitignored)

After deployment or `--invoke-only`, the following files are generated in `simple-mcp/`:

| File | Purpose |
|------|---------|
| `.deployment_info.json` | Gateway URL, ID, Cognito pool/client IDs. Used by `--invoke-only` and `get_token.sh`. |
| `.roo.json` | Ready-to-use Roo Code MCP config. Copy its contents into Roo Code's MCP settings to connect to the gateway. |
| `.token` | Raw Cognito bearer token. Tokens expire after ~1 hour, re-run `--invoke-only` or `./get_token.sh` to refresh. |
| `get_token.sh` | Standalone script to refresh the Cognito token. Updates `.token`. |

#### Testing the MCP server with curl

The MCP protocol uses JSON-RPC over HTTP. You need a valid token before running these commands.

```bash
cd simple-mcp

# Refresh token first (tokens expire after ~1 hour)
./get_token.sh

# Set variables
GW_URL=$(python3 -c "import json;print(json.load(open('.deployment_info.json'))['gateway_url'])")
TOKEN=$(cat .token)

# Initialize MCP session and save session ID
curl -s -X POST "$GW_URL" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}' \
    -D /tmp/mcp_headers > /dev/null
SESSION_ID=$(grep -i mcp-session-id /tmp/mcp_headers | tr -d '\r' | awk '{print $2}')

# List available tools
curl -s -X POST "$GW_URL" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Mcp-Session-Id: $SESSION_ID" \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | python3 -m json.tool

# Call a tool (geolocation lookup for 8.8.8.8)
curl -s -X POST "$GW_URL" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Mcp-Session-Id: $SESSION_ID" \
    -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"geolocation___getGeolocationByIp","arguments":{"ipAddress":"8.8.8.8"}}}' | python3 -m json.tool
```

## Architecture

### HTTP Agent (simple-agent/)

```
User -> deploy_agent.py -> AgentCore Runtime -> BedrockAgentCoreApp -> Strands Agent + Calculator
```

### A2A Agent (simple-a2a-agent/)

```
client.py (SigV4) -> AgentCore Runtime -> A2AServer (FastAPI:9000) -> Strands Agent + Calculator
                                                    |
                                         /.well-known/agent-card.json
```

### MCP Server (simple-mcp/)

```
User -> deploy_mcp_server.py -> Cognito (JWT) -> AgentCore Gateway (MCP) -> ipwho.is
```
