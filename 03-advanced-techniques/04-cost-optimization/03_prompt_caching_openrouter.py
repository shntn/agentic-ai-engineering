"""
プロンプトキャッシュ (OpenRouter)

大きな会社ポリシー文書をシステムプロンプトに持つカスタマーサポートエージェントで、
プロンプトキャッシュを実演します。ポリシーはキャッシュに必要な最小トークン数を
超えているため、繰り返しの呼び出しはキャッシュから読み込まれ大幅にコストが下がります。

【OpenRouter対応にあたっての注意】
DeepSeekのプロンプトキャッシュはAnthropicと異なり、Anthropicスタイルの
`cache_control`ブロックを明示的にマークする必要はなく「自動・設定不要」で動作する
（https://openrouter.ai/docs/guides/best-practices/prompt-caching 参照）。ただし
実機で検証したところ、`session_id`を指定せずにリクエストを送ると毎回別々の
アップストリームプロバイダーにルーティングされてしまい、キャッシュヒットが一切
発生しなかった。`session_id`を固定するとリクエストが同じプロバイダーに固定
ルーティングされ、2回目以降のリクエストで確実にキャッシュヒットする
（prompt_tokens_details.cached_tokensが増える）ことを確認済み。
そのためこのスクリプトでは、`cache_control`マーカーの代わりに`session_id`の
有無でキャッシュのON/OFFを切り替える。

1回目の呼び出し: キャッシュ MISS（cached_tokens == 0）
2回目以降の呼び出し: キャッシュ HIT（cached_tokens > 0）
"""

import os
import uuid
from dataclasses import dataclass, field
from typing import Any

from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from openrouter.components import ChatResult
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from common import OpenRouterTokenTracker, setup_logging

# ルートの.envファイルから環境変数を読み込む
load_dotenv(find_dotenv())

# ロギングを設定
logger = setup_logging(__name__)

# モデル設定
MODEL = "deepseek/deepseek-v4-flash-0731"

# deepseek/deepseek-v4-flash-0731 の実際の料金（$ per トークン）。
# client.models.list() で取得した実測値を基にしている（2026年8月時点）。
# input_cache_write は公開されていない——DeepSeekのキャッシュ書き込みは通常の
# input料金と同額で課金される仕様のため（OpenRouterドキュメント参照）。
PRICING = {
    "input": 0.000000045,
    "output": 0.00000009,
    "cache_write": 0.000000045,  # 通常のinputと同額
    "cache_read": 0.000000009,  # inputの約0.2倍
}

