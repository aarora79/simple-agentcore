# Simple AgentCore

## Project Summary

Three standalone examples for deploying to Amazon Bedrock AgentCore, organized into subfolders.

## Folder Structure

```
simple-agent/          - HTTP agent (tested, working)
simple-a2a-agent/      - A2A protocol agent (tested, working)
simple-mcp/            - MCP server on Gateway (not yet tested)
```

### simple-agent/ (AgentCore Runtime, HTTP) - TESTED, WORKING
- `deploy_agent.py` - Deploy script using starter toolkit configure/launch/status/invoke
- `agent_entrypoint.py` - Strands agent with calculator tool, wrapped in `BedrockAgentCoreApp`
- Auth: IAM (SigV4) by default, or Cognito JWT with `--auth cognito`
- Cognito setup auto-creates User Pool, App Client, and test user
- Cognito config saved to `.cognito_config.json` (gitignored)

### simple-a2a-agent/ (AgentCore Runtime, A2A) - TESTED, WORKING
- `deploy_a2a_agent.py` - Deploy script with `protocol="A2A"` in configure
- `a2a_agent_entrypoint.py` - Calculator agent wrapped in `A2AServer` (FastAPI + uvicorn on port 9000)
- `client.py` - A2A client with SigV4 auth for invoking the agent and fetching agent card
- Exposes `/.well-known/agent-card.json` for agent discovery
- Auth: IAM (SigV4)

### simple-mcp/ (AgentCore Gateway) - TESTED, WORKING
- `deploy_mcp_server.py` - Deploys ipwho.is as MCP server with Cognito JWT auth
- `openapi-specs/geolocation_openapi.json` - OpenAPI spec for geolocation API (ipwho.is, HTTPS)
- `get_token.sh` - Standalone script to refresh Cognito token (saves to `.token`)
- Generated files: `.deployment_info.json`, `.roo.json`, `.token` (all gitignored)
- Auth: Cognito M2M (client_credentials flow) with JWT bearer tokens

## What Needs To Be Done

- [x] Run `uv sync` to install dependencies
- [x] Test simple-agent/ deploy_agent.py end-to-end
- [x] Test simple-a2a-agent/ deploy_a2a_agent.py end-to-end
- [x] Test simple-mcp/ deploy_mcp_server.py end-to-end
- [ ] Test cleanup/delete flow for all three

## Reference Code

Based on [agentcore-samples](https://github.com/awslabs/agentcore-samples):

- [HTTP agent entrypoint](https://github.com/awslabs/agentcore-samples/blob/main/01-tutorials/01-AgentCore-runtime/01-hosting-agent/01-strands-with-bedrock-model/strands_claude.py)
- [HTTP runtime notebook](https://github.com/awslabs/agentcore-samples/blob/main/01-tutorials/01-AgentCore-runtime/01-hosting-agent/01-strands-with-bedrock-model/runtime_with_strands_and_bedrock_models.ipynb)
- [A2A agent with SigV4](https://github.com/awslabs/agentcore-samples/tree/main/01-tutorials/01-AgentCore-runtime/05-hosting-a2a/02-a2a-agent-sigv4)
- [MCP gateway with Cognito](https://github.com/awslabs/agentcore-samples/blob/main/01-tutorials/02-AgentCore-gateway/02-transform-apis-into-mcp-tools/01-transform-openapi-into-mcp-tools/01-openapis-into-mcp-api-key.ipynb)

## Key Patterns Learned

- The starter toolkit `Runtime()` instance must be shared across configure/launch/status/invoke calls
- `configure()` must always be called before status/invoke (even for `--invoke-only`)
- Status: `status_response.endpoint["status"]`, terminal states: `READY`, `CREATE_FAILED`, `DELETE_FAILED`, `UPDATE_FAILED`
- Invoke response (HTTP): `invoke_response["response"][0]`
- Delete uses `bedrock-agentcore-control` client (not `bedrock-agentcore`)
- Agent names: letters, numbers, underscores only (no hyphens)
- A2A agents: `protocol="A2A"` in configure, port 9000 (not 8080), `serve_at_root=True`, FastAPI + uvicorn
- A2A invocation uses `a2a-python` client with SigV4 auth (NOT the starter toolkit's invoke method)
- A2A runtime URL format: `https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{url_encoded_arn}/invocations/`
- Requires `bedrock-agentcore-starter-toolkit>=0.3.0` for native `protocol="A2A"` support
- Cognito inbound auth: pass `authorizer_configuration={"customJWTAuthorizer": {"discoveryUrl": url, "allowedClients": [id]}}` to configure
- Cognito invoke: `agentcore_runtime.invoke(payload, bearer_token=token)`
- Without bearer token on Cognito-protected agent: `AccessDeniedException: Agent is configured for a different authorization method`

### MCP Gateway Patterns

- Gateway APIs use `bedrock-agentcore-control` boto3 client (NOT `bedrock-agentcore`)
- Gateway names: alphanumeric + hyphens only (no underscores) - pattern `([0-9a-zA-Z][-]?){1,48}`
- Gateway status lifecycle: `CREATING` -> `READY` (not `ACTIVE`, though both should be accepted)
- OpenAPI targets require HTTPS server URLs (HTTP rejected)
- OpenAPI targets require at least one `credentialProviderConfigurations` entry (even for free APIs with no auth)
- Use `API_KEY` credential provider type for OpenAPI targets (not `GATEWAY_IAM_ROLE`)
- Target names become part of MCP tool names: `{targetName}___{operationId}` (triple underscore)
- Bedrock tool name constraint: `[a-zA-Z][a-zA-Z0-9_]*` max 64 chars - NO HYPHENS in target names
- Cognito M2M auth: resource server + scopes + app client with `client_credentials` grant + domain
- Cognito domain required for token endpoint: `https://{domain}.auth.{region}.amazoncognito.com/oauth2/token`
- Delete order: targets first (with wait) -> gateway -> credential provider -> Cognito resources
- Credential provider delete uses `name` parameter (not `credentialProviderName`)
- `.deployment_info.json` stores gateway_url, gateway_id, user_pool_id, client_id for reuse
- `.roo.json` format: `{"mcpServers": {"name": {"type": "streamable-http", "url": "...", "headers": {"Authorization": "Bearer ..."}}}}`
- MCP JSON-RPC flow: initialize (save Mcp-Session-Id header) -> tools/list -> tools/call

## Coding Standards

Follow `/home/ubuntu/repos/CLAUDE.md` (logging format, function style, no emojis, etc.).
