"""Deploy a geolocation API as an MCP server on Amazon Bedrock AgentCore Gateway.

This script:
1. Creates an IAM role for the AgentCore Gateway
2. Sets up Cognito for inbound JWT authorization (Custom JWT Authorizer)
3. Creates the AgentCore Gateway with MCP protocol
4. Uploads the geolocation OpenAPI spec to S3
5. Creates a gateway target pointing to the OpenAPI spec
6. Gets a Cognito access token and invokes the MCP server via a Strands agent

The geolocation API used is ip-api.com which is free and requires no API key.

Usage:
    # Full setup: create gateway, cognito, target, and invoke
    uv run python deploy_mcp_server.py --gateway-name geo-mcp --region us-east-1

    # Invoke only (gateway already exists)
    uv run python deploy_mcp_server.py --gateway-name geo-mcp --region us-east-1 --invoke-only

    # Delete all resources
    uv run python deploy_mcp_server.py --gateway-name geo-mcp --region us-east-1 --delete
"""

import argparse
import json
import logging
import os
import time
from typing import (
    Optional,
    Tuple,
)

import boto3
from boto3.session import Session
from botocore.exceptions import ClientError

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

logger = logging.getLogger(__name__)

DEFAULT_REGION = "us-east-1"
USER_POOL_NAME = "SimpleAgentCoreGatewayPool"
RESOURCE_SERVER_ID = "simple-agentcore-gateway"
RESOURCE_SERVER_NAME = "SimpleAgentCoreGateway"
CLIENT_NAME = "SimpleAgentCoreMCPClient"
OPENAPI_SPEC_FILE = "openapi-specs/geolocation_openapi.json"
IAM_ROLE_CREATION_WAIT_SECONDS = 10
GATEWAY_TARGET_READY_WAIT_SECONDS = 10
GATEWAY_POLL_INTERVAL_SECONDS = 10
GATEWAY_MAX_POLL_ATTEMPTS = 30
SCOPES = [
    {
        "ScopeName": "gateway:read",
        "ScopeDescription": "Read access to the gateway",
    },
    {
        "ScopeName": "gateway:write",
        "ScopeDescription": "Write access to the gateway",
    },
]


def _get_region(
    cli_region: Optional[str] = None,
) -> str:
    """Determine the AWS region to use."""
    if cli_region:
        return cli_region
    boto_session = Session()
    region = boto_session.region_name
    if region:
        return region
    return DEFAULT_REGION


def _get_account_id() -> str:
    """Get the current AWS account ID."""
    return boto3.client("sts").get_caller_identity()["Account"]


def _create_gateway_iam_role(
    gateway_name: str,
    region: str,
) -> dict:
    """Create an IAM role for the AgentCore Gateway.

    Args:
        gateway_name: Name of the gateway (used in role name)
        region: AWS region

    Returns:
        IAM role response dict
    """
    iam_client = boto3.client("iam")
    role_name = f"agentcore-{gateway_name}-role"
    account_id = _get_account_id()

    role_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AgentCoreGatewayPermissions",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:*",
                    "bedrock:*",
                    "agent-credential-provider:*",
                    "iam:PassRole",
                    "secretsmanager:GetSecretValue",
                    "lambda:InvokeFunction",
                    "s3:GetObject",
                ],
                "Resource": "*",
            }
        ],
    }

    assume_role_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AssumeRolePolicy",
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"
                    },
                },
            }
        ],
    }

    try:
        iam_role = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(assume_role_policy),
        )
        logger.info(f"Created IAM role: {role_name}")
        logger.info(f"Waiting {IAM_ROLE_CREATION_WAIT_SECONDS}s for role propagation...")
        time.sleep(IAM_ROLE_CREATION_WAIT_SECONDS)
    except iam_client.exceptions.EntityAlreadyExistsException:
        logger.info(f"IAM role '{role_name}' already exists, reusing it")
        iam_role = iam_client.get_role(RoleName=role_name)

    iam_client.put_role_policy(
        RoleName=role_name,
        PolicyName="AgentCoreGatewayPolicy",
        PolicyDocument=json.dumps(role_policy),
    )
    logger.info(f"Attached policy to role: {role_name}")

    return iam_role


