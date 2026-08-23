"""Convert Anthropic-style tool definitions to OpenRouter function-calling format."""

from typing import Any


def to_openrouter_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Wrap a single Anthropic `input_schema` tool definition as a function-calling tool."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool["input_schema"],
        },
    }


def to_openrouter_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Wrap a list of Anthropic `input_schema` tool definitions as function-calling tools."""
    return [to_openrouter_tool(tool) for tool in tools]
