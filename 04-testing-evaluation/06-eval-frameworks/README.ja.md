<!-- ---
title: "Evalフレームワーク"
description: "外部evalフレームワークを統合する: Promptfoo、Braintrust AutoEvals、Langfuse"
icon: "puzzle-piece"
--- -->

# Evalフレームワーク

Anthropicの [Demystifying Evals for AI Agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) で推奨されている**本番向けevalフレームワーク**を探ります。各スクリプトは、同じリサーチアシスタントエージェントを評価する異なるフレームワークのアプローチを示します——YAML駆動の設定から、構築済みスコアラー、トレーシングプラットフォームまで。

## 🎯 学べること

- **PromptfooのYAML設定**を使ってevalスイートを宣言的に定義する
- **Braintrust AutoEvals**の構築済みスコアラー（文字列類似度、factuality、カスタム分類器）を使う
- **Langfuse**のトレーシングとプログラム的スコアリングでエージェントを計装する
- フレームワークのトレードオフを比較する: CLI対SDK、ローカル対クラウド、文字列ベース対LLMベースの採点

## 📦 利用可能なサンプル

| プロバイダー | ファイル | 説明 |
| -------- | ---- | ----------- |
| Promptfoo | [01_promptfoo.py](01_promptfoo.py) | YAML設定 + カスタムPythonプロバイダーとアサーション |
| Braintrust | [02_braintrust_autoevals.py](02_braintrust_autoevals.py) | 構築済みスコアラー: Levenshtein、Factuality、カスタム分類器 |
| Langfuse | [03_langfuse.py](03_langfuse.py) | デコレーターベースのトレーシング + 複数種類のスコアリング |

## 🚀 クイックスタート

> **前提条件:** Python 3.11+とuv。完全なセットアップ手順は [SETUP.md](../../SETUP.md) を参照してください。

各スクリプトは外部依存なしで**シミュレーションモード**で動作します。実際のフレームワークを使うには:

```bash
# コア（すべてのスクリプトはこれらなしで動作する）
uv run --directory 04-testing-evaluation/06-eval-frameworks python 01_promptfoo.py

# フレームワークの依存関係を含めて
uv sync --extra promptfoo    # YAML生成用にpyyamlを追加
uv sync --extra braintrust   # autoevalsのスコアラーを追加
uv sync --extra langfuse     # langfuse SDKを追加
uv sync --extra all           # すべてのフレームワーク
```

または、[Code Runner](https://marketplace.visualstudio.com/items?itemName=formulahendry.code-runner) VS Code拡張機能を使えば、開いているスクリプトをワンクリックで実行できます。

## 🔑 キーコンセプト

### フレームワーク比較

| 観点 | Promptfoo | Braintrust AutoEvals | Langfuse |
|--------|-----------|---------------------|----------|
| **種類** | CLIツール（Node.js） | Python SDK | Python SDK |
| **設定** | YAML駆動 | コード駆動 | コード駆動 |
| **ローカルで動くか** | はい（完全に） | 文字列スコアラー: はい | サーバーが必要（またはセルフホスト） |
| **APIキー** | LLMプロバイダーのみ | LLMスコアラー用に`OPENAI_API_KEY` | `LANGFUSE_*`キー |
| **最適な用途** | 宣言的なevalスイート | 構築済みスコアリング | トレーシング + スコアリング |

### 1. Promptfoo: 宣言的なYAML Evals

プロバイダー・プロンプト・テストケース・アサーションをYAMLで定義します:

```yaml
providers:
  - id: "file://provider_agent.py"    # カスタムPythonプロバイダー
tests:
  - vars:
      question: "What are microservices benefits?"
    assert:
      - type: python                   # カスタムPythonアサーション
        value: "file://assertion_keywords.py"
      - type: contains                 # 組み込みの文字列チェック
        value: "doc_001"
      - type: llm-rubric              # LLM-as-judge
        value: "Response should cover scalability and fault isolation."
```

### 2. Braintrust AutoEvals: 構築済みスコアラー

ゼロから構築せずに、実績あるスコアラーを使います:

```python
from autoevals import Factuality, Levenshtein

# ローカルスコアラー — APIキー不要
lev = Levenshtein()
result = lev.eval(output="hello wrld", expected="hello world")

# LLMスコアラー — OPENAI_API_KEYが必要
fact = Factuality()
result = fact.eval(input="question", output="answer", expected="reference")
```

### 3. Langfuse: トレーシング + スコアリング

デコレーターでコードを計装し、プログラム的にスコアを追加します:

```python
from langfuse import observe, get_client

@observe()  # ネストしたスパンを持つトレースを自動作成する
def my_agent(question: str) -> str:
    return search_and_answer(question)

# トレースを採点する
langfuse = get_client()
langfuse.create_score(
    trace_id=trace_id,
    name="correctness",
    value=0.95,
    data_type="NUMERIC",
)
```

## ⚠️ 重要な考慮事項

- **フレームワークを1つ選んで反復する** — このブログでは、フレームワーク選定よりも高品質なテストケースとグレーダーへの投資を推奨しています
- **多くのチームはツールを組み合わせる** — CI/CDのアサーションにはPromptfoo、本番トレーシングにはLangfuse、素早い採点にはautoevals
- **すべてのスクリプトは外部依存なしで動作する** — シミュレーションモードでパターンを示します。実際に使う準備ができたらフレームワークをインストールしてください
- **LLMベースのスコアラーはコストがかかる** — Factuality/ClosedQAスコアラーはOpenAIを呼び出します。大規模なevalスイートでは予算を考慮しましょう

## 🔗 リソース

- [Promptfoo Python Provider Docs](https://www.promptfoo.dev/docs/providers/python/)
- [Braintrust AutoEvals GitHub](https://github.com/braintrustdata/autoevals)
- [Langfuse Python SDK](https://langfuse.com/docs/sdk/python/decorators)
- [Demystifying Evals — Eval Frameworks Appendix](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)

## 👉 次のステップ

- **適用** — 自分のワークフローに合ったフレームワークを選び、自分のエージェント向けにevalタスクを定義してみましょう
- **組み合わせる** — CIアサーションにPromptfoo + 本番トレーシングにLangfuseを使ってみましょう
- **集大成** — すべてのテクニックを組み合わせたフレームワーク非依存のevalパイプラインについては [Eval Harness](../07-eval-harness/) を参照してください
