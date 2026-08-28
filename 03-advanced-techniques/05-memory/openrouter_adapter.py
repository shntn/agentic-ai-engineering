"""Anthropicスタイルのツール定義をOpenRouterのfunction calling形式に変換する。"""

from typing import Any


def to_openrouter_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """単一のAnthropic `input_schema` ツール定義をfunction calling形式にラップする。"""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool["input_schema"],
        },
    }


def to_openrouter_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """複数のAnthropic `input_schema` ツール定義をfunction calling形式にラップする。"""
    return [to_openrouter_tool(tool) for tool in tools]
