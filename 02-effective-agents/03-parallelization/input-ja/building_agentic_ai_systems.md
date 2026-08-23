# エージェント型AIシステムの構築: Anthropicのエージェントフレームワークから見る5つの中核パターン

5つの中核となるワークフローパターンが、本番運用可能なエージェント型システムの基盤となっており、それぞれ特定の運用要件と複雑さのレベルに最適化されています。Anthropicの調査によると、成功の鍵は複雑さそのものではなく、正しいパターンをタスクに適切に当てはめることにあります。

## パターン1: タスクを逐次分解するプロンプトチェイニング

プロンプトチェイニングは、複雑なタスクを個別のステップに分解し、各LLM呼び出しが前のステップの出力を処理する手法です。Anthropicでは、複数ステップのワークフローにおいて、単一プロンプトによるアプローチと比較してこのパターンによりタスク失敗率が34%低下しています。

10,000ページに及ぶ規制関連書類を処理する文書レビューパイプラインを考えてみましょう。ステップ1では関連セクションを抽出します（セクションIDとページ番号を含むJSONを出力）。ステップ2では各セクションを要約します（リスクスコア1〜10を含む構造化された要約を出力）。ステップ3ではコンプライアンス上の問題にフラグを立てます（具体的な規制条項への参照を含むブール値フラグを出力）。ステップ4では最終的な推奨事項を生成します（優先度付きのランク付けされたアクション項目を出力）。

各ステップは明確な入出力の契約を維持します。要約ステップが失敗した場合、文書全体を処理し終えてからではなく、どこをデバッグすべきかがすぐに分かります。

**レイテンシに関する考慮事項**: チェーンは最大3〜5ステップに抑えてください。この閾値を超えると、多くのアプリケーションで累積レイテンシが15秒を超え、ステップが1つ増えるごとに精度が12〜18%低下します。

**エラーハンドリング**: 各ステップにサーキットブレーカーを実装してください。ステップ2が3回連続で失敗した場合は、チェーンを継続するのではなく人間によるレビューにルーティングしましょう。

## パターン2: 本番グレードのエラーハンドリングを備えた堅牢なツール統合

生のAPI統合は本番環境で失敗します。Anthropicによるエンタープライズ導入事例の分析では、エージェントの失敗の60%はLLMの推論の問題ではなく、未処理のツールエラーに起因することが分かっています。

すべての外部APIを、エージェントに優しいエラーハンドリングでラップしましょう:

```python
def call_payment_api(amount: float, account_id: str) -> dict:
    """指数バックオフとサーキットブレーカーを備えて決済を処理する。"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(
                "https://api.payment.service/charge",
                json={"amount": amount, "account": account_id},
                timeout=5
            )
            response.raise_for_status()
            return {"status": "success", "transaction_id": response.json()["id"]}
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # 指数バックオフ
                continue
            return {"status": "failed", "reason": "timeout_after_retries"}
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:  # レート制限
                return {"status": "failed", "reason": "rate_limited", "retry_after": 60}
            return {"status": "error", "code": e.response.status_code}
```

**ツールの粒度**: 何でもできる巨大な関数ではなく、原子的なツールを構築してください。決済システムには、検証・処理・確認それぞれに独立したツールが必要であり、すべてをこなす1つの決済ツールではいけません。このアプローチにより、Anthropicの社内テストではツール選択エラーが40%減少しています。

**Model Context Protocol（MCP）**: カスタム統合を作る代わりに、Slack、Google Drive、Salesforceなどのプロバイダーが提供する既存のMCPサーバーを使いましょう。MCPを使用しているチームは、統合時間が70%短縮され、メンテナンスの問題が50%減少したと報告しています。

## パターン3: 専門化された役割によるマルチエージェントオーケストレーション

50個以上のツールを持つ単一エージェントは、ツール選択における混乱率が60%高くなります。解決策は、オーケストレーターによって調整される、絞り込まれたツールセットを持つ専門化されたエージェントです。

カスタマーサポート自動化のためのアーキテクチャ例:
- **トリアージエージェント**: 5つのツール（チケット分類、緊急度スコアリング、ルーティング判断）
- **リサーチエージェント**: 8つのツール（ナレッジベース検索、顧客履歴の照会、製品ドキュメント）
- **解決エージェント**: 6つのツール（返金処理、アカウント更新、メール作成）

オーケストレーターはチケットの種類に基づいてルーティングし、確信度スコアが0.7を下回るとエスカレーションします。これにより、同じチケット量を処理する単一エージェントシステムと比較して応答時間が45%短縮されます。

**通信プロトコル**: エージェント間の引き継ぎには明示的なスキーマを定義してください。エージェントAは`{"ticket_id": "123", "classification": "billing", "confidence": 0.85, "extracted_data": {...}}`のような形式で出力します。エージェントBはこの正確な形式を期待しています——曖昧さはありません。

