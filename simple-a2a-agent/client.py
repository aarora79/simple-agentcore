"""A2A Client with IAM Authentication for AgentCore Runtime.

This client connects to an A2A agent deployed on AgentCore Runtime
using AWS IAM (SigV4) authentication.

By default, reads the agent ARN from .bedrock_agentcore.yaml (created after deployment).
You can override with --agent-arn if needed.

Usage:
    # Send a message (reads ARN from .bedrock_agentcore.yaml)
    uv run python client.py

    # With a custom prompt
    uv run python client.py --prompt "What is 42 * 17 + 99?"

    # Get just the agent card (pretty printed and saved to agent_card.json)
    uv run python client.py --agent-card-only

    # Override agent ARN
    uv run python client.py --agent-arn <ARN>
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import quote
from uuid import uuid4

import boto3
import httpx
import yaml
from a2a.client import (
    A2ACardResolver,
    ClientConfig,
    ClientFactory,
)
from a2a.types import (
    Message,
    Part,
    Role,
    TextPart,
)
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 300
CONFIG_FILE = ".bedrock_agentcore.yaml"
AGENT_CARD_FILE = "agent_card.json"


class _SigV4HTTPXAuth(httpx.Auth):
    """HTTPX Auth class that signs requests with AWS SigV4."""

    def __init__(
        self,
        credentials,
        service: str,
        region: str,
    ):
        self.credentials = credentials
        self.service = service
        self.region = region
        self.signer = SigV4Auth(credentials, service, region)

    def auth_flow(
        self,
        request: httpx.Request,
    ):
        """Sign the request with SigV4 and add signature to headers."""
        headers = dict(request.headers)
        headers.pop("connection", None)

        aws_request = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers=headers,
        )

        self.signer.add_auth(aws_request)
        request.headers.update(dict(aws_request.headers))

        yield request


def _read_agent_arn_from_config() -> Optional[str]:
    """Read the agent ARN from the .bedrock_agentcore.yaml config file.

    Returns:
        The agent ARN, or None if not found
    """
    config_path = Path(CONFIG_FILE)
    if not config_path.exists():
        return None

    with open(config_path) as f:
        config = yaml.safe_load(f)

    default_agent = config.get("default_agent")
    if not default_agent:
        return None

    agents = config.get("agents", {})
    agent_config = agents.get(default_agent, {})
    agent_arn = agent_config.get("bedrock_agentcore", {}).get("agent_arn")

    if agent_arn:
        logger.info(f"Read agent ARN from {CONFIG_FILE}: {agent_arn}")

    return agent_arn


def _extract_region_from_arn(
    arn: str,
) -> Optional[str]:
    """Extract AWS region from an ARN string.

    Args:
        arn: The ARN string

    Returns:
        The region, or None if not found
    """
    parts = arn.split(":")
    if len(parts) >= 4:
        return parts[3]
    return None


def _create_message(
    text: str,
    role: Role = Role.user,
) -> Message:
    """Create an A2A message.

    Args:
        text: The message text
        role: The message role (default: user)

    Returns:
        A2A Message object
    """
    return Message(
        kind="message",
        role=role,
        parts=[Part(TextPart(kind="text", text=text))],
        message_id=uuid4().hex,
    )


def _format_agent_response(
    event,
) -> str:
    """Extract and format agent response text.

    Args:
        event: The A2A response event

    Returns:
        Formatted response text
    """
    response = event[0] if isinstance(event, tuple) else event

    if (
        hasattr(response, "artifacts")
        and response.artifacts
        and len(response.artifacts) > 0
    ):
        artifact = response.artifacts[0]
        if artifact.parts and len(artifact.parts) > 0:
            return artifact.parts[0].root.text

    if hasattr(response, "history"):
        agent_messages = [
            msg.parts[0].root.text
            for msg in response.history
            if msg.role.value == "agent" and msg.parts
        ]
        return "".join(agent_messages)

    return str(response)


def _save_agent_card(
    card_dict: dict,
) -> None:
    """Save agent card JSON to a local file.

    Args:
        card_dict: The agent card dictionary
    """
    with open(AGENT_CARD_FILE, "w") as f:
        json.dump(card_dict, f, indent=2, default=str)
    logger.info(f"Agent card saved to {AGENT_CARD_FILE}")


async def _get_agent_card(
    agent_arn: str,
    region: str,
) -> dict:
    """Retrieve the A2A agent card.

    Args:
        agent_arn: The agent runtime ARN
        region: AWS region

    Returns:
        The agent card as a dictionary
    """
    boto_session = boto3.Session(region_name=region)
    credentials = boto_session.get_credentials()

    escaped_arn = quote(agent_arn, safe="")
    runtime_url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_arn}/invocations/"

    auth = _SigV4HTTPXAuth(credentials, "bedrock-agentcore", region)
    session_id = str(uuid4())

    headers = {
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
    }

    async with httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT,
        auth=auth,
        headers=headers,
    ) as httpx_client:
        resolver = A2ACardResolver(
            httpx_client=httpx_client,
            base_url=runtime_url,
        )
        agent_card = await resolver.get_agent_card()

        logger.info(f"Agent: {agent_card.name}")
        logger.info(f"Description: {agent_card.description}")

        # Convert to dict for pretty printing and saving
        card_dict = agent_card.model_dump()
        return card_dict


async def _send_message(
    agent_arn: str,
    region: str,
    prompt: str,
) -> str:
    """Send a message to the A2A agent and get the response.

    Args:
        agent_arn: The agent runtime ARN
        region: AWS region
        prompt: The message to send

    Returns:
        The agent response text
    """
    boto_session = boto3.Session(region_name=region)
    credentials = boto_session.get_credentials()

    escaped_arn = quote(agent_arn, safe="")
    runtime_url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_arn}/invocations/"

    auth = _SigV4HTTPXAuth(credentials, "bedrock-agentcore", region)
    session_id = str(uuid4())

    headers = {
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
    }

    logger.info(f"Connecting to agent: {agent_arn}")
    logger.info(f"Runtime URL: {runtime_url}")
    logger.info(f"Session ID: {session_id}")

    async with httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT,
        auth=auth,
        headers=headers,
    ) as httpx_client:
        # Get agent card first
        logger.info("Fetching agent card...")
        resolver = A2ACardResolver(
            httpx_client=httpx_client,
            base_url=runtime_url,
        )
        agent_card = await resolver.get_agent_card()
        logger.info(f"Agent: {agent_card.name}")

        # Create A2A client
        config = ClientConfig(
            httpx_client=httpx_client,
            streaming=False,
        )
        factory = ClientFactory(config)
        client = factory.create(agent_card)

        # Send message
        logger.info(f"Sending message: {prompt}")
        msg = _create_message(text=prompt)

        async for event in client.send_message(msg):
            response_text = _format_agent_response(event)
            logger.info(f"Agent response:\n{response_text}")
            return response_text

    return "No response received"


def _parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="A2A client for testing agents deployed on AgentCore Runtime",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example usage:
    # Send a message (reads ARN from .bedrock_agentcore.yaml)
    uv run python client.py

    # Custom prompt
    uv run python client.py --prompt "What is 100 / 4?"

    # Get agent card (pretty printed and saved to agent_card.json)
    uv run python client.py --agent-card-only

    # Override agent ARN
    uv run python client.py --agent-arn <ARN> --prompt "Hello"
""",
    )
    parser.add_argument(
        "--agent-arn",
        type=str,
        default=None,
        help="The agent runtime ARN (default: read from .bedrock_agentcore.yaml)",
    )
    parser.add_argument(
        "--region",
        type=str,
        default=None,
        help="AWS region (default: extracted from ARN)",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default="What is 42 * 17 + 99?",
        help="Message to send to the agent",
    )
    parser.add_argument(
        "--agent-card-only",
        action="store_true",
        help="Only fetch and display the agent card (saves to agent_card.json)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def _resolve_agent_arn(
    cli_arn: Optional[str] = None,
) -> str:
    """Resolve the agent ARN from CLI arg or config file.

    Args:
        cli_arn: ARN provided via command line (takes precedence)

    Returns:
        The resolved agent ARN

    Raises:
        ValueError: If no ARN found from any source
    """
    if cli_arn:
        return cli_arn

    config_arn = _read_agent_arn_from_config()
    if config_arn:
        return config_arn

    raise ValueError(
        f"No agent ARN provided. Either pass --agent-arn or deploy first "
        f"(creates {CONFIG_FILE} with the ARN)."
    )


def main():
    """Main entry point."""
    args = _parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    agent_arn = _resolve_agent_arn(args.agent_arn)
    region = args.region or _extract_region_from_arn(agent_arn) or "us-east-1"
    logger.info(f"Using region: {region}")
    logger.info(f"Agent ARN: {agent_arn}")

    if args.agent_card_only:
        card_dict = asyncio.run(_get_agent_card(agent_arn, region))
        _save_agent_card(card_dict)
        print(f"\nAgent Card:\n{json.dumps(card_dict, indent=2, default=str)}")
        return

    response = asyncio.run(_send_message(agent_arn, region, args.prompt))
    print(f"\nResponse: {response}")


if __name__ == "__main__":
    main()
