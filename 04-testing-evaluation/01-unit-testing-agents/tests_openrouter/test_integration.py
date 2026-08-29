"""
レスポンスカセットによる統合テスト

モックの代わりに記録済みのAPIレスポンスを使って、エージェントループ全体を
テストする。カセットファイルから事前に記録されたレスポンスを再生することで、
決定的なテストが得られ、実際のレスポンス解析パスを検証できる——MagicMockの
形状を保守する必要がない。

キーとなるテストの考え方:
- 全体ループのテスト: 記録済みレスポンスで複数ターンのエージェント会話をテストする
- スナップショット回帰: エージェントの出力を正解のベースラインと比較し、ドリフトを検出する
- カセット枯渇: エージェントの振る舞いの乖離を自動的に検知する
"""

import json
from pathlib import Path

import pytest

from shared_openrouter.agent import ToolUseAgent
from tests_openrouter.conftest import (
    CASSETTE_BLOCKED_COMMAND,
    CASSETTE_CALCULATOR,
    CASSETTE_MULTI_TOOL,
    CASSETTE_TEXT_ONLY,
    CassetteClient,
    CassetteResponse,
    serialize_response,
    write_cassette,
)


# ---------------------------------------------------------------------------
# テスト — カセット再生による全体のエージェントループ
# ---------------------------------------------------------------------------


class TestCassetteReplay:
    """事前に記録されたAPIレスポンスを使った統合テスト。"""

    def test_text_only_response(self, cassette_dir: Path) -> None:
        """ツールが不要な場合、エージェントが直接テキストを返す。"""
        path = write_cassette(cassette_dir, "text_only", CASSETTE_TEXT_ONLY)
        client = CassetteClient(path)
        agent = ToolUseAgent(client=client)

        result = agent.send_message("こんにちは")

        assert result == "こんにちは！計算のお手伝いをいたします。"
        assert client.calls_remaining == 0

    def test_single_tool_call(self, cassette_dir: Path) -> None:
        """エージェントが電卓ツールを実行し、最終的な回答を返す。"""
        path = write_cassette(cassette_dir, "calculator", CASSETTE_CALCULATOR)
        client = CassetteClient(path)
        agent = ToolUseAgent(client=client)

        result = agent.send_message("12×15は何ですか？")

        assert "180" in result
        assert client.calls_remaining == 0
        # ツールが実際に実行されたことを確認する——結果がメッセージ履歴にあるはず
        tool_result_msg = agent.messages[2]
        tool_result_data = json.loads(tool_result_msg["content"])
        assert tool_result_data["result"] == 180

    def test_multi_turn_tool_use(self, cassette_dir: Path) -> None:
        """エージェントが複数ターンにまたがる連続したツール呼び出しを処理する。"""
        path = write_cassette(cassette_dir, "multi_tool", CASSETTE_MULTI_TOOL)
        client = CassetteClient(path)
        agent = ToolUseAgent(client=client)

        result = agent.send_message("100 + 200を計算し、その後2倍してください")

        assert "600" in result
        assert client.calls_remaining == 0
        # 6メッセージ: user, assistant(tool), tool(result), assistant(tool), tool(result), assistant
        assert len(agent.messages) == 6

    def test_blocked_command_integration(self, cassette_dir: Path) -> None:
        """全体統合: LLMが危険なコマンドを要求し、エージェントがブロックし、LLMが立て直す。"""
        path = write_cassette(cassette_dir, "blocked", CASSETTE_BLOCKED_COMMAND)
        client = CassetteClient(path)
        agent = ToolUseAgent(client=client)

        result = agent.send_message("一時データを削除して")

        assert "ブロック" in result or "安全" in result
        # ツール結果にブロックエラーが含まれることを確認する
        tool_result_msg = agent.messages[2]
        tool_result_data = json.loads(tool_result_msg["content"])
        assert "error" in tool_result_data
        assert "blocked" in tool_result_data["error"].lower()


class TestCassetteExhaustion:
    """カセットの仕組みがエージェントの振る舞いの乖離を検知することを確認する。"""

    def test_cassette_exhausted_raises_error(self, cassette_dir: Path) -> None:
        """エージェントが記録数より多くAPIを呼び出すと、カセットがエラーを送出する。"""
        # テキストのみのカセット（1件）を使うが、エージェントには2回呼び出させる
        path = write_cassette(cassette_dir, "short", CASSETTE_TEXT_ONLY)
        client = CassetteClient(path)

        # 1回目の呼び出しは成功する
        response = client.send(model="test", max_tokens=100, tools=[], messages=[])
        assert response.choices[0].finish_reason == "stop"

        # 2回目の呼び出しは失敗するはず——カセットが枯渇している
        with pytest.raises(RuntimeError, match="Cassette exhausted"):
            client.send(model="test", max_tokens=100, tools=[], messages=[])


