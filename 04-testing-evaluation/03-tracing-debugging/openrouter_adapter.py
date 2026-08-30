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


# str.split()による単純な空白区切りは、分かち書きのない日本語ではほとんど機能
# しない。ベースの`shared/`パッケージを変更せずに日本語対応するため、janomeで
# 独自に形態素解析する（06-rag-techniquesと同じ方式）。
_janome_tokenizer = Tokenizer()

# 助詞・助動詞・記号は検索の関連度にほとんど寄与しないため除外する
_EXCLUDED_POS_PREFIXES = ("助詞", "助動詞", "記号")


def tokenize_japanese(
    texts: str | list[str],
    stopwords: str | list[str] = "japanese",
    show_progress: bool = True,
) -> list[list[str]]:
    """janomeで日本語テキストを分かち書きし、`bm25s.tokenize`と同じ入出力形式で返す。

    `bm25s.tokenize`のシグネチャに合わせているが、日本語の分かち書きにはjanomeを
    使うため`stopwords`（除外品詞は関数内で固定）と`show_progress`は使用しない。
    """
    if isinstance(texts, str):
        texts = [texts]

    return [
        [
            token.surface
            for token in _janome_tokenizer.tokenize(text)
            if not token.part_of_speech.startswith(_EXCLUDED_POS_PREFIXES)
        ]
        for text in texts
    ]
