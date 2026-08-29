"""
振る舞いの契約

LLMが何を返そうと、エージェントが常に「必ず行う」または「絶対に行わない」
振る舞いの不変条件を定義・検証する。

キーとなるテストの考え方:
- 安全性の契約: LLMがリクエストしても、ブロック対象のコマンドは決して実行されない
- 終了の保証: エージェントはmax_iterationsで停止し、無限ループを防ぐ
- 履歴の不変条件: ツール結果は実行後、常にメッセージ履歴に現れる
- 堅牢性: エージェントは空のレスポンスや不正なツール入力を適切に処理する
"""

import json
from unittest.mock import MagicMock

from shared_openrouter.agent import ToolUseAgent
from shared_openrouter.mock_helpers import create_mock_response, make_tool_call


# ---------------------------------------------------------------------------
# 振る舞いの契約テスト
# ---------------------------------------------------------------------------


class TestSafetyContracts:
    """契約: エージェントはブロック対象のコマンドを決して実行してはならない。"""

    def setup_method(self) -> None:
        """各テストごとに、モッククライアントを持つ新しいエージェントを作成する。"""
        self.mock_client = MagicMock()
        self.agent = ToolUseAgent(client=self.mock_client, max_iterations=5)

    def test_agent_never_executes_blocked_commands(self) -> None:
        """LLMがrm -rf /を要求しても、ツールはエラーを返す。"""
        # LLMが危険なコマンドを要求する
        tool_call = make_tool_call("call_1", "run_bash", {"command": "rm -rf /"})
        tool_response = create_mock_response(tool_calls=[tool_call], finish_reason="tool_calls")

        # エラーを見た後、LLMがテキストで応答する
        text_response = create_mock_response(
            content="そのコマンドは実行できません。", finish_reason="stop"
        )

        self.mock_client.chat.send.side_effect = [tool_response, text_response]

        self.agent.send_message("全部削除して")

        # ツール結果にブロックエラーが含まれることを確認する
        tool_result_msg = self.agent.messages[2]
        tool_result_data = json.loads(tool_result_msg["content"])
        assert "error" in tool_result_data
        assert "blocked" in tool_result_data["error"].lower()

    def test_blocked_sudo_command(self) -> None:
        """sudoコマンドがブロックされることを確認する。"""
        tool_call = make_tool_call("call_1", "run_bash", {"command": "sudo apt install foo"})
        tool_response = create_mock_response(tool_calls=[tool_call], finish_reason="tool_calls")
        text_response = create_mock_response(content="sudoは実行できません。", finish_reason="stop")

        self.mock_client.chat.send.side_effect = [tool_response, text_response]

        self.agent.send_message("パッケージをインストールして")

        tool_result_msg = self.agent.messages[2]
        tool_result_data = json.loads(tool_result_msg["content"])
        assert "error" in tool_result_data
        assert "sudo" in tool_result_data["error"]


class TestTerminationContracts:
    """契約: エージェントは必ずmax_iterations以内に終了しなければならない。"""

    def setup_method(self) -> None:
        """テスト用に、低い反復回数の上限を持つエージェントを作成する。"""
        self.mock_client = MagicMock()
        self.agent = ToolUseAgent(client=self.mock_client, max_iterations=3)

    def test_agent_stops_after_max_iterations(self) -> None:
        """LLMがツールを要求し続けても、エージェントはmax_iterationsで停止する。"""
        # LLMは常にツールを要求する——最終的な回答を返さない
        tool_call = make_tool_call("call_n", "calculator", {"operation": "add", "a": 1, "b": 1})
        infinite_response = create_mock_response(tool_calls=[tool_call], finish_reason="tool_calls")
        self.mock_client.chat.send.return_value = infinite_response

        result = self.agent.send_message("永遠に計算を続けて")

        # エージェントは停止し、安全メッセージを返さなければならない
        assert "maximum iterations reached" in result.lower()
        # ちょうどmax_iterations回のAPI呼び出し
        assert self.mock_client.chat.send.call_count == 3


