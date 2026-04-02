---
name: deploy-agentcore
description: "Deploy an agent or MCP server to Amazon Bedrock AgentCore. Covers HTTP agents, A2A agents, and MCP Gateway servers. Includes naming rules, auth patterns, boto3 client usage, and common pitfalls."
argument-hint: "[http-agent|a2a-agent|mcp-server] [folder-path]"
---

# Deploy to Amazon Bedrock AgentCore

This skill is a starter guide for deploying agents and MCP servers to Amazon Bedrock AgentCore. It covers the three most common deployment types with tested, working patterns.

**Extending this skill:** Additional auth methods, frameworks, or deployment patterns can be added as separate markdown files in this skill's directory (e.g., `cognito-auth.md`, `oauth2-auth.md`) and linked from here. Claude Code will load them on demand when referenced. For topics not covered here, refer to the official documentation and GitHub samples linked below.

Help the user deploy an agent or MCP server to Amazon Bedrock AgentCore. The deployment type is specified in `$ARGUMENTS` (one of: `http-agent`, `a2a-agent`, `mcp-server`). An optional folder path may also be provided.

## Reference Code and Documentation

Before writing any deployment code, study these references:

### Tested working examples

These are tested, end-to-end working examples. Clone or fetch from GitHub to use as templates:

- **HTTP agent**: https://github.com/aarora79/simple-agentcore/tree/main/simple-agent - IAM + optional Cognito auth
- **A2A agent**: https://github.com/aarora79/simple-agentcore/tree/main/simple-a2a-agent - SigV4 auth
- **MCP server**: https://github.com/aarora79/simple-agentcore/tree/main/simple-mcp - AgentCore Gateway with Cognito JWT auth

Read the relevant example first to understand the deployment pattern.

### Official AWS sample repos

- https://github.com/awslabs/agentcore-samples - Official tutorials covering runtime hosting (Strands, CrewAI, LangGraph, LlamaIndex), Gateway, Identity, Memory, Tools, Observability, and more

### boto3 documentation

- **Data plane** (invoke, memory, auth tokens): https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore.html
  - Client: `boto3.client('bedrock-agentcore')`
- **Control plane** (create/manage runtimes, gateways, targets, credential providers): https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore-control.html
  - Client: `boto3.client('bedrock-agentcore-control')`

### Starter toolkit

- PyPI: `bedrock-agentcore-starter-toolkit>=0.3.0`
- Provides `Runtime()` class for configure/launch/status/invoke
- A2A support requires version 0.3.0+

## Steps

### 1. Determine deployment type

Parse `$ARGUMENTS` to determine what is being deployed:

- **http-agent**: HTTP agent on AgentCore Runtime (REST API, port 8080)
- **a2a-agent**: A2A protocol agent on AgentCore Runtime (JSON-RPC, port 9000)
- **mcp-server**: MCP server on AgentCore Gateway (streamable-http)

If a folder path is provided, read the existing code there first. If not, ask the user what they want to deploy.

### 2. Study the matching example

Fetch the corresponding example from GitHub:

- `http-agent` -> https://github.com/aarora79/simple-agentcore/tree/main/simple-agent
- `a2a-agent` -> https://github.com/aarora79/simple-agentcore/tree/main/simple-a2a-agent
- `mcp-server` -> https://github.com/aarora79/simple-agentcore/tree/main/simple-mcp

Use these as the primary template.

### 3. Write the deployment code

Follow the patterns below based on deployment type. For topics not covered here (advanced auth, custom frameworks, observability, etc.), refer to the boto3 documentation and https://github.com/awslabs/agentcore-samples for up-to-date patterns.

---

## Critical Patterns and Gotchas

These patterns were learned through extensive testing. Violating them causes hard-to-debug failures.

### General (all deployment types)

- **Always use `bedrock-agentcore-control` for control plane operations** (create, delete, configure runtimes, gateways, targets, credential providers). The `bedrock-agentcore` client is data plane only (invoke, memory, tokens).
- **Agent/runtime names**: letters, numbers, underscores ONLY (no hyphens). Example: `my_calculator_agent`
- **Delete operations** use `bedrock-agentcore-control` client (not the starter toolkit)
- **Status polling**: terminal states are `READY`, `CREATE_FAILED`, `DELETE_FAILED`, `UPDATE_FAILED`. Check for `READY` (not `ACTIVE`) as the success state, though accept both to be safe.
- **Status field path**: `status_response.endpoint["status"]`

### HTTP Agent (AgentCore Runtime)

- The starter toolkit `Runtime()` instance must be shared across `configure()`, `launch()`, `status()`, and `invoke()` calls
- `configure()` must always be called before `status()` or `invoke()`, even for invoke-only runs
- Invoke response: `invoke_response["response"][0]`
- Entrypoint wraps agent in `BedrockAgentCoreApp` (port 8080)
- IAM/SigV4 auth is the default

### Cognito Auth (for HTTP agents)

This skill currently covers Cognito as the auth mechanism. Other auth methods (OAuth2, custom authorizers, etc.) can be added via additional files in this skill directory.

- Pass authorizer config to `configure()`:
  ```python
  authorizer_configuration={
      "customJWTAuthorizer": {
          "discoveryUrl": f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration",
          "allowedClients": [client_id]
      }
  }
  ```
- Invoke with bearer token: `agentcore_runtime.invoke(payload, bearer_token=token)`
- Without bearer token on Cognito-protected agent: `AccessDeniedException: Agent is configured for a different authorization method`
- Cognito setup: auto-create User Pool, App Client, and test user
- Save Cognito config to `.cognito_config.json` (add to .gitignore)
- Move passwords to `.env` file, read with `os.getenv()` with defaults

### A2A Agent (AgentCore Runtime)

