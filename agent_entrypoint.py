"""Agent entrypoint for Amazon Bedrock AgentCore Runtime.

This module defines a simple Strands agent with a calculator tool
and wraps it with BedrockAgentCoreApp for hosting on AgentCore Runtime.

The AgentCore Runtime automatically creates an HTTP server on port 8080 with:
- /invocations endpoint for processing agent requests
- /ping endpoint for health checks
"""

import logging

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import (
    Agent,
    tool,
)
from strands.models import BedrockModel

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

logger = logging.getLogger(__name__)

MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"

# Initialize the AgentCore app globally
app = BedrockAgentCoreApp()


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
            return f"Error: expression contains invalid characters"

        result = eval(expression)  # nosec B307 - input is sanitized above
        logger.info(f"Calculator result: {result}")
        return str(result)
    except Exception as e:
        logger.error(f"Calculator error: {e}")
        return f"Error evaluating expression: {e}"


# Create the Strands agent with the Bedrock model and calculator tool
model = BedrockModel(model_id=MODEL_ID)
agent = Agent(
    model=model,
    tools=[calculator],
    system_prompt=(
        "You are a helpful assistant with access to a calculator tool. "
        "Use the calculator tool when you need to perform mathematical calculations. "
        "Always show your work and explain the calculation."
    ),
)


@app.entrypoint
def handle_invocation(
    payload: dict,
) -> str:
    """Handle an invocation from AgentCore Runtime.

    Args:
        payload: The request payload containing a 'prompt' key

    Returns:
        The agent response text
    """
    user_input = payload.get("prompt", "")
    logger.info(f"Received invocation with prompt: {user_input}")

    response = agent(user_input)
    result = response.message["content"][0]["text"]

    logger.info(f"Agent response: {result}")
    return result


if __name__ == "__main__":
    app.run()