# 1024トークンを大きく超える会社ポリシー文書（約1500トークン）。
COMPANY_POLICY = """
# TechFlow Solutions — カスタマーサポートポリシー & FAQ

## 会社概要
TechFlow Solutionsは、クラウドベースのプロジェクト管理・チームコラボレーション・
ワークフロー自動化ツールを提供するB2B SaaS企業です。2019年設立、40か国15,000社
以上の企業に利用されています。製品ラインナップには TechFlow Pro（プロジェクト
管理）、TechFlow Connect（チームメッセージング）、TechFlow Automate
（ワークフロービルダー）があります。

## 返品・返金ポリシー

### ソフトウェアサブスクリプション
- すべてのサブスクリプションプランには、全機能を利用できる14日間の無料トライアル
  期間があります。
- 月額サブスクリプションはいつでも解約可能です。サービスは現在の請求サイクルの
  終了まで継続されます。未使用日数分の部分返金はありません。
- 年額サブスクリプションは購入から30日以内であれば返金可能です。30日を過ぎると、
  残額は12か月間有効なアカウントクレジットに変換されます。
- エンタープライズ契約（50席以上）はサービス契約書に定める個別条件に従います。
  変更についてはエンタープライズチームにお問い合わせください。

### ハードウェア・アクセサリー
- 物理製品（TechFlow Hubデバイス、アクセサリー）は、配送から30日以内であれば
  元の梱包状態で返品し全額返金を受けられます。
- 不良品のハードウェアは保証対象となり、無償で交換されます。
- 返品時の送料は、不良品の場合のみTechFlowが負担します。

## 配送・お届け

### デジタル製品
- ソフトウェアライセンスとサブスクリプションの有効化は、メールで即時提供されます。
- エンタープライズ導入には専任のオンボーディング担当者が付き、通常はフルセット
  アップまで5〜10営業日かかります。

### 物理製品
- 標準配送（5〜7営業日）: $50以上の注文は無料、それ以外は$7.99。
- 速達配送（2〜3営業日）: $14.99。
- 翌日配送（1営業日）: $24.99——米国内住所のみ利用可能。
- 国際配送（7〜14営業日）: 地域により$19.99〜$39.99。
- すべての配送に追跡番号が付きます。$200を超える注文には署名が必要です。

## 保証条件
- TechFlow Hubデバイス: 材料および製造上の欠陥をカバーする2年間のメーカー保証。
  物理的損傷・水濡れ・無許可の改造はカバーされません。
- ソフトウェア: ProおよびEnterpriseティアには99.9%稼働率SLAを保証。Basicティア
  にはSLAは含まれません。SLA基準を下回った時間ごとに、時間あたりコストの10倍の
  ダウンタイムクレジットが計算されます。

## アカウント管理

### プランティア
- **Basic**（$12/ユーザー/月）: コアのプロジェクト管理、5GBストレージ、
  メールサポート。
- **Pro**（$29/ユーザー/月）: 高度な分析、50GBストレージ、優先サポート、
  API アクセス、カスタム連携。
- **Enterprise**（$49/ユーザー/月）: 無制限ストレージ、専任アカウント
  マネージャー、SSO/SAML、監査ログ、カスタムSLA、電話サポート。

### アップグレード・ダウングレード
- アップグレードは即時反映されます。日割り差額分がすぐに請求されます。
- ダウングレードは次回の請求サイクルで反映されます。上位ティア専用の機能は
  それまで利用可能です。
- 下位ティアのストレージ上限を超えるデータは、ダウングレード処理前に
  エクスポートまたは削除する必要があります。7日前に自動警告が送信されます。

### 請求
- 利用可能な支払い方法: Visa、Mastercard、Amex、電信送金（Enterpriseのみ）。
- 年額プランの請求書は毎月1日に、月額プランの請求書はサブスクリプション更新日に
  発行されます。
- 支払い失敗は9日間で3回リトライされます。3回目の失敗後、アカウントは停止され
  ます。データは停止後30日間保持されます。

## よくある質問

1. **パスワードをリセットするには？**
   設定 > セキュリティ > パスワード変更に進むか、ログインページの
   「パスワードをお忘れですか」リンクを使用してください。登録済みメールアドレス
   にリセットリンクが送信されます。

2. **ライセンスを別のユーザーに譲渡できますか？**
   はい。管理者はチーム管理ダッシュボードから無料で席を再割り当てできます。
   再割り当てと同時に、以前のユーザーはアクセス権を失います。

3. **どんな連携が利用できますか？**
   ProおよびEnterpriseプランでは、Slack・Jira・GitHub・GitLab・Salesforce・
   HubSpot・Zapier、その他200以上のツールとAPIおよびZapierコネクタ経由で
   連携できます。

4. **データは暗号化されていますか？**
   はい。すべてのデータは保存時（AES-256）と通信時（TLS 1.3）に暗号化されます。
   Enterpriseプランでは顧客管理の暗号鍵（BYOK）を利用できます。

5. **解約した場合、データはどうなりますか？**
   解約後30日間データは保持されます。この期間中はいつでも設定 > データ
   エクスポートから全データをエクスポートできます。30日後、データ保持ポリシー
   に従い完全に削除されます。

6. **教育機関や非営利団体向けの割引はありますか？**
   はい。認定された教育機関および登録済み非営利団体は、全プランで40%割引を
   受けられます。有効な証明書類を添えて公式サイトから申請してください。

7. **サポートへの連絡方法は？**
   - Basic: メールサポート（24〜48時間で応答）
   - Pro: 優先メールサポート（4〜8時間で応答）+ ライブチャット
   - Enterprise: 専任アカウントマネージャー + 電話サポート（1時間応答SLA）

8. **購入前にデモを見ることはできますか？**
   はい。techflow.com/demo で個別デモを予約するか、クレジットカード不要で
   14日間の無料トライアルをすぐに開始できます。

9. **稼働率の保証はありますか？**
   ProおよびEnterpriseティアには99.9%稼働率SLAが含まれます。リアルタイムの
   ステータスは status.techflow.com で確認できます。

10. **大量ライセンスはどのように扱われますか？**
    50席以上の注文はボリュームディスカウント付きのEnterprise料金が適用されます。
    カスタム見積もりは sales@techflow.com までお問い合わせください。

## エスカレーション手順
- **Tier 1**（最前線の担当者）: 一般的な問い合わせ、パスワードリセット、
  請求に関する質問、標準的なトラブルシューティングに対応します。
- **Tier 2**（シニア担当者）: $500を超える返金依頼、アカウント停止、データ
  復旧、複雑な技術的問題に対応します。
- **Tier 3**（エンジニアリング）: サービス障害、セキュリティインシデント、
  APIのバグ、インフラの問題に対応します。
- 次のティアにエスカレーションする前に、必ず現在のティアで解決を試みて
  ください。次のティアに引き継ぐ前に、実施した手順と顧客とのやり取りを
  記録してください。
""".strip()

