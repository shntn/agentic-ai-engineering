"""
リサーチアシスタントをラップするカスタムPromptfooプロバイダー (OpenRouter)。

Promptfooはテストケースごとにcall_api()を呼び出す。関数は以下を受け取る:
- prompt: レンダリング済みのプロンプト文字列
- options: YAMLの'config'を含むdict
- context: テストケースの'vars'を含むdict
"""


def call_api(prompt, options, context):
    """Promptfooプロバイダーのエントリーポイント。"""
    from shared_openrouter.knowledge_base import get_agent_response, EVAL_TASKS

    question = context.get("vars", {}).get("question", prompt)

    # 質問文でタスクを照合
    task_id = None
    for task in EVAL_TASKS:
        if task["question"] == question:
            task_id = task["id"]
            break

    if task_id is None:
        return {"output": "一致するタスクが見つかりませんでした。"}

    response = get_agent_response(task_id)

    return {
        "output": response["answer"],
        "tokenUsage": {"total": 100, "prompt": 50, "completion": 50},
    }
