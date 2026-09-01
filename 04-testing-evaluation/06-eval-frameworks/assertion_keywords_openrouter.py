"""
キーワードカバレッジ採点用のカスタムPromptfooアサーション (OpenRouter)。

Promptfooはtype='python'の各アサーションに対してget_assert()を呼び出す。
pass・score・reasonを含むdictを返す。
"""


def get_assert(output, context):
    """エージェント出力のキーワードカバレッジをチェックする。"""
    metadata = context.get("test", {}).get("metadata", {})
    keywords = metadata.get("keywords", [])

    if not keywords:
        return {"pass": True, "score": 1.0, "reason": "チェック対象のキーワードなし"}

    output_lower = output.lower()
    found = [kw for kw in keywords if kw.lower() in output_lower]
    missing = [kw for kw in keywords if kw.lower() not in output_lower]

    score = len(found) / len(keywords)
    passed = score >= 0.5

    reason = f"{len(found)}/{len(keywords)} 件のキーワードが一致"
    if missing:
        reason += f"（不足: {', '.join(missing)}）"

    return {"pass": passed, "score": score, "reason": reason}
