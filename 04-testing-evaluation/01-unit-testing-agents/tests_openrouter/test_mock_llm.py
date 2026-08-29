"""
モックLLMテスト

実際のAPI呼び出しを行わずに、エージェントのツール使用ループをテストする。
unittest.mockを使ってOpenRouterのレスポンスをシミュレートし、エージェントが
tool_callsを正しく解析し、ツールを実行し、結果を送り返し、エラーを処理
することを検証する。
"""

import json
from unittest.mock import MagicMock

import pytest

from shared_openrouter.agent import ToolUseAgent
from shared_openrouter.mock_helpers import create_mock_response, make_tool_call


class TestToolUseAgent:
    """モックLLMレスポンスを使ったToolUseAgentのテスト。"""

    def setup_method(self) -> None:
        """各テストごとに、モッククライアントを持つ新しいエージェントを作成する。"""
        self.mock_client = MagicMock()
        self.agent = ToolUseAgent(client=self.mock_client)

    def test_agent_calls_calculator_tool(self) -> None:
        """LLMがリクエストしたとき、エージェントが電卓を実行することを確認する。"""
        # 1回目のレスポンス: LLMが電卓の使用を要求する
        tool_call = make_tool_call(
            "call_1", "calculator", {"operation": "multiply", "a": 6, "b": 7}
        )
        tool_response = create_mock_response(tool_calls=[tool_call], finish_reason="tool_calls")

        # 2回目のレスポンス: LLMが最終的なテキストを返す
        text_response = create_mock_response(content="答えは42です。", finish_reason="stop")

        self.mock_client.chat.send.side_effect = [tool_response, text_response]

        result = self.agent.send_message("6×7は何ですか？")

        assert result == "答えは42です。"
        assert self.mock_client.chat.send.call_count == 2

        # ツール結果がLLMに送り返されたことを確認する
        tool_result_msg = self.agent.messages[2]  # user -> assistant -> tool
        assert tool_result_msg["role"] == "tool"
        tool_result_data = json.loads(tool_result_msg["content"])
        assert tool_result_data["result"] == 42

    def test_agent_handles_text_response(self) -> None:
        """ツール呼び出しがない場合、エージェントがテキストを直接返すことを確認する。"""
        response = create_mock_response(
            content="こんにちは！何かお手伝いできることはありますか？", finish_reason="stop"
        )
        self.mock_client.chat.send.return_value = response

        result = self.agent.send_message("こんにちは")

        assert result == "こんにちは！何かお手伝いできることはありますか？"
        # API呼び出しは1回のみ——ツールループなし
        assert self.mock_client.chat.send.call_count == 1

    def test_agent_handles_multi_turn_tool_use(self) -> None:
        """エージェントがtool_calls -> 結果 -> テキストとループすることを確認する。"""
        # ターン1: LLMが電卓を要求する
        tool_call = make_tool_call("call_1", "calculator", {"operation": "add", "a": 10, "b": 20})
        # ターン2: LLMが最終的な回答を返す

        self.mock_client.chat.send.side_effect = [
            create_mock_response(tool_calls=[tool_call], finish_reason="tool_calls"),
            create_mock_response(content="10 + 20 = 30", finish_reason="stop"),
        ]

        result = self.agent.send_message("10と20を足してください")

        assert result == "10 + 20 = 30"
        # メッセージ: user, assistant(tool_calls), tool, assistant(text)
        assert len(self.agent.messages) == 4

    def test_agent_sends_tool_results_back(self) -> None:
        """ツール結果がメッセージ履歴に正しい形式で記録されることを確認する。"""
        tool_call = make_tool_call(
            "call_abc", "calculator", {"operation": "divide", "a": 100, "b": 4}
        )

        self.mock_client.chat.send.side_effect = [
            create_mock_response(tool_calls=[tool_call], finish_reason="tool_calls"),
            create_mock_response(content="25", finish_reason="stop"),
        ]

        self.agent.send_message("100を4で割ってください")

        # tool結果メッセージを探す
        tool_result_msg = self.agent.messages[2]
        assert tool_result_msg["role"] == "tool"
        assert tool_result_msg["tool_call_id"] == "call_abc"

    def test_agent_tracks_tokens(self) -> None:
        """API呼び出しをまたいでトークン追跡が累積することを確認する。"""
        response = create_mock_response(
            content="完了しました", finish_reason="stop", prompt_tokens=150, completion_tokens=75
        )
        self.mock_client.chat.send.return_value = response

        self.agent.send_message("こんにちは")

        assert self.agent.token_tracker.total_input_tokens == 150
        assert self.agent.token_tracker.total_output_tokens == 75

    def test_agent_handles_api_error(self) -> None:
        """エージェントがAPIエラーを伝播することを確認する。"""
        self.mock_client.chat.send.side_effect = Exception("API rate limit exceeded")

        with pytest.raises(Exception, match="API rate limit exceeded"):
            self.agent.send_message("こんにちは")