**競合解決**: エージェント同士の判断が食い違う場合（リサーチエージェントが「低リスク」と判定する一方、トリアージエージェントが「高リスク」と判定する場合など）、両方の評価結果を見える形にして人間によるレビューにルーティングしてください。

## パターン4: 戦略的なHuman-in-the-Loopチェックポイント

Human-in-the-Loop（HITL）チェックポイントは、自動化の効率を維持しながらコストのかかるエラーを防ぎます。鍵となるのは閾値ベースの実装です: 日常的な判断は自動化し、リスクの高いアクションには承認を必須とします。

金融サービス向けエージェントでの実装例:
```python
# エージェントが返金判断のドラフトを作成する
refund_proposal = agent.decide_refund(customer_id="123")

# 100ドル未満の金額は自動承認
if refund_proposal["amount"] < 100:
    execute_refund(refund_proposal)
else:
    # より大きな金額には人間の承認が必要
    approval = await request_approval(
        action="process_refund",
        amount=refund_proposal["amount"],
        reason=refund_proposal["reason"],
        customer_history=refund_proposal["context"]
    )
```

**閾値の最適化**: 500ドルの閾値を使用している企業では、500ドルを超える不正な返金がゼロのまま94%の自動化率を達成していると報告されています。閾値を100ドル未満に下げると承認率は99.8%に達し、人間によるレビューが非効率になります。

**承認のレイテンシ**: 非同期ワークフローとして設計してください。平均承認時間は12分であり、エージェントはタイムアウトや再起動をするのではなく、優雅に一時停止し、承認が届いたら再開すべきです。

## パターン5: タスク完了を超えた包括的な評価

多くのチームはタスク完了度（「エージェントは仕事を終えたか？」）だけを測定し、振る舞いの整合性（「正しいやり方で仕事をしたか？」）を無視しています。Anthropicの Bloomフレームワークは、この両方の側面に対応します。

カスタマーサービスエージェントの**タスク完了メトリクス**:
- 解決率: エスカレーションなしでクローズされたチケットの87%
- 正確性: 事実に基づく主張のうち92%が正しいと検証された
- レイテンシ: 平均応答時間30秒未満

**振る舞いの整合性メトリクス**:
- トーン分析: 94%の応答が「プロフェッショナルで有用」と評価された
- ポリシー遵守: 会社の返金ポリシーへの遵守率99.2%
- ハルシネーション率: 検証不能な主張を含む応答は2%未満

Bloomの自動評価は、振る舞いごとに500件以上のテストシナリオを生成し、ユーザーとのやり取りをシミュレートし、エージェントの応答を採点します。Bloomを使用しているチームは、手動評価と比較して振る舞いのドリフトを40%早く特定できたと報告しています。

**本番モニタリング**: 稼働中のエージェントと並行してシャドー評価をデプロイしてください。本番トラフィックの5%を並列の評価エージェントに通し、実際のやり取りを採点しつつ顧客体験には影響を与えないようにします。

## まとめ

• **3ステップ以上の逐次的なワークフローには、まずプロンプトチェイニングから始めてください**——各ステップに明確な入出力スキーマとサーキットブレーカーを実装し、失敗を適切に処理しましょう。

• **すべての外部APIを、指数バックオフとタイムアウトロジックを備えたエラーハンドリングツールでラップしてください**——カスタム統合を作る代わりに既存のMCPサーバーを使い、開発時間を70%削減しましょう。

• **単一エージェントが15個を超えるツールを扱う場合にのみ、マルチエージェントアーキテクチャを導入してください**——それぞれ5〜8個の絞り込まれたツールを持つ専門化されたエージェントを作成し、エージェント間の通信には明示的なJSONスキーマを使いましょう。

• **リスク閾値を超えるアクションにはHuman-in-the-Loopの承認を実装してください**——金額の上限（例: 返金なら500ドル）を設定し、承認待ちの間は優雅に一時停止する非同期ワークフローを設計しましょう。

• **初日からタスク完了度と振る舞いの整合性の両方を測定してください**——正確性、レイテンシ、ポリシー遵守率を追跡し、本番トラフィックの5%でシャドー評価を実施して振る舞いのドリフトを早期に発見しましょう。

## 参考文献

