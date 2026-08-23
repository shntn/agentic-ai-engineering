"""
単純な LLM 呼び出し (OpenRouter)

OpenRouter API の基本的な呼び出しを示します。
エージェント ロジックとオーケストレーションの分離を示します。
"""

import os
from openrouter import OpenRouter
from dotenv import find_dotenv, load_dotenv
from common.logging_config import setup_logging

# .env ファイルから環境変数をロードする
load_dotenv(find_dotenv())

# ロギングの構成
logger = setup_logging(__name__)


class LLMClient:
    """
    OpenRouter への基本的な LLM 呼び出しを行う単純なエージェント。

    API インタラクションを含むすべてのエージェント ロジックをカプセル化します。
    """

    def __init__(self, model: str):
        """
        エージェントを初期化
        """
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.system_prompt = (
            "あなたは役に立つ AI アシスタントです。明確かつ簡潔な回答を提供してください。"
        )

    def run(self, prompt: str) -> str:
        """
        指定されたプロンプトでエージェントを実行
        """
        logger.info(f"Calling model: {self.model}")

        # API 呼び出し
        response = self.client.chat.send(
            model=self.model,
            temperature=0.1,
            max_tokens=1024,
            messages=[  # type: ignore[arg-type]
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
        )

        assert response.usage is not None

        # トークンの使用状況をログに記録
        logger.info(
            f"Token usage - Input: {response.usage.prompt_tokens}, "
            f"Output: {response.usage.completion_tokens}, "
            f"Total: {response.usage.total_tokens}"
        )

        # レスポンスを抽出して返す
        result = response.choices[0].message.content
        return str(result)


def main() -> None:
    """
    メインのオーケストレーション機能。

    エージェントをセットアップし、実行フローを調整します。
    """

    # エージェントを初期化
    agent = LLMClient("deepseek/deepseek-v4-flash")

    prompt = "AI エージェントとは何かを 2 ～ 3 文で説明してください。"
    logger.info(f"👤 User: {prompt}")

    # LLM を呼び出す
    response = agent.run(prompt)

    # 結果の表示
    logger.info(f"🤖 Response: {response}")


if __name__ == "__main__":
    main()
