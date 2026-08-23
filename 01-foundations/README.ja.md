<!-- ---
title: "AIエージェントの基礎"
description: "段階的なハンズオンチュートリアルを通じて、AIエージェントの中核となる構成要素を習得する"
--- -->

# AIエージェントの基礎

段階的なハンズオンチュートリアルを通じて、AIエージェントの中核となる構成要素を習得します。

<!-- TODO: Add reference to blog post "How Agents Work: The Patterns Behind the Magic" on Substack -->

## 🗺️ 進行パス

| ステップ | チュートリアル | 追加される内容 |
|:----:|----------|-------------|
| 1 | [Simple LLM Call](01-simple-llm-call/) | APIの基礎、トークン追跡 |
| 2 | [Prompt Engineering](02-prompt-engineering/) | + 振る舞いの制御、構造化出力 |
| 3 | [Interactive Chat](03-chat/) | + 会話履歴、インタラクティブ性 |
| 4 | [Tool Use](04-tool-use/) | + 関数呼び出し、ツール実行 |
| 5 | [Agent Loop](05-agent-loop/) | + 自律性、複数ステップの推論 |
| 🏆 | [Codebase Navigator](06-codebase-navigator/) | + 検索、ツール & メモリ |

## 💡 成功のためのヒント

1. **各チュートリアルを実行する** - コードを読むだけでなく、実行してください
2. **両方のバージョンを試す** - AnthropicとOpenAIのアプローチを比較してください
3. **改造して実験する** - プロンプトを変更したり、機能を追加したり、あえて壊してみたりしてください
4. **ログを読む** - 各ステップで何が起きているかを理解してください
5. **トークンを追跡する** - API使用量とコストを意識してください
6. **段階的に積み上げる** - 各チュートリアルは1つずつ新しい概念を導入します

> 各チュートリアルは、比較しやすいように**AnthropicとOpenAI両方の実装**を同じディレクトリに含んでいます！

## 📚 チュートリアル

### [01 - Simple LLM Call](01-simple-llm-call/)

**学べること:**
- APIクライアントを初期化する
- 最初のAPI呼び出しを行う
- ストリーミングと非ストリーミング両方のAPIを理解する
- コールバックでトークン使用量を追跡する

**キーコンセプト:** APIの基礎、トークン追跡、クリーンなコードパターン

---

### [02 - Prompt Engineering](02-prompt-engineering/)

**学べること:**
- 効果的なシステムメッセージを作成する
- ロールベースのプロンプティングを使う
- Few-shot学習を適用する
- 構造化された出力（JSON）を要求する

**進化:** モデルの振る舞いと出力形式に対する制御を追加します

---

### [03 - Chat](03-chat/)

**学べること:**
- インタラクティブなチャットループを構築する
- 会話履歴を管理する
- メッセージのロール（user/assistant）を扱う
- Richフォーマットでより良いユーザー体験を作る


**進化:** これまでのチュートリアルに、会話のコンテキストとインタラクティブ性を追加します

---

### [04 - Tool Use](04-tool-use/)

**学べること:**
- 適切なスキーマでツールを定義する
- ツール呼び出しリクエストを処理する
- ツールを実行し、結果を返す
- 複数ターンにわたるツールのやり取りを管理する

**進化:** 関数呼び出しを通じてモデルがアクションを取れるようにします

---

### [05 - Agent Loop](05-agent-loop/)

**学べること:**
- 完全な自律エージェントループを構築する
- 意思決定ロジックを実装する
- 複雑な複数ステップのタスクを処理する
- タスクの完了を自動的に検知する

**進化:** これまでのすべてを組み合わせ、ツール呼び出しを連鎖させる完全に自律的なエージェントにします

---

### 🏆 [06 - Codebase Navigator](06-codebase-navigator/)

**学べること:**
- ChromaDBを使った検索拡張生成（RAG）を実装する
- LLMを、自律的に呼び出せるツールに接続する
- セッションをまたいでコンテキストを維持する永続的なメモリを追加する
- 実際のコードベースを探索する実用的なエージェントを構築する

**進化:** [拡張LLM (Augmented LLM)](https://www.anthropic.com/engineering/building-effective-agents) — 検索・ツール・メモリで強化された、すべてのエージェントシステムの基礎となるビルディングブロック — を構築します

---

## 🔗 リソース

### Anthropic Claude
- [Anthropic Documentation](https://docs.anthropic.com/)
- [Claude API Reference](https://docs.anthropic.com/en/api/messages)
- [Tool Use Guide](https://docs.anthropic.com/en/docs/tool-use)
- [Prompt Engineering Guide](https://docs.anthropic.com/en/docs/prompt-engineering)

### OpenAI GPT
- [OpenAI Documentation](https://platform.openai.com/docs)
- [Responses API](https://platform.openai.com/docs/api-reference/responses)
- [Function Calling Guide](https://platform.openai.com/docs/guides/function-calling)
- [Prompt Engineering Guide](https://platform.openai.com/docs/guides/prompt-engineering)
