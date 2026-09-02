<!-- ---
title: "ベンチマーキング"
description: "モデル・プロンプト・アーキテクチャの体系的な直接比較"
icon: "bar-chart-2"
--- -->

# ベンチマーキング

Claude Sonnet対GPT-4o-mini、あるいは2つのプロンプト戦略のどちらかを選ぶ必要があるとき、ベンチマーキングは雰囲気ではなく**データ**を与えてくれます。正確性・レイテンシ・コスト・信頼性という重要な次元にわたる体系的な直接比較です。

## 🎯 学べること

- 正確性・レイテンシ・トークン使用量・コストでモデルを比較する
- プロンプト戦略を評価する: zero-shot、few-shot、chain-of-thought
- 設定マトリクス（モデル × プロンプト）を構築し、制御された実験を実行する
- **パレート最適**な設定を見つける（与えられたコスト予算で最良の正確性）
- データに基づいたモデル選定の意思決定を行う

## 📦 利用可能なサンプル

| スクリプト | ファイル | 説明 |
| ------ | ---- | ----------- |
| Model Comparison | [01_model_comparison.py](01_model_comparison.py) | Claude Sonnet、Haiku、GPT-4.1 miniにわたる同一タスク |
| Prompt Comparison | [02_prompt_comparison.py](02_prompt_comparison.py) | Zero-shot対Few-shot対Chain-of-thought |
| Benchmark Suite | [03_benchmark_suite.py](03_benchmark_suite.py) | 完全なマトリクス、パレート分析、レポート生成 |

## 🚀 クイックスタート

> **前提条件:** Python 3.11+、APIキー、uv。完全なセットアップ手順は [SETUP.md](../../SETUP.md) を参照してください。

```bash
uv run --directory 04-testing-evaluation/05-benchmarking python 01_model_comparison.py

# 例
uv run --directory 04-testing-evaluation/05-benchmarking python 03_benchmark_suite.py
```

すべてのスクリプトには**シミュレートされた結果**が含まれており、APIキーなしで動作します。`ANTHROPIC_API_KEY`（および任意で`OPENAI_API_KEY`）が設定されている場合、ライブモードが有効になります。

または、[Code Runner](https://marketplace.visualstudio.com/items?itemName=formulahendry.code-runner) VS Code拡張機能を使えば、開いているスクリプトをワンクリックで実行できます。

## 🔑 キーコンセプト

### 1. 制御された実験

1つの変数を変え、他は一定に保ちます:

| ベンチマークの種類 | 変数 | 一定に保つもの |
|---------------|----------|----------|
| モデル比較 | モデル | 同じタスク、同じプロンプト、同じグレーダー |
| プロンプト比較 | プロンプト戦略 | 同じモデル、同じタスク、同じグレーダー |
| 完全なマトリクス | モデル × プロンプト | 同じタスク、同じグレーダー |

### 2. 多次元評価

正確性だけでは十分ではありません:

```python
@dataclass
class BenchmarkResult:
    keyword_score: float   # 品質: 回答に期待される情報が含まれていたか？
    latency_ms: float      # 速度: 応答はどれだけ速かったか？
    input_tokens: int      # 効率: 消費されたトークン数は？
    cost_usd: float        # コスト: この実行にはいくらかかったか？
    tool_calls: int        # 振る舞い: 何回のツール呼び出しが必要だったか？
```

### 3. パレート最適性

ある設定が、すべての次元で他の設定より優れているわけではない場合、その設定は**パレート最適**です:

```
正確性 ↑
    │   ★ Sonnet+CoT（最高品質、最高コスト）
    │
    │       ★ Sonnet+ZeroShot（良好なバランス）
    │
    │           ★ Haiku+FewShot（最も安価な良い選択肢）
    │
    └──────────────────────────── コスト →
```

パレートフロンティアは、「タスクあたり$Xで得られる最良の結果は何か？」という問いに答える助けになります。

### 4. プロンプト戦略の影響

異なるプロンプト戦略は、品質とコストをトレードオフします:

| 戦略 | 正確性 | コスト | 使いどころ |
|----------|----------|------|----------|
| Zero-shot | ベースライン | 最低 | シンプルで明確に定義されたタスク |
| Few-shot | +10〜15% | 中程度 | 明確なパターンを持つタスク |
| Chain-of-thought | +15〜25% | 最高 | 複雑な推論タスク |

## ⚠️ 重要な考慮事項

- **複数回のトライアルを実行する** — 1回のトライアルはベンチマークとは言えません。設定ごとに最低3〜5回は実行しましょう
- **ばらつきを考慮する** — 非決定的な出力のため、実行のたびに結果は変動します
- **コストはすぐに積み上がる** — 完全なマトリクスベンチマークは高価になり得ます。まずシミュレーションモードから始めましょう
- **トークン価格は変動する** — プロバイダーが価格を更新したら、`cost_per_input_token`と`cost_per_output_token`を更新しましょう

## 🔗 リソース

- [Chatbot Arena: An Open Platform for Evaluating LLMs by Human Preference — Chiang et al., 2024](https://arxiv.org/abs/2403.04132) — Eloレーティングによる人間の選好ベンチマーク手法とオープンリーダーボードのアプローチ
- [Holistic Evaluation of Language Models (HELM) — Liang et al., 2022](https://arxiv.org/abs/2211.09110) — 正確性・頑健性・公平性・効率性にわたる多次元評価
- [Chain-of-Thought Prompting Elicits Reasoning in Large Language Models — Wei et al., 2022](https://arxiv.org/abs/2201.11903) — 推論タスクで顕著な精度向上を示した、CoTプロンプティングの基礎となる論文
- [Language Models are Few-Shot Learners — Brown et al., 2020](https://arxiv.org/abs/2005.14165) — few-shotのin-context学習をプロンプティングのパラダイムとして確立したGPT-3論文
- [AI Agent Benchmarks — Evidently AI](https://www.evidentlyai.com/blog/ai-agent-benchmarks) — AIエージェント向けベンチマークの全体像

## 👉 次のステップ

ベンチマーキングを習得したら、次に進みましょう:
- **[Eval Harness](../07-eval-harness/)** — 5つのテクニックすべてを統一されたパイプラインに統合する集大成
- **実験** — 自分のモデルとプロンプト戦略をベンチマークに追加してみましょう
- **探求** — ベンチマーク結果を[チュートリアル02](../02-evals/)のevalスコアと組み合わせてみましょう