- Use `protocol="A2A"` in `configure()` call
- Entrypoint uses `A2AServer` (from a2a-python) wrapping FastAPI + uvicorn on **port 9000** (not 8080)
- Use `serve_at_root=True` in A2AServer
- Invocation uses `a2a-python` client with SigV4 auth (NOT the starter toolkit's invoke method)
- Runtime URL format: `https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{url_encoded_arn}/invocations/`
- Exposes `/.well-known/agent-card.json` for agent discovery
- Requires `bedrock-agentcore-starter-toolkit>=0.3.0` for native A2A support

### MCP Server (AgentCore Gateway)

#### Gateway naming
- Gateway names: alphanumeric + hyphens ONLY (no underscores) - pattern `([0-9a-zA-Z][-]?){1,48}`
- This is the OPPOSITE of agent names (which allow underscores but not hyphens)

#### Target naming (CRITICAL)
- Target names become part of MCP tool names: `{targetName}___{operationId}` (triple underscore)
- Bedrock tool name constraint: `[a-zA-Z][a-zA-Z0-9_]*` max 64 chars
- **NO HYPHENS in target names** - hyphens in the target name produce invalid tool names
- Use short, alphanumeric target names like `"geolocation"` not `"geo-mcp-geolocation-target"`

#### OpenAPI targets
- Server URLs in OpenAPI specs MUST use HTTPS (HTTP is rejected)
- Every target requires at least one `credentialProviderConfigurations` entry, even for free APIs with no auth
- Use `API_KEY` credential provider type for OpenAPI targets (not `GATEWAY_IAM_ROLE`)
- Create the credential provider first, then reference its ARN in the target config

#### Gateway status lifecycle
- `CREATING` -> `READY` (not `ACTIVE`)
- Poll for status, accept both `READY` and `ACTIVE` as success
- Also check for `FAILED` status and extract `statusReasons` for error details

#### Cognito M2M auth (for MCP Gateway)
- Create: resource server + scopes + app client with `client_credentials` grant + domain
- Cognito domain required for token endpoint: `https://{domain}.auth.{region}.amazoncognito.com/oauth2/token`
- Token request uses client_credentials grant with client_id and client_secret

#### Delete order (CRITICAL)
1. Delete targets first (and wait for completion)
2. Wait 10 seconds
3. Delete gateway
4. Delete credential provider (uses `name` parameter, NOT `credentialProviderName`)
5. Delete Cognito resources (domain deletion may fail due to IAM permissions - this is a known issue)

#### Generated files
- `.deployment_info.json` - stores gateway_url, gateway_id, user_pool_id, client_id for reuse
- `.token` - bearer token file (refreshed by get_token.sh)
- `get_token.sh` - standalone script to refresh Cognito token
- All generated files must be in `.gitignore`

#### MCP JSON-RPC testing flow
1. `initialize` request (save `Mcp-Session-Id` response header)
2. `tools/list` request (pass `Mcp-Session-Id` header)
3. `tools/call` request (pass `Mcp-Session-Id` header, provide tool name and arguments)

---

## Deployment Script Structure

Every deployment script should follow this pattern:

```python
#!/usr/bin/env python3
"""Deploy <type> to Amazon Bedrock AgentCore."""

import argparse
import json
import logging
import os
import sys
import time

import boto3

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)
logger = logging.getLogger(__name__)

# Constants at top of file
POLL_INTERVAL_SECONDS: int = 10
MAX_POLL_ATTEMPTS: int = 30
REGION: str = os.getenv("AWS_REGION", "us-east-1")


# Private functions first (underscore prefix)
def _wait_for_ready(client, resource_id):
    """Poll until resource is READY."""
    ...


def _create_resources(...):
    """Create the required AWS resources."""
    ...


def _delete_all_resources(...):
    """Clean up all created resources."""
    ...


# Public functions
def deploy(...):
    """Main deployment orchestrator."""
    ...


def main():
    """Parse args and delegate to deploy functions."""
    parser = argparse.ArgumentParser(
        description="Deploy to Amazon Bedrock AgentCore"
    )
    # Add arguments...
    args = parser.parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    deploy(args)


if __name__ == "__main__":
    main()
```

### Key requirements for deployment scripts

- Use `argparse` with `--deploy`, `--invoke-only`, `--delete`, `--auth` flags
- Support `--debug` flag to set logging to DEBUG
- Log configuration at startup
- Show elapsed time after long operations
- Save deployment state to JSON files for reuse
- Handle both fresh deploy and re-deploy (delete + create)
- Always run `uv run python -m py_compile <filename>` after writing

---

## Validation

After writing deployment code, validate:

1. Run `uv run python -m py_compile <filename>` on every Python file
2. Check that all boto3 clients use the correct service name (`bedrock-agentcore` vs `bedrock-agentcore-control`)
3. Verify naming rules are followed (no hyphens in agent names, no underscores in gateway names, no hyphens in target names)
4. Confirm `.gitignore` includes all generated files (`.env`, `.cognito_config.json`, `.deployment_info.json`, `.token`)
5. Verify auth configuration matches the chosen auth type

---

## Report results

After writing the deployment code, output in this format:

```
Deployment code ready. Here's a summary:

Type: <http-agent|a2a-agent|mcp-server>
Files created/modified:
  - <file1> - <purpose>
  - <file2> - <purpose>

Auth: <IAM|Cognito JWT|SigV4> (<details>)

Commands:
  # Deploy
  uv run python deploy_<type>.py --deploy

  # Invoke
  uv run python deploy_<type>.py --invoke-only --prompt "your prompt"

  # Delete and redeploy
  uv run python deploy_<type>.py --delete && uv run python deploy_<type>.py --deploy

Validation: py_compile passed for all files
```