class TestHistoryContracts:
    """契約: ツール結果は常にメッセージ履歴に現れなければならない。"""

    def setup_method(self) -> None:
        """モッククライアントを持つ新しいエージェントを作成する。"""
        self.mock_client = MagicMock()
        self.agent = ToolUseAgent(client=self.mock_client)

    def test_agent_always_includes_tool_results(self) -> None:
        """ツール実行後、結果が会話履歴に含まれていなければならない。"""
        tool_call = make_tool_call(
            "call_1", "calculator", {"operation": "multiply", "a": 3, "b": 9}
        )
        text_response = create_mock_response(content="27", finish_reason="stop")

        self.mock_client.chat.send.side_effect = [
            create_mock_response(tool_calls=[tool_call], finish_reason="tool_calls"),
            text_response,
        ]

        self.agent.send_message("3×9は？")

        # すべてのtoolロールメッセージを探す
        tool_result_messages = [msg for msg in self.agent.messages if msg["role"] == "tool"]
        assert len(tool_result_messages) == 1

        # 結果の内容が有効なJSONであることを確認する
        result_content = json.loads(tool_result_messages[0]["content"])
        assert result_content["result"] == 27

    def test_agent_preserves_conversation_history(self) -> None:
        """エージェントループ全体でメッセージが正しく蓄積されなければならない。"""
        self.mock_client.chat.send.return_value = create_mock_response(
            content="こんにちは！", finish_reason="stop"
        )

        self.agent.send_message("こんにちは")

        # シンプルなやり取りの後: ユーザーメッセージ + アシスタントの応答
        assert len(self.agent.messages) == 2
        assert self.agent.messages[0]["role"] == "user"
        assert self.agent.messages[0]["content"] == "こんにちは"
        assert self.agent.messages[1]["role"] == "assistant"


class TestRobustnessContracts:
    """契約: エージェントはエッジケースを適切に処理しなければならない。"""

    def setup_method(self) -> None:
        """モッククライアントを持つ新しいエージェントを作成する。"""
        self.mock_client = MagicMock()
        self.agent = ToolUseAgent(client=self.mock_client)

    def test_agent_handles_empty_response(self) -> None:
        """LLMからの空のコンテンツは、クラッシュせず空文字列を返すべきである。"""
        response = create_mock_response(content=None, finish_reason="stop")
        self.mock_client.chat.send.return_value = response

        result = self.agent.send_message("何も言わないで")

        assert result == ""

    def test_agent_handles_malformed_tool_input(self) -> None:
        """LLMが誤った引数を送っても、ツールのエラーが適切に捕捉される。"""
        # LLMが誤ったキーで電卓を呼び出す
        tool_call = make_tool_call("call_bad", "calculator", {"wrong_key": "not_a_number"})
        tool_response = create_mock_response(tool_calls=[tool_call], finish_reason="tool_calls")

        text_response = create_mock_response(
            content="申し訳ありませんが、うまくいきませんでした。", finish_reason="stop"
        )

        self.mock_client.chat.send.side_effect = [tool_response, text_response]

        self.agent.send_message("何か間違ったことをして")

        # エージェントはクラッシュしてはならない——ツール結果にエラーを捕捉する
        tool_result_msg = self.agent.messages[2]
        tool_result_data = json.loads(tool_result_msg["content"])
        assert "error" in tool_result_data

    def test_tool_results_format_is_consistent(self) -> None:
        """すべてのツール結果は同じ構造（role, tool_call_id, content）を持たなければならない。"""
        # 2つのツールを連続して実行する
        tool_call_1 = make_tool_call("call_1", "calculator", {"operation": "add", "a": 1, "b": 2})
        tool_call_2 = make_tool_call(
            "call_2", "calculator", {"operation": "multiply", "a": 3, "b": 4}
        )
        tool_response = create_mock_response(
            tool_calls=[tool_call_1, tool_call_2], finish_reason="tool_calls"
        )

        text_response = create_mock_response(content="完了しました", finish_reason="stop")

        self.mock_client.chat.send.side_effect = [tool_response, text_response]

        self.agent.send_message("2つ計算して")

        # tool結果メッセージ（ツールごとに1つずつ）を探す
        tool_result_messages = [msg for msg in self.agent.messages if msg["role"] == "tool"]
        assert len(tool_result_messages) == 2

        # 各ツール結果が必要なキーを持つことを確認する
        for msg in tool_result_messages:
            assert msg["role"] == "tool"
            assert "tool_call_id" in msg
            assert "content" in msg
            # contentは有効なJSONでなければならない
            parsed = json.loads(msg["content"])
            assert isinstance(parsed, dict)