def _setup_cognito(
    region: str,
) -> Tuple[str, str, str, str]:
    """Set up Cognito user pool, resource server, and M2M client.

    Args:
        region: AWS region

    Returns:
        Tuple of (user_pool_id, client_id, client_secret, discovery_url)
    """
    cognito = boto3.client("cognito-idp", region_name=region)

    # Get or create user pool
    user_pool_id = _get_or_create_user_pool(cognito)
    logger.info(f"User pool ID: {user_pool_id}")

    # Get or create resource server
    _get_or_create_resource_server(cognito, user_pool_id)
    logger.info(f"Resource server ID: {RESOURCE_SERVER_ID}")

    # Get or create M2M client
    client_id, client_secret = _get_or_create_m2m_client(cognito, user_pool_id)
    logger.info(f"Client ID: {client_id}")

    discovery_url = (
        f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        f"/.well-known/openid-configuration"
    )
    logger.info(f"Discovery URL: {discovery_url}")

    return user_pool_id, client_id, client_secret, discovery_url


def _get_or_create_user_pool(
    cognito,
) -> str:
    """Get existing or create new Cognito user pool.

    Args:
        cognito: Cognito client

    Returns:
        User pool ID
    """
    response = cognito.list_user_pools(MaxResults=60)
    for pool in response["UserPools"]:
        if pool["Name"] == USER_POOL_NAME:
            logger.info(f"Found existing user pool: {pool['Id']}")
            return pool["Id"]

    logger.info(f"Creating new user pool: {USER_POOL_NAME}")
    created = cognito.create_user_pool(PoolName=USER_POOL_NAME)
    user_pool_id = created["UserPool"]["Id"]

    # Create a domain for the user pool (needed for token endpoint)
    domain_name = user_pool_id.replace("_", "").lower()
    cognito.create_user_pool_domain(
        Domain=domain_name,
        UserPoolId=user_pool_id,
    )
    logger.info(f"Created user pool domain: {domain_name}")

    return user_pool_id


def _get_or_create_resource_server(
    cognito,
    user_pool_id: str,
) -> str:
    """Get existing or create new resource server.

    Args:
        cognito: Cognito client
        user_pool_id: Cognito user pool ID

    Returns:
        Resource server identifier
    """
    try:
        cognito.describe_resource_server(
            UserPoolId=user_pool_id,
            Identifier=RESOURCE_SERVER_ID,
        )
        logger.info(f"Found existing resource server: {RESOURCE_SERVER_ID}")
        return RESOURCE_SERVER_ID
    except cognito.exceptions.ResourceNotFoundException:
        logger.info(f"Creating new resource server: {RESOURCE_SERVER_ID}")
        cognito.create_resource_server(
            UserPoolId=user_pool_id,
            Identifier=RESOURCE_SERVER_ID,
            Name=RESOURCE_SERVER_NAME,
            Scopes=SCOPES,
        )
        return RESOURCE_SERVER_ID


def _get_or_create_m2m_client(
    cognito,
    user_pool_id: str,
) -> Tuple[str, str]:
    """Get existing or create new M2M client for client credentials flow.

    Args:
        cognito: Cognito client
        user_pool_id: Cognito user pool ID

    Returns:
        Tuple of (client_id, client_secret)
    """
    response = cognito.list_user_pool_clients(
        UserPoolId=user_pool_id,
        MaxResults=60,
    )
    for client in response["UserPoolClients"]:
        if client["ClientName"] == CLIENT_NAME:
            describe = cognito.describe_user_pool_client(
                UserPoolId=user_pool_id,
                ClientId=client["ClientId"],
            )
            logger.info(f"Found existing M2M client: {client['ClientId']}")
            return client["ClientId"], describe["UserPoolClient"]["ClientSecret"]

    logger.info(f"Creating new M2M client: {CLIENT_NAME}")
    allowed_scopes = [
        f"{RESOURCE_SERVER_ID}/gateway:read",
        f"{RESOURCE_SERVER_ID}/gateway:write",
    ]
    created = cognito.create_user_pool_client(
        UserPoolId=user_pool_id,
        ClientName=CLIENT_NAME,
        GenerateSecret=True,
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthScopes=allowed_scopes,
        AllowedOAuthFlowsUserPoolClient=True,
        SupportedIdentityProviders=["COGNITO"],
        ExplicitAuthFlows=["ALLOW_REFRESH_TOKEN_AUTH"],
    )
    return created["UserPoolClient"]["ClientId"], created["UserPoolClient"]["ClientSecret"]


