"""Deploy a simple Strands agent on Amazon Bedrock AgentCore Runtime.

This script:
1. Defines a simple agent with a calculator tool using the Strands framework
2. Wraps it with BedrockAgentCoreApp for AgentCore Runtime hosting
3. Configures and launches the agent to AgentCore Runtime using the starter toolkit
4. Waits for the agent to be ready and invokes it

Usage:
    # Configure and launch
    uv run python deploy_agent.py --agent-name my-simple-agent --region us-east-1

    # Invoke an already deployed agent
    uv run python deploy_agent.py --agent-name my-simple-agent --region us-east-1 --invoke-only

    # Check status
    uv run python deploy_agent.py --agent-name my-simple-agent --region us-east-1 --status-only

    # Delete the agent
    uv run python deploy_agent.py --agent-name my-simple-agent --region us-east-1 --delete
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

DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"
DEFAULT_REGION = "us-east-1"
POLL_INTERVAL_SECONDS = 30
MAX_POLL_ATTEMPTS = 40


def _get_account_id() -> str:
    """Get the current AWS account ID."""
    return boto3.client("sts").get_caller_identity()["Account"]


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


def _configure_agent(
    agent_name: str,
    region: str,
) -> dict:
    """Configure the agent for AgentCore Runtime deployment.

    Args:
        agent_name: Name for the agent runtime
        region: AWS region for deployment

    Returns:
        Configuration response from the starter toolkit
    """
    from bedrock_agentcore_starter_toolkit import Runtime

    logger.info(f"Configuring agent '{agent_name}' in region '{region}'")

    agentcore_runtime = Runtime()
    response = agentcore_runtime.configure(
        entrypoint="agent_entrypoint.py",
        auto_create_execution_role=True,
        auto_create_ecr=True,
        requirements_file="requirements.txt",
        region=region,
        agent_name=agent_name,
    )
    logger.info(f"Configuration response:\n{json.dumps(response, indent=2, default=str)}")
    return response


def _launch_agent() -> dict:
    """Launch the agent to AgentCore Runtime.

    Returns:
        Launch response from the starter toolkit
    """
    from bedrock_agentcore_starter_toolkit import Runtime

    logger.info("Launching agent to AgentCore Runtime (this may take several minutes)...")
    agentcore_runtime = Runtime()
    launch_result = agentcore_runtime.launch()
    logger.info(f"Launch result:\n{json.dumps(launch_result, indent=2, default=str)}")
    return launch_result


def _wait_for_ready() -> dict:
    """Poll until the agent runtime is ready.

    Returns:
        Status response when ready
    """
    from bedrock_agentcore_starter_toolkit import Runtime

    agentcore_runtime = Runtime()

    for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
        status_response = agentcore_runtime.status()
        status = status_response.get("status", "UNKNOWN")
        logger.info(f"Poll attempt {attempt}/{MAX_POLL_ATTEMPTS}: status={status}")

        if status == "READY":
            logger.info("Agent runtime is READY")
            return status_response

        if status in ("FAILED", "DELETED"):
            logger.error(f"Agent runtime entered terminal state: {status}")
            raise RuntimeError(f"Agent runtime failed with status: {status}")

        time.sleep(POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"Agent runtime did not become ready after {MAX_POLL_ATTEMPTS * POLL_INTERVAL_SECONDS} seconds"
    )


def _invoke_agent(
    prompt: str,
) -> str:
    """Invoke the deployed agent with a prompt.

    Args:
        prompt: The user prompt to send to the agent

    Returns:
        The agent response text
    """
    from bedrock_agentcore_starter_toolkit import Runtime

    agentcore_runtime = Runtime()
    logger.info(f"Invoking agent with prompt: {prompt}")

    start_time = time.time()
    response = agentcore_runtime.invoke({"prompt": prompt})
    elapsed = time.time() - start_time

    logger.info(f"Agent responded in {elapsed:.1f} seconds")
    logger.info(f"Response:\n{json.dumps(response, indent=2, default=str)}")
    return response


def _check_status() -> dict:
    """Check the current status of the agent runtime.

    Returns:
        Status response
    """
    from bedrock_agentcore_starter_toolkit import Runtime

    agentcore_runtime = Runtime()
    status_response = agentcore_runtime.status()
    logger.info(f"Agent status:\n{json.dumps(status_response, indent=2, default=str)}")
    return status_response


def _delete_agent(
    agent_name: str,
    region: str,
) -> None:
    """Delete the agent runtime.

    Args:
        agent_name: Name of the agent runtime to delete
        region: AWS region
    """
    logger.info(f"Deleting agent runtime '{agent_name}' in region '{region}'")

    agentcore_client = boto3.client("bedrock-agentcore", region_name=region)

    # List runtimes to find the one matching our agent name
    response = agentcore_client.list_agent_runtimes()
    for runtime in response.get("agentRuntimeSummaries", []):
        if runtime.get("agentRuntimeName") == agent_name:
            arn = runtime["agentRuntimeArn"]
            logger.info(f"Found agent runtime ARN: {arn}")
            agentcore_client.delete_agent_runtime(agentRuntimeId=runtime["agentRuntimeId"])
            logger.info(f"Agent runtime '{agent_name}' deleted successfully")
            return

    logger.warning(f"No agent runtime found with name '{agent_name}'")


def _parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Deploy a simple Strands agent on Amazon Bedrock AgentCore Runtime",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example usage:
    # Deploy a new agent
    uv run python deploy_agent.py --agent-name my-agent --region us-east-1

    # Invoke an already deployed agent
    uv run python deploy_agent.py --agent-name my-agent --region us-east-1 --invoke-only

    # Check status
    uv run python deploy_agent.py --agent-name my-agent --region us-east-1 --status-only
""",
    )
    parser.add_argument(
        "--agent-name",
        type=str,
        required=True,
        help="Name for the agent runtime",
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
    """Main entry point - orchestrates agent deployment workflow."""
    args = _parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    region = _get_region(args.region)
    logger.info(f"Using region: {region}")

    if args.status_only:
        _check_status()
        return

    if args.delete:
        _delete_agent(args.agent_name, region)
        return

    if args.invoke_only:
        _invoke_agent(args.prompt)
        return

    # Full deployment flow
    _configure_agent(args.agent_name, region)
    _launch_agent()
    _wait_for_ready()
    _invoke_agent(args.prompt)


if __name__ == "__main__":
    main()
