<!-- ---
title: "エージェントのユニットテスト"
description: "LLM応答をモックし、決定的にエージェントの振る舞いをテストする"
icon: "check-square"
--- -->

# エージェントのユニットテスト

API呼び出しをせずにAIエージェントをテストする方法を学びます。LLM応答をモックし、モデルの周辺にあるすべて——ツールの実行、意思決定のルーティング、メッセージの構築、エラーハンドリング——をテストすることで、実際のバグを捕捉する高速で決定的なテストを構築できます。

## 🎯 学べること

- 決定的なテストシナリオを作るためにLLM応答をモックする
- ツール関数を分離してテストする（入力バリデーション、出力フォーマット、エラーハンドリング）
- 振る舞い契約（エージェントが常に行うべきこと・決して行ってはいけないこと）を定義・検証する
- pytestをエージェントテストスイートのテストランナーとして使う
- 依存性注入を使ってテスト可能なエージェントを構築する
- 統合テスト向けにカセットファイルでAPI応答を記録・再生する
- スナップショットテストとトークン予算アサーションでリグレッションを検出する

## 📦 利用可能なサンプル

| スクリプト | ファイル | 説明 |
| ------ | ---- | ----------- |
| Run Tests | [01_run_tests.py](01_run_tests.py) | Rich UIと確認プロンプト付きで完全なテストスイートを実行 |

### テストモジュール

| テスト | ファイル | 説明 |
| ---- | ---- | ----------- |
| Mock LLM | [tests/test_mock_llm.py](tests/test_mock_llm.py) | `client.messages.create()`をモックし、エージェントループのロジックをテスト |
| Tools | [tests/test_tools.py](tests/test_tools.py) | エッジケースを含めてツール関数を分離してテスト |
| Contracts | [tests/test_behavioral_contracts.py](tests/test_behavioral_contracts.py) | エージェントの不変条件を定義・検証 |
| Integration | [tests/test_integration.py](tests/test_integration.py) | API応答の記録/再生、スナップショットリグレッション |

## 🚀 クイックスタート

> **前提条件:** Python 3.11+、APIキー、uv。完全なセットアップ手順は [SETUP.md](../../SETUP.md) を参照してください。

```bash
# Rich UIでテストスイートを実行する（概要を表示し、確認を求める）
uv run --directory 04-testing-evaluation/01-unit-testing-agents python 01_run_tests.py

# pytest経由ですべてのテストを直接実行する（42テスト）
uv run --directory 04-testing-evaluation/01-unit-testing-agents pytest tests/ -v

# 単一のテストモジュールを実行する
uv run --directory 04-testing-evaluation/01-unit-testing-agents pytest tests/test_mock_llm.py -v

# 特定のテストクラスを実行する
uv run --directory 04-testing-evaluation/01-unit-testing-agents pytest tests/test_behavioral_contracts.py::TestSafetyContracts -v
```