def _get_cognito_token(
    user_pool_id: str,
    client_id: str,
    client_secret: str,
    region: str,
) -> str:
    """Get an access token from Cognito using client credentials flow.

    Args:
        user_pool_id: Cognito user pool ID
        client_id: App client ID
        client_secret: App client secret
        region: AWS region

    Returns:
        Access token string
    """
    import requests

    user_pool_id_clean = user_pool_id.replace("_", "")
    url = f"https://{user_pool_id_clean}.auth.{region}.amazoncognito.com/oauth2/token"

    scope_string = f"{RESOURCE_SERVER_ID}/gateway:read {RESOURCE_SERVER_ID}/gateway:write"

    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": scope_string,
    }

    logger.info("Requesting access token from Cognito...")
    response = requests.post(url, headers=headers, data=data)
    response.raise_for_status()

    token = response.json()["access_token"]
    logger.info("Successfully obtained access token")
    return token


def _create_gateway(
    gateway_name: str,
    role_arn: str,
    client_id: str,
    discovery_url: str,
    region: str,
) -> Tuple[str, str]:
    """Create the AgentCore Gateway with Cognito JWT authorizer.

    Args:
        gateway_name: Name for the gateway
        role_arn: IAM role ARN for the gateway
        client_id: Cognito app client ID (for allowed clients)
        discovery_url: Cognito OIDC discovery URL
        region: AWS region

    Returns:
        Tuple of (gateway_id, gateway_url)
    """
    gateway_client = boto3.client("bedrock-agentcore-control", region_name=region)

    auth_config = {
        "customJWTAuthorizer": {
            "allowedClients": [client_id],
            "discoveryUrl": discovery_url,
        }
    }

    logger.info(f"Creating gateway '{gateway_name}' with Cognito JWT authorizer...")
    logger.info(f"Auth config:\n{json.dumps(auth_config, indent=2)}")

    response = gateway_client.create_gateway(
        name=gateway_name,
        roleArn=role_arn,
        protocolType="MCP",
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration=auth_config,
        description="Simple AgentCore Gateway with geolocation MCP tools",
    )

    gateway_id = response["gatewayId"]
    gateway_url = response["gatewayUrl"]

    logger.info(f"Gateway created - ID: {gateway_id}")
    logger.info(f"Gateway URL: {gateway_url}")

    # Wait for gateway to become ACTIVE
    _wait_for_gateway_ready(gateway_client, gateway_id)

    return gateway_id, gateway_url


def _wait_for_gateway_ready(
    gateway_client,
    gateway_id: str,
) -> None:
    """Poll until the gateway is in ACTIVE status.

    Args:
        gateway_client: The bedrock-agentcore-control boto3 client
        gateway_id: Gateway ID to poll
    """
    for attempt in range(1, GATEWAY_MAX_POLL_ATTEMPTS + 1):
        response = gateway_client.get_gateway(gatewayIdentifier=gateway_id)
        status = response.get("status", "UNKNOWN")
        logger.info(f"Gateway poll {attempt}/{GATEWAY_MAX_POLL_ATTEMPTS}: status={status}")

        if status in ("ACTIVE", "READY"):
            logger.info(f"Gateway is {status}")
            return

        if status in ("CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"):
            raise RuntimeError(f"Gateway entered terminal state: {status}")

        time.sleep(GATEWAY_POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"Gateway did not become ready after {GATEWAY_MAX_POLL_ATTEMPTS * GATEWAY_POLL_INTERVAL_SECONDS}s"
    )