SYSTEM_INSTRUCTIONS = (
    "あなたはTechFlow Solutionsのカスタマーサポートエージェントです。以下の会社"
    "ポリシーを使って、顧客の質問に正確に答えてください。フレンドリーかつプロ"
    "フェッショナルに、簡潔に対応してください。ポリシーの範囲外の質問であれば、"
    "その旨を伝え、適切なチームへの問い合わせを提案してください。該当する場合は"
    "常に関連するポリシーのセクションを引用してください。"
)


@dataclass
class CacheMetrics:
    """API呼び出し全体でのキャッシュ性能を追跡する。"""

    call_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_write_tokens: int = 0
    total_cache_read_tokens: int = 0
    per_call_history: list[dict] = field(default_factory=list)

    def record_call(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_write_tokens: int,
        cache_read_tokens: int,
    ) -> None:
        """1回のAPI呼び出しの指標を記録する。"""
        self.call_count += 1
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cache_write_tokens += cache_write_tokens
        self.total_cache_read_tokens += cache_read_tokens
        self.per_call_history.append(
            {
                "call": self.call_count,
                "input": input_tokens,
                "output": output_tokens,
                "cache_write": cache_write_tokens,
                "cache_read": cache_read_tokens,
            }
        )

    def cost_with_caching(self) -> float:
        """キャッシュ料金を使った実際のコストを計算する。"""
        uncached_input = (
            self.total_input_tokens - self.total_cache_write_tokens - (self.total_cache_read_tokens)
        )
        return (
            uncached_input * PRICING["input"]
            + self.total_cache_write_tokens * PRICING["cache_write"]
            + self.total_cache_read_tokens * PRICING["cache_read"]
            + self.total_output_tokens * PRICING["output"]
        )

    def cost_without_caching(self) -> float:
        """すべてのトークンが通常のinput料金で課金されたと仮定した場合の
        コストを計算する。"""
        return (
            self.total_input_tokens * PRICING["input"]
            + self.total_output_tokens * PRICING["output"]
        )

    def savings(self) -> float:
        """キャッシュによるドル建ての節約額。"""
        return self.cost_without_caching() - self.cost_with_caching()

    def cache_hit_rate(self) -> float:
        """キャッシュ対象トークンのうち、キャッシュから提供された割合。"""
        total_cache = self.total_cache_write_tokens + self.total_cache_read_tokens
        if total_cache == 0:
            return 0.0
        return (self.total_cache_read_tokens / total_cache) * 100


