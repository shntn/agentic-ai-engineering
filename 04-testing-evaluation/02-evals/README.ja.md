<!-- ---
title: "Evals"
description: "正確性・品質・時系列でのリグレッションを測定する評価スイートを構築する"
icon: "bar-chart"
--- -->

# Evals

決定的なアサーションを超え、エージェント品質の**統計的評価**へと踏み込みます。Anthropicのeval駆動開発の方法論に従い、成功基準をevalタスクとして定義し、複数種類のグレーダーで採点し、品質を時系列で追跡し、リグレッションを自動的に検出します。

## 🎯 学べること

- コードベースのグレーダーを構築する: キーワードマッチング、正規表現、出典引用、ツール呼び出しの検証
- 構造化されたルーブリックとchain-of-thought判定による**LLM-as-judge**パターンを実装する
- **ゴールデンデータセット**を設計する——リグレッションテスト向けに厳選された入出力ペア
- マルチグレーダー採点によるエンドツーエンドのevalパイプラインを構築する
- 合格率をベースラインと比較してリグレッションを検出する
- Anthropicのeval語彙を理解する: task、trial、transcript、outcome、grader

## 📦 利用可能なサンプル

| スクリプト | ファイル | 説明 |
| ------ | ---- | ----------- |
| Code-Based Graders | [01_code_based_graders.py](01_code_based_graders.py) | キーワード・正規表現・出典引用・ツール呼び出しのグレーダー |
| LLM-as-Judge | [02_llm_as_judge.py](02_llm_as_judge.py) | chain-of-thoughtによる構造化ルーブリック採点 |
| Eval Pipeline | [03_eval_pipeline.py](03_eval_pipeline.py) | エンドツーエンド: データセット → トライアル → 採点 → リグレッション検出 |

## 🚀 クイックスタート

> **前提条件:** Python 3.11+、APIキー、uv。完全なセットアップ手順は [SETUP.md](../../SETUP.md) を参照してください。

```bash
uv run --directory 04-testing-evaluation/02-evals python 01_code_based_graders.py

# 例
uv run --directory 04-testing-evaluation/02-evals python 03_eval_pipeline.py
```

すべてのスクリプトは、APIキーなしの**シミュレーションモード**（事前定義済みの応答を使用）と、`ANTHROPIC_API_KEY`を使った**ライブモード**の両方で動作します。

または、[Code Runner](https://marketplace.visualstudio.com/items?itemName=formulahendry.code-runner) VS Code拡張機能を使えば、開いているスクリプトをワンクリックで実行できます。

## 🔑 キーコンセプト

### 1. eval語彙（Anthropicより）

| 用語 | 定義 |
|------|-----------|
| **Task** | 入力と成功基準を持つテストケース |
| **Trial** | タスクの1回の確率的な実行（ばらつきを捉えるため複数回実行する） |
| **Transcript** | エージェントの行動の完全な記録 |
| **Outcome** | エージェント終了後の環境の最終状態 |
| **Grader** | エージェントの性能の何らかの側面を採点するロジック |
| **pass@k** | k回のトライアルのうち少なくとも1回成功する確率 |
| **pass^k** | k回すべてのトライアルが成功しなければならない（一貫性をテストする） |

### 2. 3種類のグレーダー

```python
# コードベース: 高速、決定的、安価
class KeywordGrader:
    def grade(self, answer, expected_keywords) -> GraderResult: ...

# モデルベース: 柔軟、繊細、高価
class LLMJudge:
    def evaluate(self, question, answer, reference) -> JudgeResult: ...

# 人間: 最高水準、非常に高価、スケールしない
# （言及のみで未実装——自動グレーダーのキャリブレーションに使う）
```

### 3. 構造化出力によるLLM-as-Judge

tool_choiceを使って構造化された採点を強制します:

```python
JUDGE_TOOLS = [{
    "name": "submit_evaluation",
    "input_schema": {
        "properties": {
            "reasoning": {"type": "string"},       # まずchain-of-thought
            "accuracy_score": {"type": "integer"},  # 次にスコア
            "completeness_score": {"type": "integer"},
            "grounding_score": {"type": "integer"},
        }
    }
}]
# tool_choice={"type": "tool", "name": "submit_evaluation"} を使う
```

### 4. ゴールデンデータセットの設計

15〜20件の厳選されたタスクから始めます（Anthropicは20〜50件から始めることを推奨しています）:

```json
{
    "id": "task_001",
    "question": "What are the key benefits of microservices?",
    "expected_keywords": ["scalability", "fault isolation"],
    "expected_source_ids": ["doc_001"],
    "difficulty": "easy",
    "category": "architecture"
}
```

以下を組み合わせて含めましょう: 単一ドキュメントの簡単なタスク、複数ドキュメントにまたがる難しい統合タスク、そして拒否すべきスコープ外の質問です。

## ⚠️ 重要な考慮事項

- **小さく始める** — よく練られた15〜20件のタスクは、1000件の汎用的なタスクに勝ります
- **グレーダーをキャリブレーションする** — 自動スコアを人間の判断と比較しましょう
- **LLM-as-judgeは無料ではない** — 評価のたびにトークンコストがかかります。まずはコードベースのグレーダーを使いましょう
- **合格率を時系列で追跡する** — 5%の低下はリグレッションの調査に値するシグナルです

## 🔗 リソース

- [Demystifying Evals for AI Agents — Anthropic](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) — このチュートリアル全体で使われる基本語彙（task、trial、grader）、グレーダーの分類、8ステップのevalロードマップ
- [Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena — Zheng et al., 2023](https://arxiv.org/abs/2306.05685) — LLMジャッジの体系的研究: 人間との一致率、位置バイアス、構造化ルーブリックのアプローチ
- [Holistic Evaluation of Language Models (HELM) — Liang et al., 2022](https://arxiv.org/abs/2211.09110) — 正確性・キャリブレーション・頑健性・公平性・効率性を網羅するマルチメトリクス評価フレームワーク
- [OpenAI Evaluation Best Practices](https://platform.openai.com/docs/guides/evaluation-best-practices) — eval設計・ゴールデンデータセット・採点戦略に関する実践的な指南
- [Eval-Driven Development](https://evaldriven.org/) — 機能構築の前にevalsを構築するという規律

## 👉 次のステップ

evalsを習得したら、次に進みましょう:
- **[トレーシング & デバッグ](../03-tracing-debugging/)** — evalが失敗したとき、トレースが正確に*なぜ*失敗したかを示します
- **実験** — 自分のエージェントの失敗からゴールデンデータセットにタスクを追加してみましょう
- **探求** — 異なるルーブリック設計を試し、ジャッジの一貫性を比較してみましょう