def _wait_for_target_ready(
    gateway_client,
    gateway_id: str,
    target_id: str,
) -> None:
    """Poll until the gateway target is in ACTIVE status.

    Args:
        gateway_client: The bedrock-agentcore-control boto3 client
        gateway_id: Gateway ID
        target_id: Target ID to poll
    """
    for attempt in range(1, GATEWAY_MAX_POLL_ATTEMPTS + 1):
        response = gateway_client.get_gateway_target(
            gatewayIdentifier=gateway_id,
            targetId=target_id,
        )
        status = response.get("status", "UNKNOWN")
        logger.info(f"Target poll {attempt}/{GATEWAY_MAX_POLL_ATTEMPTS}: status={status}")

        if status in ("ACTIVE", "READY"):
            logger.info(f"Gateway target is {status}")
            return

        if status in ("CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED", "FAILED"):
            status_reason = response.get("statusReason", "unknown")
            raise RuntimeError(
                f"Gateway target entered terminal state: {status}, reason: {status_reason}"
            )

        time.sleep(GATEWAY_POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"Gateway target did not become ready after {GATEWAY_MAX_POLL_ATTEMPTS * GATEWAY_POLL_INTERVAL_SECONDS}s"
    )


def _upload_openapi_spec_to_s3(
    gateway_name: str,
    region: str,
) -> str:
    """Upload the OpenAPI spec file to S3.

    Args:
        gateway_name: Gateway name (used in bucket/key naming)
        region: AWS region

    Returns:
        S3 URI of the uploaded spec
    """
    s3_client = boto3.client("s3", region_name=region)
    account_id = _get_account_id()
    bucket_name = f"agentcore-gateway-{account_id}-{region}"
    object_key = f"{gateway_name}/geolocation_openapi.json"

    # Create bucket if it does not exist
    try:
        s3_client.head_bucket(Bucket=bucket_name)
        logger.info(f"S3 bucket already exists: {bucket_name}")
    except ClientError:
        logger.info(f"Creating S3 bucket: {bucket_name}")
        if region == "us-east-1":
            s3_client.create_bucket(Bucket=bucket_name)
        else:
            s3_client.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": region},
            )

    # Read and upload the OpenAPI spec
    script_dir = os.path.dirname(os.path.abspath(__file__))
    spec_path = os.path.join(script_dir, OPENAPI_SPEC_FILE)

    with open(spec_path, "r") as f:
        spec_data = f.read()

    s3_client.put_object(
        Bucket=bucket_name,
        Key=object_key,
        Body=spec_data,
        ContentType="application/json",
    )

    s3_uri = f"s3://{bucket_name}/{object_key}"
    logger.info(f"Uploaded OpenAPI spec to: {s3_uri}")
    return s3_uri


def _create_api_key_credential_provider(
    gateway_name: str,
    region: str,
) -> str:
    """Create an API key credential provider for the gateway target.

    The ip-api.com API is free and does not require an API key,
    but OpenAPI targets in AgentCore Gateway require an API_KEY
    credential provider. A placeholder key is used.

    Args:
        gateway_name: Gateway name (used in provider naming)
        region: AWS region

    Returns:
        Credential provider ARN
    """
    gateway_client = boto3.client("bedrock-agentcore-control", region_name=region)
    provider_name = f"{gateway_name}-api-key"

    # Check if provider already exists
    try:
        response = gateway_client.list_api_key_credential_providers()
        for provider in response.get("credentialProviders", []):
            if provider.get("name") == provider_name:
                arn = provider["credentialProviderArn"]
                logger.info(f"Found existing API key credential provider: {arn}")
                return arn
    except Exception as e:
        logger.debug(f"Could not list credential providers: {e}")

    logger.info(f"Creating API key credential provider: {provider_name}")
    response = gateway_client.create_api_key_credential_provider(
        name=provider_name,
        apiKey="placeholder-not-used",
    )
    arn = response["credentialProviderArn"]
    logger.info(f"Created API key credential provider: {arn}")
    return arn


