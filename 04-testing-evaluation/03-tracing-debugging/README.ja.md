<!-- ---
title: "トレーシング & デバッグ"
description: "すべてのLLM呼び出し・ツール呼び出し・意思決定ポイントをトレースする"
icon: "search"
--- -->

# トレーシング & デバッグ

エージェントが予期しない動作をしたとき、**正確な理由**を知る必要があります。トレーシングは、すべてのLLM呼び出し・ツール呼び出し・意思決定ポイント・中間結果を含む完全な実行フローを記録するため、後からエージェントの推論経路を再構築できます。

このチュートリアルでは、純粋なPythonを使って**可観測性を第一級の関心事**として扱う方法を教えます。外部依存はありません——まず概念を学び、その後に本番向けツールで応用します。

## 🎯 学べること

- コンテキストマネージャーとデコレーターによるスパンベースのトレースコレクターを構築する
- 実行トレースをRichのツリー階層として可視化する
- アンチパターンを検出する: 過剰な呼び出し、ループ、検索の繰り返し、高いトークン使用量
- 同じタスクの実行間でトレースを比較する
- 記録されたトレースをたどってエージェントの失敗をデバッグする
- チェックポイントからエージェントの実行をリプレイする

## 📦 利用可能なサンプル

| スクリプト | ファイル | 説明 |
| ------ | ---- | ----------- |
| Trace Collector | [01_trace_collector.py](01_trace_collector.py) | スパン・コンテキストマネージャー・デコレーターを備えた`TraceCollector`を構築 |
| Trace Analysis | [02_trace_analysis.py](02_trace_analysis.py) | トレースを読み込み、アンチパターンを検出し、メトリクスを計算 |
| Trace Debugging | [03_trace_debugging.py](03_trace_debugging.py) | 失敗ポイントの特定、意思決定経路、リプレイ |

## 🚀 クイックスタート

> **前提条件:** Python 3.11+、APIキー、uv。完全なセットアップ手順は [SETUP.md](../../SETUP.md) を参照してください。

```bash
uv run --directory 04-testing-evaluation/03-tracing-debugging python 01_trace_collector.py

# 例
uv run --directory 04-testing-evaluation/03-tracing-debugging python 02_trace_analysis.py
```

すべてのスクリプトには**サンプルトレースデータ**が含まれており、APIキーなしで動作します。`ANTHROPIC_API_KEY`が設定されている場合、ライブモードが自動的に有効になります。

または、[Code Runner](https://marketplace.visualstudio.com/items?itemName=formulahendry.code-runner) VS Code拡張機能を使えば、開いているスクリプトをワンクリックで実行できます。

## 🔑 キーコンセプト

### 1. スパンベースのトレーシング

すべての操作は、タイミング・入力・出力・子スパンを持つ**スパン**です:

```python
@dataclass
class Span:
    name: str           # "llm_call_1", "search_knowledge_base"
    span_type: str      # "llm_call", "tool_call", "agent_step"
    start_time: float
    end_time: float
    tokens: dict        # {"input": 150, "output": 80}
    children: list      # ネストされた子スパン
    error: str | None   # スパンが失敗した場合のエラーメッセージ
```

### 2. コンテキストマネージャーによるトレーシング

`TraceCollector`はコンテキストマネージャーを使って、スパンのライフサイクルを自動管理します:

```python
tracer = TraceCollector()

with tracer.span("answer_question", "agent_step") as root:
    with tracer.span("llm_call", "llm_call") as llm_span:
        response = client.messages.create(...)
        llm_span.tokens = {"input": 150, "output": 80}

    with tracer.span("search", "tool_call") as tool_span:
        results = search_knowledge_base(query)
```

### 3. アンチパターン検出

自動化された分析が、よくあるエージェントの問題を捕捉します:

| アンチパターン | 症状 | 典型的な原因 |
|-------------|---------|---------------|
| 過剰なLLM呼び出し | 単純な質問に5回超の呼び出し | 停止条件の欠落 |
| 検索の繰り返し | 同じクエリが2回検索される | 結果のキャッシュがない |
| 高いトークン使用量 | 単純なタスクに2000トークン超 | 冗長なプロンプトやループ |
| 失敗したツール呼び出し | ツールエラーがリトライされない | エラーハンドリングの欠落 |
| 非常に長いスパン | 単一操作に10秒超 | APIタイムアウトやループ |

### 4. トレースベースのデバッグ

evalが失敗したとき、トレースが失敗経路を示します:

```
1. 失敗したoutcomeから逆向きにたどる
2. エラーまたは予期しない出力を持つ最初のスパンを見つける
3. その悪い意思決定につながった入力を調べる
4. TraceReplayを使ってそのチェックポイントから再実行する
```

## ⚠️ 重要な考慮事項

- **純粋なPython、本番向けではない** — このチュートリアルは概念を教えるものです。本番では [Langfuse](https://langfuse.com/)、[Datadog](https://www.datadoghq.com/)、[OpenTelemetry](https://opentelemetry.io/) を使いましょう
- **トレースのストレージは急速に増大する** — 本番環境ではトレースをサンプリングし、保持ポリシーを設定しましょう
- **コストの帰属が重要** — どのステップが最もコストがかかっているかを知ることが、最適化の指針になります

## 🔗 リソース

- [OpenTelemetry Documentation](https://opentelemetry.io/docs/) — 本番の可観測性で使われる、スパン・トレース・コンテキスト伝搬に関する業界標準の仕様
- [Langfuse — Open Source LLM Observability](https://langfuse.com/) — コスト追跡・スコアリング・プロンプト管理を備えたLLMアプリケーション向けの本番トレーシング
- [Dapper, a Large-Scale Distributed Systems Tracing Infrastructure — Sigelman et al., 2010](https://research.google/pubs/dapper-a-large-scale-distributed-systems-tracing-infrastructure/) — OpenTelemetryに影響を与えた、スパンベースの分散トレーシングに関するGoogleの先駆的論文
- [The Three Pillars of Observability — Charity Majors](https://www.oreilly.com/library/view/distributed-systems-observability/9781492033431/ch04.html) — 相補的な可観測性シグナルとしてのメトリクス・ログ・トレース

## 👉 次のステップ

トレーシングを習得したら、次に進みましょう:
- **[レッドチーミング & 安全性](../04-red-teaming-safety/)** — 敵対的攻撃に対してエージェントをテストする
- **実験** — `TraceCollector`で自分のエージェントを計装してみましょう
- **探求** — [チュートリアル02](../02-evals/)のeval失敗とトレースを結びつけてみましょう