# ---------------------------------------------------------------------------
# テスト — スナップショット回帰テスト
# ---------------------------------------------------------------------------


class TestSnapshotRegression:
    """エージェントの出力を正解のスナップショットと比較し、回帰を検出する。"""

    def test_calculator_output_matches_snapshot(self, cassette_dir: Path) -> None:
        """既知の入力に対するエージェント出力は、記録済みの正解スナップショットと一致しなければならない。"""
        path = write_cassette(cassette_dir, "calculator", CASSETTE_CALCULATOR)

        # 正解スナップショット——"12×15は何ですか？"に対する期待される出力
        golden_snapshot = "12かける15は180です。"

        client = CassetteClient(path)
        agent = ToolUseAgent(client=client)
        result = agent.send_message("12×15は何ですか？")

        assert result == golden_snapshot, (
            f"Output has drifted from snapshot.\n"
            f"  Expected: {golden_snapshot!r}\n"
            f"  Got:      {result!r}"
        )

    def test_message_history_shape_matches_snapshot(self, cassette_dir: Path) -> None:
        """メッセージ履歴の形状は、期待されるパターンと一致しなければならない。"""
        path = write_cassette(cassette_dir, "calculator", CASSETTE_CALCULATOR)
        client = CassetteClient(path)
        agent = ToolUseAgent(client=client)
        agent.send_message("12×15は何ですか？")

        # 期待されるメッセージロールの順序のスナップショット
        expected_roles = ["user", "assistant", "tool", "assistant"]
        actual_roles = [msg["role"] for msg in agent.messages]

        assert actual_roles == expected_roles, (
            f"Message history shape has changed.\n"
            f"  Expected: {expected_roles}\n"
            f"  Got:      {actual_roles}"
        )

    def test_token_usage_within_budget(self, cassette_dir: Path) -> None:
        """合計トークン使用量は、期待される予算内に収まらなければならない。"""
        path = write_cassette(cassette_dir, "multi_tool", CASSETTE_MULTI_TOOL)
        client = CassetteClient(path)
        agent = ToolUseAgent(client=client)
        agent.send_message("100 + 200を計算し、その後2倍してください")

        # 予算のスナップショット——トークン使用量が急増したら何かが変わったということ
        max_input_tokens = 1000
        max_output_tokens = 200

        assert agent.token_tracker.total_input_tokens <= max_input_tokens, (
            f"Input token budget exceeded: "
            f"{agent.token_tracker.total_input_tokens} > {max_input_tokens}"
        )
        assert agent.token_tracker.total_output_tokens <= max_output_tokens, (
            f"Output token budget exceeded: "
            f"{agent.token_tracker.total_output_tokens} > {max_output_tokens}"
        )


# ---------------------------------------------------------------------------
# テスト — シリアライズの往復
# ---------------------------------------------------------------------------


class TestCassetteSerialization:
    """レスポンスのシリアライズ・デシリアライズが無損失であることを確認する。"""

    def test_text_response_round_trip(self) -> None:
        """テキストのみのレスポンスは、シリアライズ→デシリアライズを経てもデータを失わない。"""
        original_data = CASSETTE_TEXT_ONLY[0]["response"]
        response = CassetteResponse(original_data)
        serialized = serialize_response(response)

        assert serialized["finish_reason"] == "stop"
        assert serialized["content"] == "こんにちは！計算のお手伝いをいたします。"
        assert serialized["usage"]["prompt_tokens"] == 120

    def test_tool_use_response_round_trip(self) -> None:
        """tool_use応答は、シリアライズ→デシリアライズを経てもデータを失わない。"""
        original_data = CASSETTE_CALCULATOR[0]["response"]
        response = CassetteResponse(original_data)
        serialized = serialize_response(response)

        assert serialized["finish_reason"] == "tool_calls"
        assert serialized["tool_calls"][0]["name"] == "calculator"
        assert serialized["tool_calls"][0]["arguments"]["operation"] == "multiply"
        assert serialized["tool_calls"][0]["id"] == "call_01ABC"