def _create_gateway_target(
    gateway_id: str,
    gateway_name: str,
    openapi_s3_uri: str,
    credential_provider_arn: str,
    region: str,
) -> str:
    """Create a gateway target pointing to the geolocation OpenAPI spec.

    Args:
        gateway_id: AgentCore Gateway ID
        gateway_name: Gateway name (used in target naming)
        openapi_s3_uri: S3 URI of the OpenAPI spec
        credential_provider_arn: ARN of the API key credential provider
        region: AWS region

    Returns:
        Target ID
    """
    gateway_client = boto3.client("bedrock-agentcore-control", region_name=region)

    # Target name becomes part of MCP tool name: {targetName}___{operationId}
    # Bedrock tool names must match [a-zA-Z][a-zA-Z0-9_]* (no hyphens, max 64 chars)
    target_name = "geolocation"

    target_config = {
        "mcp": {
            "openApiSchema": {
                "s3": {
                    "uri": openapi_s3_uri,
                }
            }
        }
    }

    credential_config = [
        {
            "credentialProviderType": "API_KEY",
            "credentialProvider": {
                "apiKeyCredentialProvider": {
                    "providerArn": credential_provider_arn,
                    "credentialParameterName": "api_key",
                    "credentialLocation": "QUERY_PARAMETER",
                }
            },
        }
    ]

    logger.info(f"Creating gateway target '{target_name}'...")
    logger.info(f"Target config:\n{json.dumps(target_config, indent=2)}")

    response = gateway_client.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=target_name,
        description="IP geolocation API (ip-api.com) - free, no auth required",
        targetConfiguration=target_config,
        credentialProviderConfigurations=credential_config,
    )

    target_id = response["targetId"]
    logger.info(f"Gateway target created - ID: {target_id}")

    # Wait for target to become ACTIVE
    _wait_for_target_ready(gateway_client, gateway_id, target_id)

    return target_id


def _invoke_mcp_agent(
    gateway_url: str,
    token: str,
    prompt: str,
) -> str:
    """Create a Strands agent with MCP tools from the gateway and invoke it.

    Args:
        gateway_url: The AgentCore Gateway URL
        token: Bearer token for authentication
        prompt: User prompt to send

    Returns:
        Agent response text
    """
    from mcp.client.streamable_http import streamablehttp_client
    from strands import Agent
    from strands.models import BedrockModel
    from strands.tools.mcp.mcp_client import MCPClient

    def _create_transport():
        return streamablehttp_client(
            gateway_url,
            headers={"Authorization": f"Bearer {token}"},
        )

    mcp_client = MCPClient(_create_transport)

    model = BedrockModel(
        model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
        temperature=0.7,
    )

    logger.info(f"Connecting to MCP server at: {gateway_url}")
    logger.info(f"Sending prompt: {prompt}")

    start_time = time.time()

    with mcp_client:
        tools = mcp_client.list_tools_sync()
        tool_names = [getattr(t, 'tool_name', getattr(t, 'name', str(t))) for t in tools]
        logger.info(f"Available MCP tools: {tool_names}")

        agent = Agent(model=model, tools=tools)
        response = agent(prompt)
        result = response.message["content"][0]["text"]

    elapsed = time.time() - start_time
    logger.info(f"Agent responded in {elapsed:.1f} seconds")
    logger.info(f"Response: {result}")

    return result


