<!-- ---
title: "Eval Harness"
description: "集大成: すべてのテストテクニックを組み合わせた完全な評価パイプライン"
icon: "award"
--- -->

# Eval Harness

このモジュールの**5つのテクニックすべてを組み合わせ**、単一の再利用可能な評価ハーネスにまとめる集大成プロジェクトです。ユニットテストのパターン、evals、トレーシング、レッドチーミング、ベンチマーキングが組み合わさり、実際のエージェント向けの完全な品質システムを構成します。

## 🎯 学べること

- 5つのテストテクニックすべてを統一された評価パイプラインに配線する
- 型安全なeval基盤のためにPydanticデータモデルを使う
- 関心事が明確に分離されたモジュール式の`eval_harness`パッケージを構築する
- 品質・安全性・ベンチマークのメトリクスを組み合わせた包括的なレポートを生成する
- **eval駆動開発**をエンドツーエンドで実践する

## 📦 利用可能なサンプル

| スクリプト | ファイル | 説明 |
| ------ | ---- | ----------- |
| Eval Harness | [01_eval_harness.py](01_eval_harness.py) | 完全な評価パイプラインを実行する |

### パッケージモジュール

| モジュール | ファイル | 説明 |
| ------ | ---- | ----------- |
| Models | [eval_harness/models.py](eval_harness/models.py) | Pydanticモデル: EvalTask、EvalTrial、EvalResultなど |
| Agent | [eval_harness/agent.py](eval_harness/agent.py) | リサーチアシスタント（ライブ + シミュレーション） |
| Graders | [eval_harness/graders.py](eval_harness/graders.py) | キーワード・出典引用・複合グレーダー |
| Tracer | [eval_harness/tracer.py](eval_harness/tracer.py) | 軽量なスパンベースのトレースコレクター |
| Red Team | [eval_harness/red_team.py](eval_harness/red_team.py) | 敵対的入力による安全性テスト |
| Benchmark | [eval_harness/benchmark.py](eval_harness/benchmark.py) | パレート分析によるモデル比較 |
| Reporter | [eval_harness/reporter.py](eval_harness/reporter.py) | Richターミナルレポート生成 |

## 🚀 クイックスタート

> **前提条件:** Python 3.11+、APIキー、uv。完全なセットアップ手順は [SETUP.md](../../SETUP.md) を参照してください。

```bash
uv run --directory 04-testing-evaluation/07-eval-harness python 01_eval_harness.py
```

起動すると、インタラクティブなメニューで以下を選択できます:

- **シミュレーション**（デフォルト） — 事前定義済みの応答、API呼び出しなし、即座に結果が出る
- **ライブ** — ツール使用エージェントループによる実際のAnthropic API呼び出し（`ANTHROPIC_API_KEY`が必要）。ライブモードでは、使用するモデルも選択します。評価トライアルと安全性テストはAPIを呼び出しますが、ベンチマークは複数のモデル設定を比較するためシミュレーションのままです。

または、[Code Runner](https://marketplace.visualstudio.com/items?itemName=formulahendry.code-runner) VS Code拡張機能を使えば、開いているスクリプトをワンクリックで実行できます。

## 🏗️ アーキテクチャ

```
タスクを読み込む → エージェントを実行（トレーシング付き） → 採点（マルチグレーダー） → 安全性テスト → ベンチマーク → レポート
```

各ステージは、それぞれ前のチュートリアルに対応しています:

| パイプラインステージ | モジュール | 対応するチュートリアル |
|---------------|--------|---------------|
| テスト可能なエージェント設計 | `agent.py` | [01 - ユニットテスト](../01-unit-testing-agents/) |
| ゴールデンデータセット + 採点 | `graders.py` | [02 - Evals](../02-evals/) |
| 実行トレーシング | `tracer.py` | [03 - トレーシング](../03-tracing-debugging/) |
| 敵対的テスト | `red_team.py` | [04 - レッドチーミング](../04-red-teaming-safety/) |
| モデル比較 | `benchmark.py` | [05 - ベンチマーキング](../05-benchmarking/) |
| 統一されたレポーティング | `reporter.py` | 集大成で新たに追加 |

## 🔑 キーコンセプト

### 1. Pydanticデータモデル

バリデーションを備えた型安全なeval基盤:

```python
class EvalTask(BaseModel):
    id: str
    question: str
    expected_keywords: list[str]
    difficulty: str = "medium"

class EvalResult(BaseModel):
    task_id: str
    trials: list[EvalTrial]
    grader_scores: list[GraderScore]
    pass_rate: float
```

### 2. 複合採点

タスクごとに複数種類のグレーダーを使い、重み付けしたスコアを出す:

```python
class CompositeGrader:
    """設定可能な重みでキーワード + 出典引用のグレーダーを組み合わせる。"""

    def grade(self, trial, task) -> list[GraderScore]:
        keyword_score = self.keyword_grader.grade(trial.answer, task.expected_keywords)
        citation_score = self.citation_grader.grade(trial.answer, task.expected_source_ids)
        # 総合的な合否判定のための加重結合
```

### 3. Evalレポート

このハーネスは統一されたレポートを生成します:

```
╭────── Eval Report: Research Assistant ──────╮
│                                                       │
│  📊 Quality Evals        12/15 tasks passed (80.0%)   │
│  🔒 Safety Score         7/8 attacks blocked (87.5%)  │
│  ⏱️  Avg Latency          1.5s per task               │
│  💰 Total Cost           $0.045                       │
│                                                       │
╰────────────────────────────╯
```

## ⚠️ 重要な考慮事項

- **evalsは生きたインフラである** — 本番コードと同じようにゴールデンデータセットをメンテナンスしましょう
- **安全性は第一級の次元である** — レッドチームの結果は正確性の結果と並んで扱われます
- **コスト追跡は不可欠** — CI/CDで実行する前に、自分のevalスイートにいくらかかるかを把握しましょう
- **リグレッションアラートにはベースラインが必要** — 比較の基準として、既知の良好な実行の結果を保存しましょう

## 🔗 リソース

- [Demystifying Evals for AI Agents — Anthropic](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) — このハーネスが実装しているeval手法: task → trial → 採点 → リグレッション検出
- [EleutherAI Language Model Evaluation Harness](https://github.com/EleutherAI/lm-evaluation-harness) — LLM向けのオープンソースevalフレームワーク。モジュール式ハーネスアーキテクチャの設計インスピレーション
- [Eval-Driven Development](https://evaldriven.org/) — 機能構築の前に成功基準をevalsとして定義するという規律
- [Building Effective Agents — Anthropic](https://www.anthropic.com/research/building-effective-agents) — 依存性注入と明確なインターフェースを通じてエージェントをテスト可能にする設計パターン

## 👉 次のステップ

これが集大成です——テスト&評価モジュールを完了しました！ここから:
- **適用** — 自分のエージェント向けにevalハーネスを構築してみましょう
- **拡張** — [チュートリアル02](../02-evals/)のLLM-as-judge採点を複合グレーダーに追加してみましょう
- **統合** — CI/CDでハーネスを実行し、リグレッションを自動的に検出しましょう
- **フレームワーク** — 本番利用のために[evalフレームワーク](../06-eval-frameworks/)（Promptfoo、Braintrust、Langfuse）を組み込んでみましょう
- **探求** — テスト対象としてより複雑なエージェントを求めるなら、[Module 02: Effective Agents](../../02-effective-agents/)を見てみましょう
