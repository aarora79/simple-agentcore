"""Deploy a simple Strands agent on Amazon Bedrock AgentCore Runtime.

Supports two authentication modes:
- IAM (default): Uses SigV4 credentials, no additional setup needed
- Cognito: Uses JWT bearer tokens from a Cognito User Pool

Usage:
    # Deploy with IAM auth (default)
    uv run python deploy_agent.py --agent-name my_agent --region us-east-1

    # Deploy with Cognito auth
    uv run python deploy_agent.py --agent-name my_agent --region us-east-1 --auth cognito

    # Invoke with Cognito auth (token auto-refreshed from saved config)
    uv run python deploy_agent.py --agent-name my_agent --region us-east-1 --auth cognito --invoke-only

    # Setup Cognito only (without deploying)
    uv run python deploy_agent.py --agent-name my_agent --region us-east-1 --setup-cognito

    # Delete agent and Cognito resources
    uv run python deploy_agent.py --agent-name my_agent --region us-east-1 --delete
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import boto3
from boto3.session import Session

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

logger = logging.getLogger(__name__)

DEFAULT_REGION = "us-east-1"
POLL_INTERVAL_SECONDS = 30
MAX_POLL_ATTEMPTS = 40
COGNITO_CONFIG_FILE = ".cognito_config.json"
COGNITO_POOL_NAME = "AgentCoreSimpleAgentPool"
COGNITO_TEST_USER = os.getenv("COGNITO_TEST_USER", "testuser")
COGNITO_TEST_PASSWORD = os.getenv("COGNITO_TEST_PASSWORD", "TestPassword123!")  # nosec B105
COGNITO_TEMP_PASSWORD = os.getenv("COGNITO_TEMP_PASSWORD", "Temp123!")  # nosec B105


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


def _create_runtime():
    """Create a starter toolkit Runtime instance.

    Returns:
        A Runtime instance from bedrock_agentcore_starter_toolkit
    """
    from bedrock_agentcore_starter_toolkit import Runtime

    return Runtime()


# --- Cognito functions (private, at top per coding standards) ---


def _setup_cognito(
    region: str,
) -> dict:
    """Create a Cognito User Pool, App Client, and test user.

    Args:
        region: AWS region

    Returns:
        Dictionary with pool_id, client_id, discovery_url, bearer_token
    """
    logger.info("Setting up Cognito User Pool...")

    cognito_client = boto3.client("cognito-idp", region_name=region)

    # Create User Pool
    pool_response = cognito_client.create_user_pool(
        PoolName=COGNITO_POOL_NAME,
        Policies={"PasswordPolicy": {"MinimumLength": 8}},
    )
    pool_id = pool_response["UserPool"]["Id"]
    logger.info(f"Created User Pool: {pool_id}")

    # Create App Client
    client_response = cognito_client.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName="SimpleAgentClient",
        GenerateSecret=False,
        ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
    )
    client_id = client_response["UserPoolClient"]["ClientId"]
    logger.info(f"Created App Client: {client_id}")

    # Create test user with temporary password then set permanent via auth challenge
    cognito_client.admin_create_user(
        UserPoolId=pool_id,
        Username=COGNITO_TEST_USER,
        TemporaryPassword=COGNITO_TEMP_PASSWORD,
        MessageAction="SUPPRESS",
    )
    logger.info(f"Created test user: {COGNITO_TEST_USER}")

    # Authenticate with temp password to trigger NEW_PASSWORD_REQUIRED challenge
    temp_auth = cognito_client.initiate_auth(
        ClientId=client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={
            "USERNAME": COGNITO_TEST_USER,
            "PASSWORD": COGNITO_TEMP_PASSWORD,
        },
    )

    # Respond to challenge with permanent password
    challenge_response = cognito_client.respond_to_auth_challenge(
        ClientId=client_id,
        ChallengeName="NEW_PASSWORD_REQUIRED",
        Session=temp_auth["Session"],
        ChallengeResponses={
            "USERNAME": COGNITO_TEST_USER,
            "NEW_PASSWORD": COGNITO_TEST_PASSWORD,
        },
    )
    bearer_token = challenge_response["AuthenticationResult"]["AccessToken"]
    logger.info("Test user password set and authenticated")

    discovery_url = (
        f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"
        f"/.well-known/openid-configuration"
    )

    cognito_config = {
        "pool_id": pool_id,
        "client_id": client_id,
        "discovery_url": discovery_url,
        "region": region,
    }

    # Save config to file
    _save_cognito_config(cognito_config)

    logger.info(f"Discovery URL: {discovery_url}")
    logger.info(f"Bearer token obtained (valid for ~1 hour)")

    cognito_config["bearer_token"] = bearer_token
    return cognito_config


def _get_bearer_token(
    cognito_config: dict,
) -> str:
    """Get a fresh bearer token from Cognito.

    Args:
        cognito_config: Dictionary with client_id and region

    Returns:
        Fresh bearer token string
    """
    region = cognito_config["region"]
    client_id = cognito_config["client_id"]

    cognito_client = boto3.client("cognito-idp", region_name=region)

    auth_response = cognito_client.initiate_auth(
        ClientId=client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={
            "USERNAME": COGNITO_TEST_USER,
            "PASSWORD": COGNITO_TEST_PASSWORD,
        },
    )
    bearer_token = auth_response["AuthenticationResult"]["AccessToken"]
    logger.info("Obtained fresh bearer token from Cognito")
    return bearer_token


def _save_cognito_config(
    cognito_config: dict,
) -> None:
    """Save Cognito config to a local file.

    Args:
        cognito_config: Dictionary to save (without bearer_token)
    """
    config_to_save = {k: v for k, v in cognito_config.items() if k != "bearer_token"}

    with open(COGNITO_CONFIG_FILE, "w") as f:
        json.dump(config_to_save, f, indent=2)
    logger.info(f"Cognito config saved to {COGNITO_CONFIG_FILE}")


def _load_cognito_config() -> Optional[dict]:
    """Load Cognito config from local file.

    Returns:
        Cognito config dictionary, or None if file doesn't exist
    """
    config_path = Path(COGNITO_CONFIG_FILE)
    if not config_path.exists():
        return None

    with open(config_path) as f:
        config = json.load(f)
    logger.info(f"Loaded Cognito config from {COGNITO_CONFIG_FILE}")
    return config


def _delete_cognito(
    region: str,
) -> None:
    """Delete the Cognito User Pool.

    Args:
        region: AWS region
    """
    cognito_config = _load_cognito_config()
    if not cognito_config:
        logger.warning(f"No {COGNITO_CONFIG_FILE} found, skipping Cognito cleanup")
        return

    pool_id = cognito_config.get("pool_id")
    if not pool_id:
        logger.warning("No pool_id in Cognito config, skipping cleanup")
        return

    cognito_client = boto3.client("cognito-idp", region_name=region)

    try:
        cognito_client.delete_user_pool(UserPoolId=pool_id)
        logger.info(f"Deleted Cognito User Pool: {pool_id}")
    except cognito_client.exceptions.ResourceNotFoundException:
        logger.info(f"Cognito User Pool {pool_id} already deleted")
    except Exception as e:
        logger.error(f"Error deleting Cognito User Pool: {e}")

    # Remove config file
    config_path = Path(COGNITO_CONFIG_FILE)
    if config_path.exists():
        config_path.unlink()
        logger.info(f"Removed {COGNITO_CONFIG_FILE}")


# --- Agent deployment functions ---


def _configure_agent(
    agentcore_runtime,
    agent_name: str,
    region: str,
    auth_mode: str = "iam",
    cognito_config: Optional[dict] = None,
) -> dict:
    """Configure the agent for AgentCore Runtime deployment.

    Args:
        agentcore_runtime: The starter toolkit Runtime instance
        agent_name: Name for the agent runtime
        region: AWS region for deployment
        auth_mode: Authentication mode ("iam" or "cognito")
        cognito_config: Cognito config (required when auth_mode is "cognito")

    Returns:
        Configuration response from the starter toolkit
    """
    logger.info(f"Configuring agent '{agent_name}' in region '{region}' (auth: {auth_mode})")

    configure_kwargs = {
        "entrypoint": "agent_entrypoint.py",
        "auto_create_execution_role": True,
        "auto_create_ecr": True,
        "requirements_file": "requirements.txt",
        "region": region,
        "agent_name": agent_name,
    }

    if auth_mode == "cognito":
        if not cognito_config:
            raise ValueError("Cognito config required when auth_mode is 'cognito'")

        configure_kwargs["authorizer_configuration"] = {
            "customJWTAuthorizer": {
                "discoveryUrl": cognito_config["discovery_url"],
                "allowedClients": [cognito_config["client_id"]],
            }
        }
        logger.info(f"Cognito authorizer configured with discovery URL: {cognito_config['discovery_url']}")

    response = agentcore_runtime.configure(**configure_kwargs)
    logger.info(f"Configuration response:\n{json.dumps(response, indent=2, default=str)}")
    return response


def _launch_agent(
    agentcore_runtime,
) -> dict:
    """Launch the agent to AgentCore Runtime.

    Args:
        agentcore_runtime: The starter toolkit Runtime instance

    Returns:
        Launch response from the starter toolkit
    """
    logger.info("Launching agent to AgentCore Runtime (this may take several minutes)...")
    launch_result = agentcore_runtime.launch()
    logger.info(f"Launch result:\n{json.dumps(launch_result, indent=2, default=str)}")
    return launch_result


def _wait_for_ready(
    agentcore_runtime,
) -> dict:
    """Poll until the agent runtime is ready.

    Args:
        agentcore_runtime: The starter toolkit Runtime instance

    Returns:
        Status response when ready
    """
    end_states = ["READY", "CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"]

    for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
        status_response = agentcore_runtime.status()
        status = status_response.endpoint["status"]
        logger.info(f"Poll attempt {attempt}/{MAX_POLL_ATTEMPTS}: status={status}")

        if status == "READY":
            logger.info("Agent runtime is READY")
            return status_response

        if status in end_states:
            logger.error(f"Agent runtime entered terminal state: {status}")
            raise RuntimeError(f"Agent runtime failed with status: {status}")

        time.sleep(POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"Agent runtime did not become ready after {MAX_POLL_ATTEMPTS * POLL_INTERVAL_SECONDS} seconds"
    )


def _invoke_agent(
    agentcore_runtime,
    prompt: str,
    bearer_token: Optional[str] = None,
) -> str:
    """Invoke the deployed agent with a prompt.

    Args:
        agentcore_runtime: The starter toolkit Runtime instance
        prompt: The user prompt to send to the agent
        bearer_token: Optional Cognito bearer token for auth

    Returns:
        The agent response text
    """
    logger.info(f"Invoking agent with prompt: {prompt}")
    if bearer_token:
        logger.info("Using Cognito bearer token for authentication")

    start_time = time.time()
    invoke_kwargs = {"prompt": prompt}

    if bearer_token:
        invoke_response = agentcore_runtime.invoke(invoke_kwargs, bearer_token=bearer_token)
    else:
        invoke_response = agentcore_runtime.invoke(invoke_kwargs)

    elapsed = time.time() - start_time

    response_text = invoke_response["response"][0]
    logger.info(f"Agent responded in {elapsed:.1f} seconds")
    logger.info(f"Response: {response_text}")
    return response_text


def _check_status(
    agentcore_runtime,
) -> dict:
    """Check the current status of the agent runtime.

    Args:
        agentcore_runtime: The starter toolkit Runtime instance

    Returns:
        Status response
    """
    status_response = agentcore_runtime.status()
    status = status_response.endpoint["status"]
    logger.info(f"Agent runtime status: {status}")
    return status_response


def _delete_agent(
    agent_name: str,
    region: str,
) -> None:
    """Delete the agent runtime using configure+status to find the agent ID.

    Args:
        agent_name: Name of the agent runtime to delete
        region: AWS region
    """
    logger.info(f"Deleting agent runtime '{agent_name}' in region '{region}'")

    agentcore_runtime = _create_runtime()
    try:
        agentcore_runtime.configure(
            entrypoint="agent_entrypoint.py",
            auto_create_execution_role=True,
            auto_create_ecr=True,
            requirements_file="requirements.txt",
            region=region,
            agent_name=agent_name,
        )
        status_response = agentcore_runtime.status()
        agent_id = status_response.config.agent_id
    except Exception as e:
        logger.error(f"Could not find agent runtime config: {e}")
        return

    if not agent_id:
        logger.warning(f"No agent runtime found for '{agent_name}'")
        return

    logger.info(f"Found agent runtime ID: {agent_id}")

    agentcore_control_client = boto3.client("bedrock-agentcore-control", region_name=region)
    agentcore_control_client.delete_agent_runtime(agentRuntimeId=agent_id)
    logger.info(f"Agent runtime '{agent_name}' deleted successfully")

    # Also delete Cognito if it exists
    _delete_cognito(region)


def _parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Deploy a simple Strands agent on Amazon Bedrock AgentCore Runtime",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example usage:
    # Deploy with IAM auth (default)
    uv run python deploy_agent.py --agent-name my_agent --region us-east-1

    # Deploy with Cognito auth
    uv run python deploy_agent.py --agent-name my_agent --region us-east-1 --auth cognito

    # Invoke with Cognito auth
    uv run python deploy_agent.py --agent-name my_agent --region us-east-1 --auth cognito --invoke-only

    # Setup Cognito only
    uv run python deploy_agent.py --agent-name my_agent --region us-east-1 --setup-cognito

    # Delete agent and Cognito resources
    uv run python deploy_agent.py --agent-name my_agent --region us-east-1 --delete
""",
    )
    parser.add_argument(
        "--agent-name",
        type=str,
        required=True,
        help="Name for the agent runtime (letters, numbers, underscores only)",
    )
    parser.add_argument(
        "--region",
        type=str,
        default=None,
        help=f"AWS region (default: from AWS config or {DEFAULT_REGION})",
    )
    parser.add_argument(
        "--auth",
        type=str,
        choices=["iam", "cognito"],
        default="iam",
        help="Authentication mode (default: iam)",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default="What is 42 * 17 + 99?",
        help="Prompt to send to the agent after deployment",
    )
    parser.add_argument(
        "--invoke-only",
        action="store_true",
        help="Skip deployment, just invoke an already deployed agent",
    )
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Only check the status of the agent runtime",
    )
    parser.add_argument(
        "--setup-cognito",
        action="store_true",
        help="Only setup Cognito User Pool (saves config to .cognito_config.json)",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete the agent runtime and Cognito resources",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def main():
    """Main entry point - orchestrates agent deployment workflow."""
    args = _parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    region = _get_region(args.region)
    logger.info(f"Using region: {region}")

    if args.delete:
        _delete_agent(args.agent_name, region)
        return

    if args.setup_cognito:
        _setup_cognito(region)
        return

    # Resolve Cognito config if needed
    cognito_config = None
    bearer_token = None
    if args.auth == "cognito":
        cognito_config = _load_cognito_config()
        if not cognito_config:
            logger.info("No existing Cognito config found, creating new User Pool...")
            cognito_config = _setup_cognito(region)
        bearer_token = _get_bearer_token(cognito_config)

    # Create a single Runtime instance shared across the workflow
    agentcore_runtime = _create_runtime()
    _configure_agent(agentcore_runtime, args.agent_name, region, args.auth, cognito_config)

    if args.status_only:
        _check_status(agentcore_runtime)
        return

    if args.invoke_only:
        _invoke_agent(agentcore_runtime, args.prompt, bearer_token)
        return

    # Full deployment flow
    _launch_agent(agentcore_runtime)
    _wait_for_ready(agentcore_runtime)
    _invoke_agent(agentcore_runtime, args.prompt, bearer_token)


if __name__ == "__main__":
    main()
