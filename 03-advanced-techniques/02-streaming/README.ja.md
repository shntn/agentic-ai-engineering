<!-- ---
title: "ストリーミングとリアルタイム出力"
description: "トークン単位のストリーミング応答と、ストリーム途中でのツール呼び出しの処理"
icon: "zap"
--- -->

# ストリーミングとリアルタイム出力

リアルタイムでトークン単位の応答を実現し、エージェントに生きた反応をさせます。これまでのチュートリアルはすべてブロッキングのAPI呼び出しを使っており、ユーザーは応答が全部届くまで沈黙の中で待つことになります。このチュートリアルではストリーミングを追加し、「固まった？」という体験を「考えていて、それが見える」という体験に変えます。

本当の難しさは基本的なストリーミングではなく、ツール呼び出しを伴うストリーミングにあります。Claudeが応答の途中でツールを呼び出すことを決めた場合、それを検知し、ツールを実行し、結果をフィードバックし、ストリーミングを再開する必要があります。このチュートリアルはそれを扱いやすくします。

## 🎯 学べること

- `client.messages.stream()`を使ってClaudeの応答をトークン単位でストリーミングする
- RichのLive表示を使ってターミナルにストリーミングMarkdownを描画する
- ストリーミングイベントの完全なライフサイクル（message_start → content_block_delta → message_stop）を理解する
- ストリーム途中のtool_useブロックを処理する——検知、実行、再開
- ツール呼び出しを伴う完全なストリーミングエージェントループを構築する
- ストリーミング時のトークン使用量を追跡する（usageはストリーム終了時に届く）

## 📦 利用可能なサンプル

| プロバイダー                                   | ファイル                                                     | 説明                                            |
| ---------------------------------------------- | ------------------------------------------------------------ | ----------------------------------------------- |
| ![Anthropic](../../.docs/badges/anthropic.svg) | [01_streaming_fundamentals.py](01_streaming_fundamentals.py) | テキストストリーミング + 複数ターンのチャット   |
| ![Anthropic](../../.docs/badges/anthropic.svg) | [02_streaming_agent.py](02_streaming_agent.py)               | ツール呼び出しを伴うストリーミングエージェント  |

## 🚀 クイックスタート

> **前提条件:** Python 3.11+、APIキー、uv。セットアップの詳細は [SETUP.md](../../SETUP.md) を参照してください。

```bash
uv run --directory 03-advanced-techniques/02-streaming python {script_name}

# まずは基礎から
uv run --directory 03-advanced-techniques/02-streaming python 01_streaming_fundamentals.py

# 次にストリーミングエージェントを試す
uv run --directory 03-advanced-techniques/02-streaming python 02_streaming_agent.py
```

