# simple-agentcore

Simple examples for deploying agents and MCP servers on Amazon Bedrock AgentCore.

## Contents

1. **deploy_agent.py** - Deploy a simple Strands agent (with a calculator tool) on AgentCore Runtime
2. **deploy_mcp_server.py** - Deploy a free geolocation API (ip-api.com) as an MCP server on AgentCore Gateway with Cognito JWT authorization
3. **agent_entrypoint.py** - The agent code that runs on AgentCore Runtime

## Prerequisites

- Python 3.11+
- AWS credentials configured with access to Amazon Bedrock AgentCore
- `uv` package manager installed

## Setup

```bash
uv sync
```

## Usage

### Deploy an Agent on AgentCore Runtime

```bash
# Full deployment (configure, launch, wait, invoke)
uv run python deploy_agent.py --agent-name my-simple-agent --region us-east-1

# Check status of a deployed agent
uv run python deploy_agent.py --agent-name my-simple-agent --region us-east-1 --status-only

# Invoke an already deployed agent
uv run python deploy_agent.py --agent-name my-simple-agent --region us-east-1 --invoke-only --prompt "What is 42 * 17?"

# Delete the agent
uv run python deploy_agent.py --agent-name my-simple-agent --region us-east-1 --delete
```

### Deploy an MCP Server on AgentCore Gateway

This creates an MCP server backed by the free ip-api.com geolocation API. It uses Cognito for inbound JWT authorization (Custom JWT Authorizer).

```bash
# Full deployment (IAM role, Cognito, Gateway, S3 upload, target, invoke)
uv run python deploy_mcp_server.py --gateway-name geo-mcp --region us-east-1

# Invoke only (requires prior deployment, reads saved deployment info)
uv run python deploy_mcp_server.py --gateway-name geo-mcp --region us-east-1 --invoke-only

# Custom prompt
uv run python deploy_mcp_server.py --gateway-name geo-mcp --region us-east-1 --invoke-only \
    --prompt "Where is IP address 1.1.1.1 located?"

# Delete all resources (gateway, Cognito, IAM role)
uv run python deploy_mcp_server.py --gateway-name geo-mcp --region us-east-1 --delete
```

## Architecture

### Agent on AgentCore Runtime

```
User -> deploy_agent.py -> AgentCore Runtime -> agent_entrypoint.py (Strands Agent + Calculator Tool)
```

### MCP Server on AgentCore Gateway

```
User -> deploy_mcp_server.py -> Cognito (JWT token) -> AgentCore Gateway (MCP) -> ip-api.com (geolocation)
```

The gateway converts the OpenAPI spec into MCP tools that a Strands agent can call. Inbound requests are authorized via Cognito Custom JWT Authorizer. The geolocation API is free and needs no outbound credentials.
