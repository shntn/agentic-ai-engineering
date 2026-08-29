"""OpenRouter対応レッスン向けの補助関数。"""

from typing import Any

from janome.tokenizer import Tokenizer


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


# episodic.pyのキーワード検索は元々`content_lower.split()`（スペース区切り）で
# 単語を取り出しており、単語間にスペースを入れない日本語ではほぼ機能しない。
# janomeで分かち書きすることで対応する。
_janome_tokenizer = Tokenizer()

# 助詞・助動詞・記号は検索の関連度にほとんど寄与しないため除外する（日本語版の
# ストップワード相当）
_EXCLUDED_POS_PREFIXES = ("助詞", "助動詞", "記号")


def tokenize_japanese(text: str) -> list[str]:
    """janomeで日本語テキストを分かち書きし、単語（表層形）のリストを返す。"""
    return [
        token.surface
        for token in _janome_tokenizer.tokenize(text)
        if not token.part_of_speech.startswith(_EXCLUDED_POS_PREFIXES)
    ]
