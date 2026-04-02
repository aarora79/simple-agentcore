"""Deploy a simple Strands A2A agent on Amazon Bedrock AgentCore Runtime.

This script deploys the same calculator agent but using the A2A (Agent-to-Agent)
protocol instead of plain HTTP. This enables:
- Agent card discovery at /.well-known/agent-card.json
- Agent-to-agent communication via A2A protocol
- get_agent_card() API support

Usage:
    # Configure and launch
    uv run python deploy_a2a_agent.py --agent-name my_a2a_agent --region us-east-1

    # Check status
    uv run python deploy_a2a_agent.py --agent-name my_a2a_agent --region us-east-1 --status-only

    # Delete the agent
    uv run python deploy_a2a_agent.py --agent-name my_a2a_agent --region us-east-1 --delete

Note: To invoke the A2A agent or get its agent card, use client.py instead.
"""

import argparse
import json
import logging
import time
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


def _configure_agent(
    agentcore_runtime,
    agent_name: str,
    region: str,
) -> dict:
    """Configure the A2A agent for AgentCore Runtime deployment.

    Args:
        agentcore_runtime: The starter toolkit Runtime instance
        agent_name: Name for the agent runtime
        region: AWS region for deployment

    Returns:
        Configuration response from the starter toolkit
    """
    logger.info(f"Configuring A2A agent '{agent_name}' in region '{region}'")

    response = agentcore_runtime.configure(
        entrypoint="a2a_agent_entrypoint.py",
        auto_create_execution_role=True,
        auto_create_ecr=True,
        requirements_file="requirements.txt",
        region=region,
        agent_name=agent_name,
        protocol="A2A",
    )
    logger.info(f"Configuration response:\n{json.dumps(response, indent=2, default=str)}")
    return response


def _launch_agent(
    agentcore_runtime,
) -> dict:
    """Launch the A2A agent to AgentCore Runtime.

    Args:
        agentcore_runtime: The starter toolkit Runtime instance

    Returns:
        Launch response from the starter toolkit
    """
    logger.info("Launching A2A agent to AgentCore Runtime (this may take several minutes)...")
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
            logger.info("A2A agent runtime is READY")
            return status_response

        if status in end_states:
            logger.error(f"A2A agent runtime entered terminal state: {status}")
            raise RuntimeError(f"A2A agent runtime failed with status: {status}")

        time.sleep(POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"A2A agent runtime did not become ready after {MAX_POLL_ATTEMPTS * POLL_INTERVAL_SECONDS} seconds"
    )


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
    logger.info(f"A2A agent runtime status: {status}")
    return status_response


def _delete_agent(
    agent_name: str,
    region: str,
) -> None:
    """Delete the agent runtime.

    Uses configure() to find the agent ID from the starter toolkit config,
    since list_agent_runtimes may return empty results.

    Args:
        agent_name: Name of the agent runtime to delete
        region: AWS region
    """
    logger.info(f"Deleting A2A agent runtime '{agent_name}' in region '{region}'")

    # Use the starter toolkit to find the agent ID from config
    agentcore_runtime = _create_runtime()
    try:
        agentcore_runtime.configure(
            entrypoint="a2a_agent_entrypoint.py",
            auto_create_execution_role=True,
            auto_create_ecr=True,
            requirements_file="requirements.txt",
            region=region,
            agent_name=agent_name,
            protocol="A2A",
        )
        status_response = agentcore_runtime.status()
        agent_id = status_response.config.agent_id
    except Exception as e:
        logger.error(f"Could not find agent runtime config: {e}")
        return

    logger.info(f"Found agent runtime ID: {agent_id}")

    agentcore_control_client = boto3.client("bedrock-agentcore-control", region_name=region)
    agentcore_control_client.delete_agent_runtime(agentRuntimeId=agent_id)
    logger.info(f"A2A agent runtime '{agent_name}' deleted successfully")


def _parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Deploy a simple Strands A2A agent on Amazon Bedrock AgentCore Runtime",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example usage:
    # Deploy a new A2A agent
    uv run python deploy_a2a_agent.py --agent-name my_a2a_agent --region us-east-1

    # Check status
    uv run python deploy_a2a_agent.py --agent-name my_a2a_agent --region us-east-1 --status-only

    # Delete
    uv run python deploy_a2a_agent.py --agent-name my_a2a_agent --region us-east-1 --delete

    # To invoke or get agent card, use client.py:
    uv run python client.py --agent-arn <ARN>
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
        "--status-only",
        action="store_true",
        help="Only check the status of the agent runtime",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete the agent runtime",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def main():
    """Main entry point - orchestrates A2A agent deployment workflow."""
    args = _parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    region = _get_region(args.region)
    logger.info(f"Using region: {region}")

    if args.delete:
        _delete_agent(args.agent_name, region)
        return

    # Create a single Runtime instance shared across the workflow
    # configure() must always be called first so the toolkit knows which agent to use
    agentcore_runtime = _create_runtime()
    _configure_agent(agentcore_runtime, args.agent_name, region)

    if args.status_only:
        _check_status(agentcore_runtime)
        return

    # Full deployment flow
    launch_result = _launch_agent(agentcore_runtime)
    status_response = _wait_for_ready(agentcore_runtime)

    agent_arn = status_response.config.agent_arn
    logger.info(f"Agent ARN: {agent_arn}")
    logger.info(f"To test the agent, run:")
    logger.info(f"  uv run python client.py --agent-arn '{agent_arn}'")


if __name__ == "__main__":
    main()