- [Building AI Agents with Anthropic's 6 Composable Patterns](https://aimultiple.com/building-ai-agents)
- [Anthropic Launches Skills Open Standard for Claude](https://aibusiness.com/foundation-models/anthropic-launches-skills-open-standard-claude)
- [Agent Skills: Anthropic's Next Bid to Define AI Standards - The New Stack](https://thenewstack.io/agent-skills-anthropics-next-bid-to-define-ai-standards/)
- [Anthropic Agents | Microsoft Learn](https://learn.microsoft.com/en-us/agent-framework/user-guide/agents/agent-types/anthropic-agent)
- [Anthropic AI Releases Bloom: An Open-Source Agentic Framework for Automated Behavioral Evaluations of Frontier AI Models - MarkTechPost](https://www.marktechpost.com/2025/12/21/anthropic-ai-releases-bloom-an-open-source-agentic-framework-for-automated-behavioral-evaluations-of-frontier-ai-models/)
- [Anthropic](https://www.anthropic.com/research/bloom)
- [Anthropic makes agent Skills an open standard - SiliconANGLE](https://siliconangle.com/2025/12/18/anthropic-makes-agent-skills-open-standard/)
- [2026 Agentic Coding Trends Report How coding agents are reshaping](https://resources.anthropic.com/hubfs/2026%20Agentic%20Coding%20Trends%20Report.pdf?hsLang=en)
- [Five Agentic Workflow Patterns. Anthropic’s framework for building… | by Daniel Davenport | Medium](https://danieldavenport.medium.com/five-agentic-workflow-patterns-9f03e356d031)
- [How AI Agents Actually Work: July 2025 | by Berto Mill | Medium](https://bertomill.medium.com/how-ai-agents-actually-work-july-2025-fe44405be906)
- [Prompt Chaining | Prompt Engineering Guide](https://www.promptingguide.ai/techniques/prompt_chaining)
- [Workflow for prompt chaining - AWS Prescriptive Guidance](https://docs.aws.amazon.com/prescriptive-guidance/latest/agentic-ai-patterns/workflow-for-prompt-chaining.html)
- [Prompt Chaining for the AI Agents: Modular, Reliable, and Scalable Workflows | by NivaLabs AI | Medium](https://medium.com/@nivalabs.ai/prompt-chaining-for-the-ai-agents-modular-reliable-and-scalable-workflows-a22d15fd5d33)
- [How to Build Autonomous Agents using Prompt Chaining with AI Primitives (No Frameworks)](https://www.freecodecamp.org/news/build-autonomous-agents-using-prompt-chaining-with-ai-primitives/)
- [Building Effective AI Agents](https://www.anthropic.com/research/building-effective-agents)
- [Prompt Chaining Vs Agentic AI: Best Use Cases Compared](https://aicompetence.org/prompt-chaining-vs-agentic-ai-use-cases-compared/)
- [Prompting Agentic AI: Best Practices for CTOs & Data Leaders](https://ubtiinc.com/agentic-ai-prompt-engineering-key-concepts-techniques-and-best-practices/)
- [Prompt Chaining in Agentic AI: How Complex Thinking Emerges One Step at a Time | by Satheesh Nataraja Pillai | Medium](https://medium.com/@pillaisatheesh74/prompt-chaining-in-agentic-ai-how-complex-thinking-emerges-one-step-at-a-time-2b53e2834033)
- [What is Prompt Chaining in AI? [2026 Tutorial]](https://www.voiceflow.com/blog/prompt-chaining)
- [The Complete Guide to Prompting and Prompt Chaining in AI - Metaflow AI](https://metaflow.life/blog/prompt-chaining)
- [APIs for AI Agents: The 5 Integration Patterns (2026 Guide) - Composio](https://composio.dev/blog/apis-ai-agents-integration-patterns)
- [How to Integrate Tools with AI Agents? Complete Implementation Strategy](https://zenvanriel.nl/ai-engineer-blog/how-to-integrate-tools-with-ai-agents-implementation-guide/)
- [Agent SDK](https://chatbotkit.com/manuals/agent-sdk)
- [AI Agent Tools : Tutorial & Examples](https://www.patronus.ai/ai-agent-development/ai-agent-tools)
- [AI Agent Tool Integration: Building Powerful Agents with Spring AI | by Mehmet ÖZ | Medium](https://medium.com/@mehhmetoz/ai-agent-tool-integration-building-powerful-agents-with-spring-ai-74e70a10e3bb)
- [Orchestrating Complex AI Workflows: Advanced Integration Patterns](https://www.getknit.dev/blog/orchestrating-complex-ai-workflows-advanced-integration-patterns)
- [Build Custom AI Agents With Logic & Control | n8n Automation Platform](https://n8n.io/ai-agents/)
- [Empowering AI Agents to Act: Mastering Tool Calling & Function Execution](https://www.getknit.dev/blog/empowering-ai-agents-to-act-mastering-tool-calling-function-execution)
- [ai-agents-for-beginners | 12 Lessons to Get Started Building AI Agents](https://microsoft.github.io/ai-agents-for-beginners/04-tool-use/)
- [AI agents in enterprises: Best practices with Amazon Bedrock AgentCore | Artificial Intelligence](https://aws.amazon.com/blogs/machine-learning/ai-agents-in-enterprises-best-practices-with-amazon-bedrock-agentcore/)