def _delete_all_resources(
    gateway_name: str,
    region: str,
) -> None:
    """Delete gateway, cognito resources, IAM role, and S3 objects.

    Args:
        gateway_name: Gateway name
        region: AWS region
    """
    gateway_client = boto3.client("bedrock-agentcore-control", region_name=region)
    cognito = boto3.client("cognito-idp", region_name=region)
    iam_client = boto3.client("iam")

    # Delete gateway targets and gateway
    try:
        gateways = gateway_client.list_gateways()
        for gw in gateways.get("items", []):
            if gw.get("name") == gateway_name:
                gw_id = gw["gatewayId"]
                logger.info(f"Deleting gateway targets for: {gw_id}")

                targets = gateway_client.list_gateway_targets(gatewayIdentifier=gw_id)
                for target in targets.get("items", []):
                    gateway_client.delete_gateway_target(
                        gatewayIdentifier=gw_id,
                        targetId=target["targetId"],
                    )
                    logger.info(f"Deleted target: {target['targetId']}")
                    time.sleep(5)

                # Wait for targets to fully delete
                logger.info("Waiting for targets to fully delete...")
                time.sleep(10)

                gateway_client.delete_gateway(gatewayIdentifier=gw_id)
                logger.info(f"Deleted gateway: {gw_id}")
    except Exception as e:
        logger.warning(f"Error deleting gateway: {e}")

    # Delete API key credential provider
    provider_name = f"{gateway_name}-api-key"
    try:
        response = gateway_client.list_api_key_credential_providers()
        for provider in response.get("credentialProviders", []):
            if provider.get("name") == provider_name:
                gateway_client.delete_api_key_credential_provider(
                    name=provider_name,
                )
                logger.info(f"Deleted API key credential provider: {provider_name}")
    except Exception as e:
        logger.warning(f"Error deleting credential provider: {e}")

    # Delete Cognito resources
    try:
        response = cognito.list_user_pools(MaxResults=60)
        for pool in response["UserPools"]:
            if pool["Name"] == USER_POOL_NAME:
                pool_id = pool["Id"]
                # Delete domain first
                pool_desc = cognito.describe_user_pool(UserPoolId=pool_id)
                domain = pool_desc.get("UserPool", {}).get("Domain")
                if domain:
                    cognito.delete_user_pool_domain(
                        Domain=domain,
                        UserPoolId=pool_id,
                    )
                    logger.info(f"Deleted Cognito domain: {domain}")
                cognito.delete_user_pool(UserPoolId=pool_id)
                logger.info(f"Deleted Cognito user pool: {pool_id}")
    except Exception as e:
        logger.warning(f"Error deleting Cognito resources: {e}")

    # Delete IAM role
    role_name = f"agentcore-{gateway_name}-role"
    try:
        policies = iam_client.list_role_policies(RoleName=role_name)
        for policy_name in policies["PolicyNames"]:
            iam_client.delete_role_policy(
                RoleName=role_name,
                PolicyName=policy_name,
            )
        iam_client.delete_role(RoleName=role_name)
        logger.info(f"Deleted IAM role: {role_name}")
    except Exception as e:
        logger.warning(f"Error deleting IAM role: {e}")

    logger.info("Cleanup complete")


def _save_deployment_info(
    gateway_name: str,
    gateway_id: str,
    gateway_url: str,
    user_pool_id: str,
    client_id: str,
) -> None:
    """Save deployment info to a local file for later use.

    Args:
        gateway_name: Gateway name
        gateway_id: Gateway ID
        gateway_url: Gateway URL
        user_pool_id: Cognito user pool ID
        client_id: Cognito app client ID
    """
    info = {
        "gateway_name": gateway_name,
        "gateway_id": gateway_id,
        "gateway_url": gateway_url,
        "user_pool_id": user_pool_id,
        "client_id": client_id,
    }
    script_dir = os.path.dirname(os.path.abspath(__file__))
    info_path = os.path.join(script_dir, ".deployment_info.json")

    with open(info_path, "w") as f:
        json.dump(info, f, indent=2)

    logger.info(f"Deployment info saved to: {info_path}")


def _generate_roo_config(
    gateway_url: str,
    token: str,
) -> None:
    """Generate .roo.json with MCP endpoint and bearer token for Roo Code.

    This file can be copy-pasted into Roo Code's MCP settings.

    Args:
        gateway_url: The AgentCore Gateway MCP URL
        token: Bearer token for authentication
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Save token to .token file
    token_path = os.path.join(script_dir, ".token")
    with open(token_path, "w") as f:
        f.write(token)
    logger.info(f"Token saved to: {token_path}")

    # Generate .roo.json
    roo_config = {
        "mcpServers": {
            "agentcore-geo-mcp": {
                "type": "streamable-http",
                "url": gateway_url,
                "disabled": False,
                "headers": {
                    "Authorization": f"Bearer {token}",
                },
            }
        }
    }

    roo_path = os.path.join(script_dir, ".roo.json")
    with open(roo_path, "w") as f:
        json.dump(roo_config, f, indent=2)

    logger.info(f"Roo Code MCP config saved to: {roo_path}")
    logger.info("Copy the contents of .roo.json into Roo Code MCP settings")


def _load_deployment_info() -> dict:
    """Load previously saved deployment info.

    Returns:
        Deployment info dict
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    info_path = os.path.join(script_dir, ".deployment_info.json")

    with open(info_path, "r") as f:
        return json.load(f)