または、[Code Runner](https://marketplace.visualstudio.com/items?itemName=formulahendry.code-runner) VS Code拡張機能を使えば、開いているスクリプトをワンクリックで実行できます。

## 🔑 キーコンセプト

### 1. エージェント向けテストピラミッド

従来のテストピラミッドはエージェントにも当てはまりますが、AIならではのひねりがあります——上に行くほど、決定的なチェックではなく実際のLLM呼び出しと統計的なアサーションに依存するようになります:

| 層 | 速度 | コスト | テスト対象 |
|-------|-------|------|---------------|
| **Unit（モック）** ← test_mock_llm, test_tools | 高速 | 無料 | ツールの実行、ルーティング、メッセージの構築、エラーハンドリング |
| **Contracts** ← test_behavioral_contracts | 高速 | 無料 | 安全性、終了条件、履歴の不変条件 |
| **Integration** ← test_integration | 中速 | 無料 | 記録/キャッシュされたLLM応答によるエンドツーエンドのフロー |
| **Eval** | 低速 | $$ | 実際のAPI呼び出しと統計的アサーションによるLLM推論の品質 |

### 2. テスト容易性のための依存性注入

テスト可能なエージェントの鍵は、**LLMクライアントを内部で生成するのではなく注入する**ことです:

```python
class ToolUseAgent:
    """テスト容易性のための依存性注入を備えたツール使用エージェント。"""

    def __init__(self, client: Any, model: str = "claude-sonnet-4-5-20250929") -> None:
        self.client = client  # 注入される——テストではモックにできる
        self.model = model
```

### 3. LLM応答のモック

`unittest.mock`を使って偽のAnthropic応答を作成します:

```python
def create_mock_response(content, stop_reason="end_turn"):
    response = MagicMock()
    response.content = content
    response.stop_reason = stop_reason
    response.usage = Mock(input_tokens=100, output_tokens=50)
    return response

# テスト内で
mock_client = MagicMock()
mock_client.messages.create.return_value = create_mock_response(...)
agent = ToolUseAgent(client=mock_client)
```

### 4. 振る舞い契約

不変条件——エージェントが常に行うべきこと・決して行ってはいけないこと——を定義します:

```python
def test_agent_never_executes_blocked_commands():
    """エージェントはrm、sudo、chmod等を決して実行してはならない。"""
    # ブロックされたコマンドを要求するようLLMをモックする
    # ツールが実行結果ではなくエラーを返すことを検証する

def test_agent_stops_after_max_iterations():
    """エージェントは無限にループせず、N回の反復後に終了しなければならない。"""
    agent = SafeToolUseAgent(client=mock, max_iterations=3)
    # 常にツール使用を要求するようLLMをモックする
    # エージェントがちょうど3回の反復後に停止することを検証する
```

### 5. レスポンスカセット（記録/再生）

壊れやすい`MagicMock`の形を維持する代わりに、実際のAPI応答をJSONファイルに記録し、テストで再生します。カセットシステムは振る舞いの乖離を捕捉します——エージェントが記録より多く呼び出すと、テストは失敗します:

```python
class CassetteClient:
    """カセットファイルから応答を再生する。"""

    def __init__(self, cassette_path: Path) -> None:
        with cassette_path.open() as f:
            self._interactions = json.load(f)
        self._call_index = 0
        self.messages = self  # 実際のクライアントのようにmessages.createを公開する

    def create(self, **kwargs) -> CassetteResponse:
        if self._call_index >= len(self._interactions):
            raise RuntimeError("Cassette exhausted — agent behavior has diverged")
        response = self._interactions[self._call_index]["response"]
        self._call_index += 1
        return CassetteResponse(response)
```

### 6. スナップショットリグレッションテスト

エージェントの出力をゴールデンベースラインと比較してリグレッションを検出します。コード変更によって出力が変わったり、トークン使用量が急増したり、メッセージ履歴の形が変わったりした場合、スナップショットテストがそれを捕捉します:

```python
def test_output_matches_snapshot(cassette_dir):
    golden_snapshot = "12 multiplied by 15 equals 180."
    client = CassetteClient(write_cassette(cassette_dir, "calc", CASSETTE_DATA))
    agent = ToolUseAgent(client=client)
    result = agent.send_message("What is 12 * 15?")
    assert result == golden_snapshot, f"Output drifted: {result!r}"
```

## ⚠️ 重要な考慮事項

- **モックはインテリジェンスではなく骨格をテストする** — モックされたテストはエージェントのハーネスロジックを検証するものであり、LLMの推論を検証するものではありません。品質のためには依然としてeval（チュートリアル02）が必要です。
- **モックは現実的に保つ** — モック応答は実際のAPI形式と一致させましょう。非現実的なモックは誤った安心感につながります。
- **エラー経路をテストする** — API障害、不正な形式のツール出力、タイムアウトはバグが潜む場所です。

## 🔗 リソース

- [Beyond Accuracy: Behavioral Testing of NLP Models with CheckList — Ribeiro et al., 2020](https://arxiv.org/abs/2005.04118) — 不変性・方向性・最小機能テストによる振る舞いテストの基礎となる論文
- [Building Effective Agents — Anthropic](https://www.anthropic.com/research/building-effective-agents) — 何をテストすべきか、どう依存性を注入すべきかを示唆するエージェントパターン（ツール使用、ループ、委任）
- [pytest Documentation](https://docs.pytest.org/) — テストランナー、フィクスチャ、parametrize、mock統合
- [unittest.mock — Python Docs](https://docs.python.org/3/library/unittest.mock.html) — LLMクライアントをモックするためのMagicMock、patch、side_effect

## 👉 次のステップ

ユニットテストを習得したら、次に進みましょう:
- **[Evals](../02-evals/)** — 決定的なアサーションを超え、ゴールデンデータセットとLLM-as-judgeによる統計的評価へ
- **実験** — 自分のエージェント向けに振る舞い契約を追加してみましょう
- **練習** — [Module 01](../../01-foundations/)のエージェント向けにユニットテストを書いてみましょう