または、[Code Runner](https://marketplace.visualstudio.com/items?itemName=formulahendry.code-runner) VS Code拡張機能を使えば、開いているスクリプトをワンクリックで実行できます。

## 🔑 キーコンセプト

### 1. ストリーミングの2つの方法

Anthropicは2つのストリーミング方式を提供しています。シンプルな方から始めて、制御が必要になったらイベントベースへ進みましょう。

**シンプル — `.text_stream`イテレーター:**

```python
with client.messages.stream(
    model="claude-sonnet-4-6",
    max_tokens=2048,
    messages=messages,
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)  # 各チャンクは数文字程度
```

これがストリーミングの最も簡単な方法です。イテレーターはプレーンなテキスト文字列——つまりコンテンツの差分だけを返します。イベントレベルの制御が不要なシンプルなユースケースに最適です。

**イベントベース — 完全なライフサイクル制御:**

```python
with client.messages.stream(...) as stream:
    for event in stream:
        if event.type == "content_block_start":
            # 新しいコンテンツブロック（テキストまたはtool_use）が開始
            pass
        elif event.type == "content_block_delta":
            if event.delta.type == "text_delta":
                print(event.delta.text, end="")
            elif event.delta.type == "input_json_delta":
                # ツール入力パラメータがストリーミングされている
                pass
        elif event.type == "content_block_stop":
            # ブロック完了
            pass
        elif event.type == "message_delta":
            # stop_reasonがここで利用可能になる
            print(f"\nStop reason: {event.delta.stop_reason}")
```

ツール呼び出しの検知、ブロック境界の追跡、カスタムレンダリングロジックの構築が必要な場合はイベントベースのイテレーションを使いましょう。

### 2. ストリーミングイベントのライフサイクル

すべてのストリームは次のシーケンスに従います:

```
message_start                          ← ストリーム開始
│
├─ content_block_start (index=0)       ← 最初のブロック（通常はテキスト）
│  ├─ content_block_delta              ← テキストチャンクが届く
│  ├─ content_block_delta              ← さらにテキスト
│  └─ content_block_stop              ← ブロック完了
│
├─ content_block_start (index=1)       ← 別のテキストまたはtool_useブロックの可能性
│  ├─ content_block_delta              ← テキストまたはinput_jsonの差分
│  └─ content_block_stop
│
├─ message_delta                       ← stop_reason + 最終的な使用量統計
└─ message_stop                        ← ストリーム完了
```

重要な洞察: 1つの応答には**複数のコンテンツブロック**が含まれることがあります——テキストとtool_useブロックが交互に並ぶこともあります。これがツールを使ったストリーミングを興味深いものにしています。

### 3. ツール呼び出しを伴うストリーミング

Claudeがツールを呼び出したいとき、ストリームには`tool_use`コンテンツブロックが含まれます。フローは次のようになります:

```
User: "東京の天気は？"
        │
        ▼
  ┌─ ストリーム開始 ─────────────────┐
  │ テキストブロック: "天気を確認します..."            │  ← ターミナルにストリーミングされる
  │ tool_useブロック: get_weather(city="Tokyo")        │  ← ストリーム途中で検知
  │ stop_reason: "tool_use"                            │
  └──────────────────────────┘
        │
        ▼ ツールを実行
  ┌─ ツール結果 ───────────────────┐
  │ {"city": "Tokyo", "temp_f": 58}                    │
  └──────────────────────────┘
        │
        ▼ 結果をフィードバックし、新しいストリームを開始
  ┌─ ストリーム再開 ─────────────────┐
  │ テキストブロック: "今日の東京は58°Fで晴れです。"  │  ← ターミナルにストリーミングされる
  │ stop_reason: "end_turn"                            │
  └──────────────────────────┘
```

エージェントループは各ストリームの後で`stop_reason`を確認します:
- `"end_turn"` → 完了、応答を返す
- `"tool_use"` → ツールを実行し、結果をフィードバックして再度ストリーミング
- `"max_tokens"` → 応答が途中で切れた

### 4. Rich Live表示によるレンダリング

生の`print()`はストリーミングテキストを表示できますが、ストリーム途中のMarkdown書式には対応できません。RichのLive表示はこれを解決します——更新のたびに蓄積された全体のMarkdownを再描画します:

```python
from rich.live import Live
from rich.markdown import Markdown

accumulated = ""
with Live(Markdown(""), refresh_per_second=15, console=console) as live:
    for text in stream.text_stream:
        accumulated += text
        live.update(Markdown(accumulated))
```

`refresh_per_second=15`パラメータは更新頻度を抑え、描画をスムーズに保ちます。ユーザーには、見出し・箇条書き・太字などが正しくレンダリングされながら、整形されたMarkdownがリアルタイムで組み上がっていく様子が見えます。

### 5. ストリーミング時のトークン追跡

トークン使用量はストリームが完了するまで利用できません。`get_final_message()`を使って取得します:

```python
with client.messages.stream(...) as stream:
    for text in stream.text_stream:
        print(text, end="")

    # usageはストリーム完了後に利用可能
    final_message = stream.get_final_message()
    token_tracker.track(final_message.usage)
    print(f"\nTokens: {final_message.usage.input_tokens} in, {final_message.usage.output_tokens} out")
```

`get_final_message()`は完全に蓄積された`Message`オブジェクトを返します——`client.messages.create()`が返すものと同じですが、先にストリーミングできる点が違います。

## 🏗️ コード構造

### スクリプト01 — ストリーミングの基礎

```python
class StreamingChat:
    """ストリーミング応答を伴うインタラクティブチャット。"""

    def stream_simple(self, user_input, console) -> str:
        """.text_streamを使ったストリーミング——簡単な方法。"""
        with client.messages.stream(...) as stream:
            for text in stream.text_stream:   # ただのテキスト文字列
                # Rich Liveで描画
            final = stream.get_final_message()
            # トークンを追跡

    def stream_with_events(self, user_input, console) -> str:
        """イベントベースのイテレーションによるストリーミング——完全な制御。"""
        with client.messages.stream(...) as stream:
            for event in stream:              # 型付きイベントオブジェクト
                if event.type == "content_block_delta":
                    # text_delta、input_json_deltaを処理
```

### スクリプト02 — ストリーミングエージェント

```python
class StreamingAgent:
    """ツール呼び出し処理を伴うストリーミングエージェント。"""

    def run(self, user_input, console) -> str:
        """エージェントループ: ストリーム → ツール検知 → 実行 → 再開。"""
        while True:
            response = self._stream_response(console)
            if response.stop_reason == "tool_use":
                results = self._execute_tool_calls(response.content, console)
                # 結果をフィードバックし、再びループ
            else:
                return extract_text(response)   # 完了

    def _stream_response(self, console) -> Message:
        """1回のAPI呼び出しをストリーミングし、テキスト+ツールインジケーターを描画する。"""
        with client.messages.stream(tools=TOOLS, ...) as stream:
            self._render_mixed_stream(stream, console)
            return stream.get_final_message()

    def _render_mixed_stream(self, stream, console) -> None:
        """核となるメソッド: テキストとtool_useブロックが交互に並ぶ状態を処理する。"""
        for event in stream:
            if event.type == "content_block_start":
                if event.content_block.type == "text":
                    # Rich Live表示を開始
                elif event.content_block.type == "tool_use":
                    # 「tool_nameを呼び出し中...」を表示
            elif event.type == "content_block_delta":
                if event.delta.type == "text_delta":
                    # ライブMarkdown表示を更新
```

## ⚠️ 重要な考慮事項

- **ストリーミングは総レイテンシを削減しない** — トークン数も処理時間も同じです。進捗をすぐに見せることで*体感の*レイテンシを削減します。
- **エラー処理** — ストリームは途中で失敗することがあります。常にtry/exceptで囲み、`APIError`を処理しましょう。ターミナルの表示崩れを避けるため、`Live`表示は`finally`ブロックで停止する必要があります。
- **`stop_reason`が重要** — 常に確認してください。`"tool_use"`はツールを実行して続行することを意味します。`"end_turn"`は完了を意味します。`"max_tokens"`は応答が途中で切れたことを意味します。
- **トークン追跡のタイミング** — 使用量統計は`get_final_message()`経由でストリームが完了した後にのみ届きます。ストリーム途中でトークンを追跡することはできません。
- **会話履歴** — ストリーミング後、メッセージ履歴のために完全な応答コンテンツが必要です。`get_final_message().content`を使って、コンテンツブロックの完全なリストを取得しましょう。

## 👉 次のステップ

ストリーミングをマスターしたら、次はこちらへ:
- **[コンテキストエンジニアリング](../03-context-engineering/)** — スライディングウィンドウと要約で有限のコンテキストウィンドウを管理する
- **実験** — ストリーミングエージェントにツールを追加し、1回の応答で複数のツール呼び出しを引き起こすプロンプトを試してみましょう
- **探求** — `stream.text_stream`とイベントイテレーションを切り替えて、制御とシンプルさの違いを確認してみましょう
