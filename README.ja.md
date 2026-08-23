> **📌 このフォークについて**
>
> [agenticloops-ai/agentic-ai-engineering](https://github.com/agenticloops-ai/agentic-ai-engineering) のフォークです。
> OpenRouter対応・日本語化の詳細は [fork.ja.md](fork.ja.md) をご覧ください。

---

<div align="center">

<!-- ![AI Agents Engineering](.docs/banner.png) -->

<!-- Keep these links. Translations will automatically update with the README. -->
[English](https://zdoc.app/en/agenticloops-ai/agentic-ai-engineering) |
[Deutsch](https://zdoc.app/de/agenticloops-ai/agentic-ai-engineering) |
[Español](https://zdoc.app/es/agenticloops-ai/agentic-ai-engineering) |
[français](https://zdoc.app/fr/agenticloops-ai/agentic-ai-engineering) |
[日本語](https://zdoc.app/ja/agenticloops-ai/agentic-ai-engineering) |
[한국어](https://zdoc.app/ko/agenticloops-ai/agentic-ai-engineering) |
[Português](https://zdoc.app/pt/agenticloops-ai/agentic-ai-engineering) |
[中文](https://zdoc.app/zh/agenticloops-ai/agentic-ai-engineering)

[![GitHub stars](https://img.shields.io/github/stars/agenticloops-ai/agentic-ai-engineering?style=for-the-badge&logo=github&color=e3b341&labelColor=191919)](https://github.com/agenticloops-ai/agentic-ai-engineering/stargazers)
[![Website](https://img.shields.io/badge/Website-agenticloops.ai-green?style=for-the-badge&logo=googlechrome&logoColor=white)](https://agenticloops.ai)
[![Substack](https://img.shields.io/badge/Substack-Blogs_&_Newsletter-orange?style=for-the-badge&logo=substack&logoColor=white)](https://agenticloopsai.substack.com)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-Connect-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/company/agenticloops-ai)
[![Follow @agenticloops_ai](https://img.shields.io/badge/Follow%20%40agenticloops__ai-black?style=for-the-badge&logo=x&logoColor=white)](https://x.com/agenticloops_ai)

</div>

# Agentic AI Engineering
**AIエージェントを作ることはエンジニアリングであり、魔法ではありません。エージェントについて読むのはもうやめて、作り始めましょう。**

<img align="right" width="400" src=".docs/agentic-loop.gif" alt="The agentic loop: a prompt joins the system prompt and tool schemas in the context, the LLM reasons, executes tools, results append back to the context, and the loop repeats until it can answer" />

このリポジトリは、**Claude Code**、**Claude Cowork**、**Codex**、**GitHub Copilot** のような人気のあるエージェントの裏側で何が起きているのかを理解し、自分自身でエージェントを作れるようになりたいエンジニアのためのものです。

- 🔁 **一連のすべての道のり** — 最初のLLM呼び出しから、本番用の評価ハーネスまで
- 🧱 **凝ったフレームワークは不要** — 必要なのはLLM API、いくつかのツール、そしてループだけ
- 🌱 **AI/MLの事前知識は不要** — Pythonの基礎と**好奇心**があれば十分です

💡 **今週末に1つ作ってみましょう。** 100本のブログ記事を読むより、エージェントへの理解が深まります。

役に立ったと思ったら、⭐️ スターをつけていただけると、正しい方向に進んでいることが分かって励みになります。[💬 ディスカッション](https://github.com/agenticloops-ai/agentic-ai-engineering/discussions)に参加するか、[🐛 issue](https://github.com/agenticloops-ai/agentic-ai-engineering/issues)を報告してください — みなさんの声が、次に何を作るかに直接反映されます。

<br clear="both" />

## 🎯 なぜこのリポジトリなのか？

- 📦 **第一原理から、ブラックボックスなし。** フレームワークを1つでも導入する _前に_、エージェントループ、ツール実行機、メモリ層、評価ハーネスをゼロから構築します。何かに隠される前に、その抽象化が何を隠しているのかを学びましょう。
- ⚡ **1コマンドで実行可能。** `uv run --directory <tutorial> python <script>.py`。condaのダンスも、Jupyterカーネル探しも不要です。
- 🔬 **本番エージェントを分解します。** [_Disassembling AI Agents_](https://agenticloopsai.substack.com) というSubstackシリーズでは、Claude Code、GitHub Copilot、OpenCodeをリバースエンジニアリングしています。実際のエージェントがどう動くかを読んだ後、ここでそのパーツを自分の手で再構築できます。

## ❓ なぜこれを学ぶ必要があるのか？
エージェントへの習熟度は、新時代のデータ構造面接のようなものです。私たちは**第一原理から**それを教えます — フレームワークを導入する前に、ループ、ツール呼び出し、メモリ、評価を自分の手で構築します。魔法はありません。ブラックボックスもありません。発明された順番通りに、プリミティブだけを学びます。

<table>
<tr>
<td width="22%" align="center" valign="middle">
<a href="https://ghuntley.com"><img src="https://avatars.githubusercontent.com/u/127353?v=4" width="72" alt="Geoffrey Huntley" /></a>
<br/>
<b>Geoffrey Huntley</b>
<br/>
<sub><a href="https://ghuntley.com/ralph/">Ralph Wiggum</a> の作者</sub>
</td>
<td width="78%" valign="middle">
<em>How many of you actually can pull out a whiteboard and build me an agent? Can you show me the inferencing loop?</em>
<br/>
<b><em>If you don't know this, your career is in jeopardy.</em></b>
<br/>
<em>What is a tool call? If you don't know what that is, you need to learn what it is and all these basic fundamentals. I preference candidates if they know what a tool call is, how the inferencing loop works, pull out a whiteboard — the same way we used to say, show me a linked list, reverse me this data structure.</em>
<br/>
<em><b>This is now baseline knowledge</b> because we're getting candidates in that can answer this stuff.</em>
<br/>
<a href="https://www.youtube.com/clip/UgkxNl2grro6BiM_x9bGSH_xMOSl32fxvthr">▶️ 2026年のSWEに必須の基礎スキルと知識</a>
</td>
</tr>
</table>

**すべての答えはこのリポジトリの中にあります。** ツール呼び出しとは何か、推論ループはどう動くのか、コンテキストには何が入っているのか — あなたはただ答えを読むだけでなく、[01 - Foundations](01-foundations/README.md) から始めて、自分の手でそれらを作り上げていきます。次の面接では、あなたがホワイトボードでループを描く側になります。

## ⚡ 60秒クイックスタート

```bash
brew install uv   # または: pipx install uv
git clone https://github.com/agenticloops-ai/agentic-ai-engineering.git
cd agentic-ai-engineering
cp .env.example .env   # AnthropicやOpenAIのキーを追加

uv run --directory 01-foundations/01-simple-llm-call python 01_llm_call_anthropic.py
```

以上です。すべてのチュートリアルは自己完結型かつ冪等なので、どこからでも始められます。セットアップの詳細は[SETUP.md](SETUP.md)を参照してください。または[Codespacesで開く](https://codespaces.new/agenticloops-ai/agentic-ai-engineering)と、ローカルのセットアップを完全にスキップできます。

---

### 🎓 [01 - Foundations](01-foundations/README.md) ![new](https://img.shields.io/badge/new-brightgreen)

最初の一歩 — 単一のAPI呼び出しから、完全に自律的なエージェントループまで。裏側で実際に何が起きているかを理解するために、すべてをゼロから構築します。

1. **[Simple LLM Call](01-foundations/01-simple-llm-call/)** — トークン追跡を伴う最初のAPI呼び出し
2. **[Prompt Engineering](01-foundations/02-prompt-engineering/)** — モデルの振る舞いを導く
3. **[Chat](01-foundations/03-chat/)** — メッセージ履歴を持つインタラクティブなチャット
4. **[Tool Use](01-foundations/04-tool-use/)** — 関数呼び出しを可能にする
5. **[Agent Loop](01-foundations/05-agent-loop/)** — ツールを使う自律エージェント
6. **[Codebase Navigator](01-foundations/06-codebase-navigator/)** ![🏆 capstone](https://img.shields.io/badge/🏆_capstone-blue) — RAG・ツール・メモリを備えた拡張LLM

### 🧩 [02 - Effective Agents Patterns](02-effective-agents/README.md) ![new](https://img.shields.io/badge/new-brightgreen)

おもちゃのデモと本物のエージェントを分ける、アーキテクチャパターン。Anthropicの「[Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents)」に基づき、いつ連鎖させ、ルーティングし、並列化し、委譲すべきかを学びます。

1. **[Prompt Chaining](02-effective-agents/01-prompt-chaining/)** — 連続した複数ステップのパイプライン
2. **[Routing](02-effective-agents/02-routing/)** — 入力を分類し、専門のハンドラーに振り分ける
3. **[Parallelization](02-effective-agents/03-parallelization/)** — ファンアウト/ファンイン、並列ツール呼び出し
4. **[Orchestrator-Workers](02-effective-agents/04-orchestrator-workers/)** — 動的なタスク分解
5. **[Evaluator-Optimizer](02-effective-agents/05-evaluator-optimizer/)** — 自己批評、反復的な改善
6. **[Human in the Loop](02-effective-agents/06-human-in-the-loop/)** — 承認ゲート、エスカレーション、フィードバック
7. **[Content Writer](02-effective-agents/07-content-writer/)** ![🏆 capstone](https://img.shields.io/badge/🏆_capstone-blue) — モジュール内のすべてのエージェントワークフローパターンを組み合わせた完全なエージェント

### 🧬 [03 - Advanced Techniques](03-advanced-techniques/README.md) ![new](https://img.shields.io/badge/new-brightgreen)

エージェントがプロトタイプ段階を抜けた瞬間に直面する、実践的なエンジニアリング課題。コンテキスト、コスト、メモリ、マルチモーダル性、安全性を、1つずつチュートリアルで解決していきます。

1. **[Structured Output](03-advanced-techniques/01-structured-output/)** — JSONモード、スキーマ、制約付き生成
2. **[Streaming](03-advanced-techniques/02-streaming/)** — SSE、トークン単位の出力、ストリーミングツール呼び出し
3. **[Context Engineering](03-advanced-techniques/03-context-engineering/)** — ウィンドウ戦略、要約、ツールコンテキスト
4. **[Cost Optimization](03-advanced-techniques/04-cost-optimization/)** — プロンプトキャッシュ、モデルルーティング
5. **[Memory](03-advanced-techniques/05-memory/)** — 短期記憶、長期記憶、メモリの検査
6. **[RAG Techniques](03-advanced-techniques/06-rag-techniques/)** — ハイブリッド検索、エージェント型検索
7. **[Multimodal](03-advanced-techniques/07-multimodal/)** — ビジョン、画像生成、音声
8. **[Guardrails](03-advanced-techniques/08-guardrails/)** — 入出力フィルタリング、安全性パターン

### 🧪 [04 - Testing & Evaluation](04-testing-evaluation/README.md) ![new](https://img.shields.io/badge/new-brightgreen)

エージェントは非決定的です — テストには異なる考え方が必要です。品質を測定し、リグレッションを検出し、リリース前に自信を築きます。

1. **[Unit Testing Agents](04-testing-evaluation/01-unit-testing-agents/)** — LLMのモック化、決定的なテスト
2. **[Evals](04-testing-evaluation/02-evals/)** — 精度、品質、リグレッションのベンチマーク
3. **[Tracing & Debugging](04-testing-evaluation/03-tracing-debugging/)** — 開発中のオブザーバビリティ
4. **[Red Teaming & Safety](04-testing-evaluation/04-red-teaming-safety/)** — 敵対的テスト、ガードレール
5. **[Benchmarking](04-testing-evaluation/05-benchmarking/)** — モデル、プロンプト、アーキテクチャを直接比較する
6. **[Eval Frameworks](04-testing-evaluation/06-eval-frameworks/)** — Promptfoo、Braintrust、Langfuseとの連携
7. **[Eval Harness](04-testing-evaluation/07-eval-harness/)** ![🏆 capstone](https://img.shields.io/badge/🏆_capstone-blue) — すべての手法を組み合わせた完全な評価パイプライン

### 🔁 [05 - Loop Engineering](05-loop-engineering/README.md) ![coming soon](https://img.shields.io/badge/coming%20soon-orange)

素のエージェントループを手なずけます。スキル、フック、サンドボックス化、MCP、サブエージェント、コンパクション — 1つずつコントロールサーフェスを追加し、素朴なループを本物の拡張可能なエージェントに変えます。

1. **[Skills](05-loop-engineering/01-skills/)** — ファイルシステム型エージェントスキル、段階的開示
2. **[Hooks & Lifecycle](05-loop-engineering/02-hooks-lifecycle/)** — ツール使用前後・停止イベントをインターセプトする
3. **[Sandboxing](05-loop-engineering/03-sandboxing/)** — リソース制限付きでエージェントが実行するコードを隔離する
4. **[MCP Integration](05-loop-engineering/04-mcp/)** — MCPサーバーからツールを発見し、自分のツールを公開する
5. **[Subagents & Delegation](05-loop-engineering/05-subagents/)** — 隔離されたコンテキストを持つ子ループを生成する
6. **[Context Compaction](05-loop-engineering/06-context-compaction/)** — 長時間稼働するループの履歴を圧縮する
7. **[Extensible Agent](05-loop-engineering/07-extensible-agent/)** ![🏆 capstone](https://img.shields.io/badge/🏆_capstone-blue) — フック + サンドボックス + MCP + サブエージェント + コンパクションを組み合わせる

### 🏗️ [06 - Frameworks](06-frameworks/README.md) ![coming soon](https://img.shields.io/badge/coming%20soon-orange)

1つのエージェントを、9通りの実装で。同じシステムを各フレームワークで構築し、自分の手でトレードオフを比較します。

1. **[No Framework](06-frameworks/01-no-framework/)** — 素のSDKによるベースライン
2. **[LangGraph](06-frameworks/02-langgraph/)** — グラフベースのオーケストレーション
3. **[Pydantic AI](06-frameworks/03-pydantic-ai/)** — 型安全なエージェント
4. **[Google ADK](06-frameworks/04-google-adk/)** — GoogleのAgent Development Kit
5. **[AWS Strands](06-frameworks/05-aws-strands/)** — AWSのエージェントSDK
6. **[CrewAI](06-frameworks/06-crewai/)** — ロールベースのマルチエージェント連携
7. **[AutoGen](06-frameworks/07-autogen/)** — マルチエージェントの会話
8. **[LlamaIndex](06-frameworks/08-llamaindex/)** — データ中心のエージェント
9. **[Semantic Kernel](06-frameworks/09-semantic-kernel/)** — MicrosoftのAIオーケストレーション

### 🏭 [07 - Production](07-production/README.md) ![coming soon](https://img.shields.io/badge/coming%20soon-orange)

「自分のノートパソコンでは動く」と「本番でスケールして確実に動く」の間にあるギャップ。原則、デプロイ、モニタリング、コスト管理、セキュリティ。

1. **[12-Factor Agents](07-production/01-twelve-factor-agents/)** — 本番グレードのエージェントのための原則
2. **[Deployment Strategies](07-production/02-deployment-strategies/)** — コンテナ、サーバーレス、スケーリング
3. **[Monitoring & Observability](07-production/03-monitoring-observability/)** — 本番環境でのメトリクス、ロギング、トレーシング
4. **[Cost Optimization](07-production/04-cost-optimization/)** — トークン予算、キャッシュ、モデルルーティング
5. **[Security & Guardrails](07-production/05-security-guardrails/)** — 認証、サンドボックス化、インジェクション対策
6. **[Error Handling & Resilience](07-production/06-error-handling-resilience/)** — リトライ、フォールバック、グレースフルデグラデーション


## 🗂️ チュートリアルの構成

チュートリアルは、基礎から高度な概念へと進む**モジュール**（`01-foundations`、`02-effective-agents`）に整理されています。各モジュールには、前のレッスンの上に積み上げていく番号付きの**チュートリアル**が含まれています。各チュートリアルフォルダの中には:

- **Pythonスクリプト** ![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white) - 主要な概念を示す、自己完結型で実行可能なサンプル
- **README.md** - 詳細な説明、コードの解説、学習目標

個々のスクリプトを独立して探索することも、最初から最後まで学習パスを通しでたどることもできます。各モジュールは、そのモジュールのすべての概念を1つの本番スタイルのエージェントに組み合わせた ![🏆 capstone](https://img.shields.io/badge/🏆_capstone-blue) プロジェクトで締めくくられます。

## 📚 併せて読みたい記事

チュートリアルは「作り方」を教えます。私たちの[Substack](https://agenticloopsai.substack.com)では、まずメンタルモデルを提供します — エージェントが実際にどう動くかの基礎的な入門記事に続き、あなたが毎日使っている実際の本番エージェントの分解記事があります。**記事を読み、チュートリアルを開き、そのパターンを再構築してください。**

[**How Agents Work: The Patterns Behind the Magic**](https://agenticloopsai.substack.com/p/how-agents-work-the-patterns-behind) - 第一原理から見る中核のエージェントループ。4つのパターンレベル（ワンショット → シングルツール → ReAct → プランニング）、振る舞い設計としてのシステムプロンプトの役割、そして外側のループとしてのRalph Mode。このリポジトリを開く前に1つだけ読むなら、これを読んでください。→ `01-foundations` と対になっています。

## 💜 応援する

このプロジェクトが役に立ったと思ったら、ぜひ応援をご検討ください:

[![Sponsor](https://img.shields.io/badge/Sponsor-Support_Us-ea4aaa?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/agenticloops-ai)
## 💬 よくある質問

**モジュールが見つからない？** レッスンディレクトリで `uv sync` を実行してください。

**APIエラーや認証エラーが出る？** 実行するサンプルに応じて、[Anthropic](https://console.anthropic.com/)や[OpenAI](https://platform.openai.com/)、あるいは両方のAPIキーが必要です。詳細は[SETUP.md](SETUP.md)を参照してください。


## ⚖️ ライセンス

このプロジェクトはMITライセンスの下でライセンスされています。詳細は[LICENSE](LICENSE)ファイルを参照してください。
