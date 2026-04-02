"""A2A agent entrypoint for Amazon Bedrock AgentCore Runtime.

This module defines a simple Strands agent with a calculator tool
and wraps it with an A2A server (using strands.multiagent.a2a) for
hosting on AgentCore Runtime with the A2A protocol.

The A2A server exposes:
- /.well-known/agent-card.json for agent discovery
- /ping for health checks
- A2A message endpoints for agent-to-agent communication

AgentCore expects A2A agents on 0.0.0.0:9000/.
"""

import logging
import os

import uvicorn
from fastapi import FastAPI
from strands import (
    Agent,
    tool,
)
from strands.models import BedrockModel
from strands.multiagent.a2a import A2AServer

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

logger = logging.getLogger(__name__)

MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"
AGENT_NAME = "SimpleCalculatorAgent"
AGENT_DESCRIPTION = (
    "A simple calculator agent that can evaluate mathematical expressions. "
    "Send it math questions and it will compute the answer."
)
# Port 9000 is required by AgentCore Runtime for A2A agents
A2A_PORT = 9000
HOST = "127.0.0.1"


@tool
def calculator(
    expression: str,
) -> str:
    """Evaluate a mathematical expression and return the result.

    Args:
        expression: A mathematical expression to evaluate, e.g. '2 + 3 * 4'

    Returns:
        The result of the expression as a string
    """
    logger.info(f"Calculator called with expression: {expression}")
    try:
        # Only allow safe math operations
        allowed_chars = set("0123456789+-*/.() ")
        if not all(c in allowed_chars for c in expression):
            return "Error: expression contains invalid characters"

        result = eval(expression)  # nosec B307 - input is sanitized above
        logger.info(f"Calculator result: {result}")
        return str(result)
    except Exception as e:
        logger.error(f"Calculator error: {e}")
        return f"Error evaluating expression: {e}"


def _create_agent() -> Agent:
    """Create the Strands agent with calculator tool.

    Returns:
        Configured Strands Agent instance
    """
    model = BedrockModel(model_id=MODEL_ID)
    agent = Agent(
        name=AGENT_NAME,
        description=AGENT_DESCRIPTION,
        model=model,
        tools=[calculator],
        system_prompt=(
            "You are a helpful assistant with access to a calculator tool. "
            "Use the calculator tool when you need to perform mathematical calculations. "
            "Always show your work and explain the calculation."
        ),
    )
    return agent


# AgentCore sets AGENTCORE_RUNTIME_URL automatically when deployed
runtime_url = os.environ.get("AGENTCORE_RUNTIME_URL", f"http://{HOST}:{A2A_PORT}/")

logger.info(f"Creating A2A agent: {AGENT_NAME}")
logger.info(f"Runtime URL: {runtime_url}")

# Create agent and A2A server
agent = _create_agent()
a2a_server = A2AServer(
    agent=agent,
    http_url=runtime_url,
    serve_at_root=True,
)

# Create FastAPI app
app = FastAPI(title=AGENT_NAME)


@app.get("/ping")
def ping():
    """Health check endpoint required by AgentCore Runtime."""
    return {"status": "healthy", "agent": AGENT_NAME}


# Mount A2A server at root (handles /.well-known/agent-card.json and A2A messages)
app.mount("/", a2a_server.to_fastapi_app())


if __name__ == "__main__":
    logger.info(f"Starting A2A agent server on {HOST}:{A2A_PORT}")
    uvicorn.run(app, host=HOST, port=A2A_PORT)
