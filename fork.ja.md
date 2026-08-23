# このフォークについて

[agenticloops-ai/agentic-ai-engineering](https://github.com/agenticloops-ai/agentic-ai-engineering) を
OpenRouter対応・日本語化した個人プロジェクトです。

## 変更内容

- 各レッスンに OpenRouter 対応版スクリプト（`NN_xxx_openrouter.py`）を追加
  - デフォルトモデル: `deepseek/deepseek-v4-flash`
- OpenRouter対応レッスンのコメント・プロンプトを日本語化（`*_ja/` ディレクトリ、各`README.ja.md`）
- オリジナルのスクリプト・READMEには変更を加えていません（すべて新規ファイルとして追加）

## 進捗状況

| モジュール             | ディレクトリ                | 状態       |
|------------------------|-----------------------------|------------|
| 01-foundations         | 01-simple-llm-call          | ✅ 完了   |
| 01-foundations         | 02-prompt-engineering       | ✅ 完了   |
| 01-foundations         | 03-chat                     | ✅ 完了   |
| 01-foundations         | 04-tool-use                 | ✅ 完了   |
| 01-foundations         | 05-agent-loop               | ✅ 完了   |
| 01-foundations         | 06-codebase-navigator       | ⬜ 未着手 |
| 02-effective-agents    | 01-prompt-chaining          | ⬜ 未着手 |
| 02-effective-agents    | 02-routing                  | ⬜ 未着手 |
| 02-effective-agents    | 03-parallelization          | ⬜ 未着手 |
| 02-effective-agents    | 04-orchestrator-workers     | ⬜ 未着手 |
| 02-effective-agents    | 05-evaluator-optimizer      | ⬜ 未着手 |
| 02-effective-agents    | 06-human-in-the-loop        | ⬜ 未着手 |
| 02-effective-agents    | 07-content-writer           | ⬜ 未着手 |
| 03-advanced-techniques | 01-structured-output        | ⬜ 未着手 |
| 03-advanced-techniques | 02-streaming                | ⬜ 未着手 |
| 03-advanced-techniques | 03-context-engineering      | ⬜ 未着手 |
| 03-advanced-techniques | 04-cost-optimization        | ⬜ 未着手 |
| 03-advanced-techniques | 05-memory                   | ⬜ 未着手 |
| 03-advanced-techniques | 06-rag-techniques           | ⬜ 未着手 |
| 03-advanced-techniques | 07-multimodal               | ⬜ 未着手 |
| 03-advanced-techniques | 08-guardrails               | ⬜ 未着手 |
| 04-testing-evaluation  | 01-unit-testing-agents      | ⬜ 未着手 |
| 04-testing-evaluation  | 02-evals                    | ⬜ 未着手 |
| 04-testing-evaluation  | 03-tracing-debugging        | ⬜ 未着手 |
| 04-testing-evaluation  | 04-red-teaming-safety       | ⬜ 未着手 |
| 04-testing-evaluation  | 05-benchmarking             | ⬜ 未着手 |
| 04-testing-evaluation  | 06-eval-frameworks          | ⬜ 未着手 |
| 04-testing-evaluation  | 07-eval-harness             | ⬜ 未着手 |
| 05-loop-engineering    | 01-skills                   | ⬜ 未着手 |
| 05-loop-engineering    | 02-hooks-lifecycle          | ⬜ 未着手 |
| 05-loop-engineering    | 03-sandboxing               | ⬜ 未着手 |
| 05-loop-engineering    | 04-mcp                      | ⬜ 未着手 |
| 05-loop-engineering    | 05-subagents                | ⬜ 未着手 |
| 05-loop-engineering    | 06-context-compaction       | ⬜ 未着手 |
| 05-loop-engineering    | 07-extensible-agent         | ⬜ 未着手 |
| 06-frameworks          | 01-no-framework             | ⬜ 未着手 |
| 06-frameworks          | 02-langgraph                | ⬜ 未着手 |
| 06-frameworks          | 03-pydantic-ai              | ⬜ 未着手 |
| 06-frameworks          | 04-google-adk               | ⬜ 未着手 |
| 06-frameworks          | 05-aws-strands              | ⬜ 未着手 |
| 06-frameworks          | 06-crewai                   | ⬜ 未着手 |
| 06-frameworks          | 07-autogen                  | ⬜ 未着手 |
| 06-frameworks          | 08-llamaindex               | ⬜ 未着手 |
| 06-frameworks          | 09-semantic-kernel          | ⬜ 未着手 |
| 07-production          | 01-twelve-factor-agents     | ⬜ 未着手 |
| 07-production          | 02-deployment-strategies    | ⬜ 未着手 |
| 07-production          | 03-monitoring-observability | ⬜ 未着手 |
| 07-production          | 04-cost-optimization        | ⬜ 未着手 |
| 07-production          | 05-security-guardrails      | ⬜ 未着手 |

## ブランチ構成

- `main` — upstream（オリジナル）の追従用。変更を加えていません
- `ja-openrouter` — このフォークの変更を集約したブランチ（デフォルトブランチ）

## 移植・翻訳の方針

[CLAUDE.ja-openrouter.md](CLAUDE.ja-openrouter.md) を参照してください。

## 翻訳について

日本語訳（コメント・README・プロンプト等）はLLM（Claude Code）によるものです。
訳文の正確性は人手でのチェックを行っていません。誤訳・不自然な訳が含まれる可能性があるため、
技術的な正確性が必要な箇所（API仕様、パラメータの意味など）は、必要に応じてオリジナルの英語版
（`upstream`ブランチ、または各`NN_xxx_anthropic.py`/`NN_xxx_openai.py`）と併せて参照してください。

## ベースリビジョン

このフォークは以下の時点の `agenticloops-ai/agentic-ai-engineering` (main) をベースにしています。

- コミット: `401dc0f7f1fa09ac09eebbd7dacc04d1c34bd743`
- 日付: 2026-08-08

## 免責事項

個人の学習目的の翻訳・移植であり、原著者・OpenRouter社とは無関係です。