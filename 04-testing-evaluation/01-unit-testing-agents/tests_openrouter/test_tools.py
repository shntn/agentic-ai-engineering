"""
ツールのテスト

ツール関数を単体でテストする——LLMを一切介さずに、入力バリデーション・
出力形式・エラーハンドリング・エッジケースを検証する。

キーとなるテストの考え方:
- 純粋な関数のユニットテスト: ツールは決定的なので直接テストする
- フィクスチャベースのセットアップ: 一時ファイルや共有状態にpytestフィクスチャを使う
- エッジケースの網羅: ゼロ除算、ファイル未検出、ブロックされたコマンド、タイムアウト
"""

from pathlib import Path

import pytest

from shared_openrouter.tools import BLOCKED_COMMANDS, calculator, execute_tool, read_file, run_bash


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_file(tmp_path: Path) -> Path:
    """テスト用に既知の内容を持つ一時ファイルを作成する。"""
    filepath = tmp_path / "sample.txt"
    filepath.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n", encoding="utf-8")
    return filepath


@pytest.fixture()
def long_file(tmp_path: Path) -> Path:
    """切り詰めテスト用に、行数の多い一時ファイルを作成する。"""
    filepath = tmp_path / "long.txt"
    content = "\n".join(f"line {i}" for i in range(1, 201))
    filepath.write_text(content, encoding="utf-8")
    return filepath


# ---------------------------------------------------------------------------
# 電卓のテスト
# ---------------------------------------------------------------------------


class TestCalculator:
    """電卓ツールのテスト。"""

    def test_calculator_add(self) -> None:
        """加算が正しい結果を返すことを確認する。"""
        result = calculator("add", 3, 5)
        assert result["result"] == 8
        assert result["operation"] == "add"
        assert result["operands"] == [3, 5]

    def test_calculator_subtract(self) -> None:
        """減算が正しい結果を返すことを確認する。"""
        result = calculator("subtract", 10, 4)
        assert result["result"] == 6

    def test_calculator_multiply(self) -> None:
        """乗算が正しい結果を返すことを確認する。"""
        result = calculator("multiply", 7, 8)
        assert result["result"] == 56

    def test_calculator_divide(self) -> None:
        """除算が正しい結果を返すことを確認する。"""
        result = calculator("divide", 20, 4)
        assert result["result"] == 5.0

    def test_calculator_division_by_zero(self) -> None:
        """ゼロ除算が例外ではなくエラー文字列を返すことを確認する。"""
        result = calculator("divide", 10, 0)
        assert result["result"] == "Error: Division by zero"

    def test_calculator_float_precision(self) -> None:
        """電卓が浮動小数点数を扱えることを確認する。"""
        result = calculator("add", 0.1, 0.2)
        assert abs(result["result"] - 0.3) < 1e-9


# ---------------------------------------------------------------------------
# ファイル読み込みのテスト
# ---------------------------------------------------------------------------


class TestReadFile:
    """read_fileツールのテスト。"""

    def test_read_file_success(self, sample_file: Path) -> None:
        """ファイル読み込みが成功し、内容とメタデータを返すことを確認する。"""
        result = read_file(str(sample_file))
        assert "line 1" in result["content"]
        assert result["total_lines"] == 5
        assert result["truncated"] is False

    def test_read_file_not_found(self) -> None:
        """存在しないファイルが例外ではなくエラーdictを返すことを確認する。"""
        result = read_file("/nonexistent/path/file.txt")
        assert "error" in result
        assert "File not found" in result["error"]

    def test_read_file_truncation(self, long_file: Path) -> None:
        """max_linesパラメータが出力を正しく切り詰めることを確認する。"""
        result = read_file(str(long_file), max_lines=3)
        # contentには最初の3行のみが含まれるはず
        assert result["content"].count("\n") <= 3
        assert result["truncated"] is True
        assert result["total_lines"] == 200


# ---------------------------------------------------------------------------
# bashコマンドのテスト
# ---------------------------------------------------------------------------


class TestRunBash:
    """run_bashツールのテスト。"""

    def test_run_bash_success(self) -> None:
        """シンプルなコマンドが実行され、出力を返すことを確認する。"""
        result = run_bash("echo hello")
        assert result["stdout"].strip() == "hello"
        assert result["exit_code"] == 0

    def test_run_bash_blocked_commands(self) -> None:
        """ブロック対象のコマンドがすべて実行前に拒否されることを確認する。"""
        for cmd in BLOCKED_COMMANDS:
            result = run_bash(f"{cmd} something")
            assert "error" in result, f"Command '{cmd}' should be blocked"
            assert "blocked" in result["error"].lower()

    def test_run_bash_rm_rf_blocked(self) -> None:
        """典型的な危険コマンドがブロックされることを確認する。"""
        result = run_bash("rm -rf /")
        assert "error" in result
        assert "blocked" in result["error"].lower()

    def test_run_bash_timeout(self) -> None:
        """長時間実行されるコマンドにタイムアウトが適用されることを確認する。"""
        result = run_bash("sleep 10", timeout=1)
        assert "error" in result
        assert "timed out" in result["error"].lower()

    def test_run_bash_nonexistent_command(self) -> None:
        """不正なコマンドがゼロ以外の終了コードを返すことを確認する。"""
        result = run_bash("nonexistent_command_xyz_123")
        assert result["exit_code"] != 0


# ---------------------------------------------------------------------------
# ツールディスパッチャーのテスト
# ---------------------------------------------------------------------------


class TestExecuteTool:
    """execute_toolディスパッチャーのテスト。"""

    def test_execute_tool_unknown_tool(self) -> None:
        """未知のツール名がエラーを返すことを確認する。"""
        result = execute_tool("nonexistent_tool", {})
        assert "error" in result
        assert "Unknown tool" in result["error"]

    def test_execute_tool_invalid_args(self) -> None:
        """誤った引数がクラッシュではなくエラーを返すことを確認する。"""
        # calculatorは'operation'、'a'、'b'を要求する——誤ったキーを渡す
        result = execute_tool("calculator", {"wrong_key": "value"})
        assert "error" in result
        assert "Invalid arguments" in result["error"]

    def test_execute_tool_dispatches_correctly(self) -> None:
        """ディスパッチャーが正しいツール関数にルーティングすることを確認する。"""
        result = execute_tool("calculator", {"operation": "add", "a": 1, "b": 2})
        assert result["result"] == 3

    def test_execute_tool_read_file_dispatch(self, sample_file: Path) -> None:
        """ディスパッチャーがread_fileを正しくルーティングすることを確認する。"""
        result = execute_tool("read_file", {"path": str(sample_file)})
        assert "content" in result
        assert "line 1" in result["content"]