def _parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Deploy a geolocation API as an MCP server on Amazon Bedrock AgentCore Gateway",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example usage:
    # Full deployment
    uv run python deploy_mcp_server.py --gateway-name geo-mcp --region us-east-1

    # Invoke only (requires prior deployment)
    uv run python deploy_mcp_server.py --gateway-name geo-mcp --region us-east-1 --invoke-only

    # Cleanup all resources
    uv run python deploy_mcp_server.py --gateway-name geo-mcp --region us-east-1 --delete
""",
    )
    parser.add_argument(
        "--gateway-name",
        type=str,
        required=True,
        help="Name for the AgentCore Gateway",
    )
    parser.add_argument(
        "--region",
        type=str,
        default=None,
        help=f"AWS region (default: from AWS config or {DEFAULT_REGION})",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default="What is the geolocation of IP address 8.8.8.8? Tell me the city, country, and timezone.",
        help="Prompt to send to the agent",
    )
    parser.add_argument(
        "--invoke-only",
        action="store_true",
        help="Skip deployment, just invoke using saved deployment info",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete all resources (gateway, cognito, IAM role)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def main():
    """Main entry point - orchestrates MCP server deployment workflow."""
    args = _parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    region = _get_region(args.region)
    logger.info(f"Using region: {region}")

    if args.delete:
        _delete_all_resources(args.gateway_name, region)
        return

    if args.invoke_only:
        info = _load_deployment_info()
        user_pool_id = info["user_pool_id"]
        client_id = info["client_id"]
        gateway_url = info["gateway_url"]

        # Re-fetch client secret from Cognito
        cognito = boto3.client("cognito-idp", region_name=region)
        describe = cognito.describe_user_pool_client(
            UserPoolId=user_pool_id,
            ClientId=client_id,
        )
        client_secret = describe["UserPoolClient"]["ClientSecret"]

        token = _get_cognito_token(user_pool_id, client_id, client_secret, region)
        _generate_roo_config(gateway_url, token)
        _invoke_mcp_agent(gateway_url, token, args.prompt)
        return

    # Full deployment flow
    # Step 1: Create IAM role
    iam_role = _create_gateway_iam_role(args.gateway_name, region)
    role_arn = iam_role["Role"]["Arn"]
    logger.info(f"IAM role ARN: {role_arn}")

    # Step 2: Setup Cognito
    user_pool_id, client_id, client_secret, discovery_url = _setup_cognito(region)

    # Step 3: Create gateway
    gateway_id, gateway_url = _create_gateway(
        args.gateway_name,
        role_arn,
        client_id,
        discovery_url,
        region,
    )

    # Step 4: Upload OpenAPI spec to S3
    openapi_s3_uri = _upload_openapi_spec_to_s3(args.gateway_name, region)

    # Step 5: Create API key credential provider
    credential_provider_arn = _create_api_key_credential_provider(args.gateway_name, region)

    # Step 6: Create gateway target
    _create_gateway_target(gateway_id, args.gateway_name, openapi_s3_uri, credential_provider_arn, region)

    # Step 7: Save deployment info for later use
    _save_deployment_info(gateway_name=args.gateway_name, gateway_id=gateway_id, gateway_url=gateway_url, user_pool_id=user_pool_id, client_id=client_id)

    # Step 8: Get token, generate Roo config, and invoke
    token = _get_cognito_token(user_pool_id, client_id, client_secret, region)
    _generate_roo_config(gateway_url, token)
    _invoke_mcp_agent(gateway_url, token, args.prompt)


if __name__ == "__main__":
    main()