class CachedSupportAgent:
    """プロンプトキャッシュを実演するカスタマーサポートエージェント。"""

    def __init__(
        self,
        model: str,
        token_tracker: OpenRouterTokenTracker,
        use_cache: bool = True,
    ):
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.token_tracker = token_tracker
        self.use_cache = use_cache
        self.messages: list[dict[str, Any]] = []
        self.metrics = CacheMetrics()
        # 同じセッションのリクエストを同じアップストリームプロバイダーに固定
        # ルーティングするためのID。これがないと毎回別のプロバイダーに
        # ルーティングされ、キャッシュヒットが発生しない（実機検証済み）。
        self.session_id = f"prompt-caching-demo-{uuid.uuid4().hex[:8]}"

    def chat(self, user_input: str) -> tuple[str, dict]:
        """メッセージを送信し、キャッシュ指標を記録し、(応答, usage_dict) を返す。"""
        self.messages.append({"role": "user", "content": user_input})

        kwargs: dict[str, Any] = {}
        if self.use_cache:
            kwargs["session_id"] = self.session_id

        try:
            response: ChatResult = self.client.chat.send(  # type: ignore[call-overload]
                model=self.model,
                max_tokens=1024,
                reasoning={"effort": "none"},
                messages=[
                    {"role": "system", "content": f"{SYSTEM_INSTRUCTIONS}\n\n{COMPANY_POLICY}"},
                    *self.messages,
                ],
                **kwargs,
            )
        except Exception:
            self.messages.pop()
            raise

        assert response.usage is not None
        self.token_tracker.track(response.usage)

        # usageからキャッシュ指標を抽出する
        input_tokens = response.usage.prompt_tokens
        output_tokens = response.usage.completion_tokens
        details = response.usage.prompt_tokens_details
        cache_write = getattr(details, "cache_write_tokens", None) or 0
        cache_read = getattr(details, "cached_tokens", None) or 0

        self.metrics.record_call(input_tokens, output_tokens, cache_write, cache_read)

        usage_dict = {
            "input": input_tokens,
            "output": output_tokens,
            "cache_write": cache_write,
            "cache_read": cache_read,
        }

        logger.info(
            "Call %d — input: %d, output: %d, cache_write: %d, cache_read: %d",
            self.metrics.call_count,
            input_tokens,
            output_tokens,
            cache_write,
            cache_read,
        )

        assistant_message = str(response.choices[0].message.content or "")
        self.messages.append({"role": "assistant", "content": assistant_message})

        return assistant_message, usage_dict


def _render_call_metrics(console: Console, call_num: int, usage: dict) -> None:
    """1回の呼び出しごとのキャッシュ指標を描画する。"""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Metric", style="dim")
    table.add_column("Value", justify="right")

    table.add_row("Input tokens", f"[cyan]{usage['input']:,}[/cyan]")
    table.add_row("Output tokens", f"[cyan]{usage['output']:,}[/cyan]")

    # キャッシュの挙動を強調表示する
    if usage["cache_write"] > 0:
        table.add_row(
            "Cache write",
            f"[yellow]{usage['cache_write']:,}[/yellow] [dim](通常のinputと同額——初回呼び出しでキャッシュを作成)[/dim]",
        )
    if usage["cache_read"] > 0:
        table.add_row(
            "Cache read",
            f"[green]{usage['cache_read']:,}[/green] [dim](inputの約0.2倍——約80%の節約！)[/dim]",
        )
    if usage["cache_write"] == 0 and usage["cache_read"] == 0:
        table.add_row("Cache", "[dim]no cacheable content[/dim]")

    console.print(Panel(table, title=f"Call {call_num}", border_style="dim", padding=(0, 1)))


def _render_savings_summary(console: Console, metrics: CacheMetrics) -> None:
    """累積のコスト比較を描画する。"""
    cost_cached = metrics.cost_with_caching()
    cost_baseline = metrics.cost_without_caching()
    savings = metrics.savings()
    savings_pct = (savings / cost_baseline * 100) if cost_baseline > 0 else 0

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Metric", style="dim", min_width=20)
    table.add_column("Value", justify="right")

    table.add_row("Cost without caching", f"[red]${cost_baseline:.6f}[/red]")
    table.add_row("Cost with caching", f"[green]${cost_cached:.6f}[/green]")
    table.add_row("Savings", f"[bold green]${savings:.6f} ({savings_pct:.1f}%)[/bold green]")
    table.add_row("Cache hit rate", f"[cyan]{metrics.cache_hit_rate():.1f}%[/cyan]")
    table.add_row("Total calls", f"[cyan]{metrics.call_count}[/cyan]")

    console.print(
        Panel(
            table,
            title="Cumulative Savings",
            border_style="green" if savings > 0 else "dim",
            padding=(0, 1),
        )
    )


def main() -> None:
    """プロンプトキャッシュのデモ用メインオーケストレーション関数。"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()
    agent = CachedSupportAgent(MODEL, token_tracker)

    console.print(
        Panel(
            "[bold cyan]Prompt Caching Demo[/bold cyan]\n\n"
            "このカスタマーサポートエージェントは、大きな会社ポリシー文書（約1500トークン）\n"
            "をシステムプロンプトとして持ち、OpenRouter経由のプロンプトキャッシュで\n"
            "キャッシュされます。\n\n"
            "[bold]仕組み:[/bold]\n"
            "  1. 初回呼び出し: キャッシュ MISS——ポリシーがキャッシュに書き込まれる\n"
            "     （通常のinputと同額）\n"
            "  2. 以降の呼び出し: キャッシュ HIT——ポリシーがキャッシュから読み込まれる\n"
            "     （約0.2倍のコスト）\n"
            "  3. 同一session_idのリクエストが同じプロバイダーに固定ルーティングされる\n"
            "     ことでキャッシュヒットが成立する\n\n"
            "TechFlow Solutionsについてサポートの質問をして、節約額が増えていく様子を"
            "見てください。\n"
            "[bold]'quit'[/bold] または [bold]'exit'[/bold] と入力すると終了します。\n\n"
            "[bold]サンプルの質問:[/bold]\n"
            "  1. TechFlow Solutionsとはどんな会社ですか？\n"
            "  2. 年間サブスクリプションの返品ポリシーは？\n"
            "  3. 配送にはどれくらいかかりますか？\n"
            "  4. どんなプランティアがあり、料金はいくらですか？\n"
            "  5. 非営利団体向けの割引はありますか？",
            title="TechFlow Support",
        )
    )

    while True:
        console.print("\n[bold green]You:[/bold green] ", end="")
        user_input = input().strip()

        if user_input.lower() in ["quit", "exit", ""]:
            console.print("\n[yellow]Ending session...[/yellow]")
            break

        try:
            response, usage = agent.chat(user_input)

            console.print("\n[bold blue]Support Agent:[/bold blue]")
            console.print(Markdown(response))

            # 呼び出しごとのキャッシュ内訳
            console.print()
            _render_call_metrics(console, agent.metrics.call_count, usage)

            # 累積の節約額（2回目以降で意味を持つ）
            if agent.metrics.call_count >= 2:
                _render_savings_summary(console, agent.metrics)

        except Exception as e:
            logger.error("Error during chat: %s", e)
            console.print(f"\n[red]Error: {e}[/red]")
            break

    # 最終レポート
    console.print()
    token_tracker.report()

    if agent.metrics.call_count >= 2:
        console.print()
        _render_savings_summary(console, agent.metrics)


if __name__ == "__main__":
    main()
