# CLAUDE.ja-openrouter.md — Agent Rules for AI Agents Engineering (OpenRouter移植プロジェクト)

このリポジトリは https://github.com/agenticloops-ai/agentic-ai-engineering の学習教材を
日本語化しつつ、Anthropic/OpenAI直叩きのコードをOpenRouter経由に移植する個人プロジェクトです。

## 命名規則

- 元ファイル: `NN_xxx_anthropic.py` または `NN_xxx_openai.py`
- 移植先: 元ファイルをコピーし、`NN_xxx_openrouter.py` として保存（連番は既存ファイルの続き番号）
- 元ファイルの構成・関数分割・ロジックの骨格はできる限り保持し、差分を最小化する

## 翻訳ルール

- コメント・ユーザー向け表示メッセージ（rich の Panel タイトル、CLIメニュー文言など）は日本語化する
- **エラーメッセージ（例外の文言、`logger.error` の内容）は英語のまま**でよい
- 変数名・関数名・クラス名は英語のまま（日本語化しない）
- 訳のトーン: 漢字・カタカナ半々程度。専門用語（Few-shot, Chain-of-Thought, プロンプトなど）はカタカナ優先、業務寄りの語（バグ、機能要望など）は和語混在可
- 見出しの `&` や `+` などの記号は元の表記を維持してよい（無理に「・」等に置き換えない）
- 既存の日本語化済みファイル（`07_system_prompts_openrouter.py`, `08_few_shot_cot_openrouter.py` など）との訳語の一貫性を優先する

## READMEの翻訳ルール

- 元ファイル: `README.md`
- 翻訳先: 同じディレクトリに `README.ja.md` としてコピーして翻訳する（`README.md` 自体は書き換えない）
- frontmatter（`title` / `description`）を含め、見出し・本文・表の中身・箇条書きはすべて日本語化する
- 表の列見出し（例: `Provider` → `プロバイダー`、`File` → `ファイル`、`Description` → `説明`）も翻訳する
- Mermaid図はノードラベルの文言のみ翻訳し、絵文字・矢印・レイアウト（`config`/`flowchart`定義など）はそのまま維持する
- 説明用コードスニペット内のコメント・docstringは日本語化してよいが、コードとして実行される部分
  （関数名・変数名・API呼び出し・文字列リテラルの値など）は変更しない
- ファイル名・相対リンク・バッジ画像パス・プロバイダー名（Anthropic/OpenAI等）は翻訳しない
- 翻訳のトーン・訳語の一貫性は「翻訳ルール」節の方針（漢字/カタカナのバランス、既存の日本語化済みファイルとの整合性）を踏襲する

## OpenRouter移植ルール

### API呼び出しの変換

| Anthropic版 | OpenAI版 | OpenRouter版 |
|---|---|---|
| `Anthropic()` | `OpenAI()` | `OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))` — **api_keyは必ず明示的に渡す**（省略すると空文字が送られ401 "User not found"になる） |
| `client.messages.create(system=..., messages=[...])` | `client.responses.create(instructions=..., input=...)` | `client.chat.send(messages=[{"role":"system",...},{"role":"user",...}])`（Chat Completions形式。Responses APIは非対応） |
| `response.content[0].text` | `response.output_text` | `response.choices[0].message.content` |
| `response.usage.input_tokens` / `.output_tokens` | `response.usage.input_tokens` / `.output_tokens` | `response.usage.prompt_tokens` / `.completion_tokens` |

### ツール呼び出しループの変換（tool_use系）
- assistant のツール呼び出し: Anthropicは content配列内にtool_useブロック混在 →
  OpenRouterは response.choices[0].message をそのまま履歴に積む（tool_callsフィールド込み）
- ツール結果の返却: Anthropicは role="user" + content配列(tool_result) →
  OpenRouterは role="tool" + tool_call_id を使った専用メッセージ（tool_callごとに1メッセージ）
- 終了判定: stop_reason=="end_turn" → finish_reason=="stop"
- ツール定義: Anthropicのinput_schema形式 → OpenAIのfunction calling形式{"type":"function","function":{"name":,"parameters":}}にラップ
- tool_use系のレッスンでツール定義変換（Anthropic input_schema → OpenAI function calling形式）が
  必要な場合、変換ヘルパーは教材オリジナルのディレクトリ（`tools/`等）には混ぜず、
  レッスン直下に独立ファイル（例: `openrouter_adapter.py`）として配置する
- 強制ツール選択: Anthropicの `{"type": "tool", "name": "X"}` →
  OpenRouterの `{"type": "function", "function": {"name": "X"}}`
- Web検索の組み込みツール: Anthropicの
  `{"type": "web_search_20250305", "name": "web_search", "max_uses": N}` →
  OpenRouterの `{"type": "web_search", "max_uses": N}`（OpenAI Responses API互換の
  ショートハンド形式。サーバー側で実行されるため、Anthropic版のようなクライアント側での
  tool_use/tool_result往復は不要）

### 構造化出力（response_format）

- OpenAI版の `text={"format": {"type": "json_schema", "name": ..., "schema": ..., "strict": ...}}` は
  OpenRouterでは1段ネストが深くなる:
```python
  response_format={
      "type": "json_schema",
      "json_schema": {"name": ..., "strict": True, "schema": {...}},
  }
```
- `response_format` は `text` / `json_object` / `json_schema` / `grammar` / `python` をサポート。
  モデルが `json_schema`（特に `strict: true`）に対応しているかは
  `curl https://openrouter.ai/api/v1/models | jq '.data[] | select(.id=="<model>") | .supported_parameters'`
  で確認できる。未対応の場合は `provider: {require_parameters: true}` を使う。

### Scaffolding（構造化プロンプト）

- Anthropic版でXMLタグ（`<schema>...</schema>`）を使っている箇所は、
  OpenRouter版ではMarkdown見出し（`## Schema` など）に置き換える
  （XMLタグの効きはClaude固有の学習傾向によるもので、非Claudeモデルには弱い）

### mypy対策

- openrouterパッケージの型スタブは `chat.send()` の `messages` 引数に厳密なUnion型
  （TypedDict群）を要求するが、プレーンな `dict` リテラルのリストはこの型に一致せず
  `call-overload`（`**kwargs`併用時）または `arg-type`（併用しない時）エラーになる
- `chat.send(` の行末に `# type: ignore[call-overload]`（または `[arg-type]`）を付ける。
  実行時の挙動には影響しない、型スタブの厳密さに起因する既知の問題
- `_call_llm` のようなラッパーメソッドが `ChatResult` を返す場合、
  `response: ChatResult = self.client.chat.send(...)` のように変数へ明示的に型注釈を
  付けないと、`# type: ignore` の影響で戻り値が `Any` 扱いになり
  `no-any-return` エラーが出ることがある

### reasoning / 思考モデル対策

- `openrouter/free` はランダムに無料モデルを選ぶルーターで、思考モデル（reasoning）に当たると
  `reasoning` フィールドが `max_tokens` を消費し、`content: null` で切れることがある
- デフォルトモデルは `deepseek/deepseek-v4-flash` を使用し、`openrouter/free` は使わない
- CoT系のデモなど出力が長くなるタスクは `max_tokens` を1000以上に設定する
  （200では思考なしモデルでも本文が途中で切れることがある）
- 必要に応じて `reasoning={"effort": "none"}` を付けて隠れ思考を抑制する

### タイムアウト・リトライ対策（Web検索など時間のかかるツール使用時）

- openrouter SDK（openrouter/utils/retries.py）は、httpx.TimeoutException を
  リトライ対象として扱う。max_elapsed_time のデフォルトは 3600000ms（1時間）で、
  タイムアウトが起き続けると理論上その間ずっとリトライし続ける
- OpenRouterのweb_searchサーバーツールは応答に数十秒かかることがあり、
  SDKのデフォルトタイムアウト（httpxの初期値）ではこれより短く設定されているため、
  正常な応答でもタイムアウト→リトライのループに陥りやすい
- OpenRouter(api_key=..., timeout_ms=120_000) のように、クライアント初期化時に
  タイムアウトを明示的に伸ばす（Web検索を使う場合は最低120秒を目安に）

## プロジェクト構成の注意点

- 各レッスンディレクトリ（`NN-xxx/`）は独立した uv プロジェクト（自前の `pyproject.toml`）。
  ルートで `uv add <pkg>` してもレッスン側には反映されないので、
  新しい依存（`openrouter` など）はレッスンディレクトリ側でも `uv add --directory <dir> <pkg>` する
- `common/` パッケージ（`setup_logging`, `interactive_menu`, `OpenRouterTokenTracker` など）は
  横断的関心事のみを共通化する方針。LLM呼び出しクラス自体（`LLMClient`/`PromptEngineer`等）は
  各レッスンで反復させる元リポジトリの設計を踏襲し、安易に共通化しない
  （ただし同じ形が3回以上続く場合は `common/` への切り出しを検討する）

## 実行方法

```bash
orb -m dev uv run --directory <lesson-dir> python <script>.py
```

例:

```
orb -m dev uv run --directory 01-foundations/04-tool-use python 03_tool_use_openrouter.py
```

## コミット

### コミットメッセージのルール

- [Conventional Commits](https://www.conventionalcommits.org/) に準拠する
- `type`（`feat:`, `fix:`, `docs:`, `style:`, `refactor:`, `test:`, `chore:` 等）と `scope` は英語
  - `style:` はロジックを変更しない整形のみの変更（`ruff format`適用など）に使う
- `description`（1行目の要約）と、任意の `body`（詳細説明）は日本語
- `scope` は以下のいずれか
  - レッスンディレクトリ名（例: `01-simple-llm-call`, `06-codebase-navigator`。親パスの `01-foundations/` は含めない）
  - 1つのモジュール内の複数レッスンにまたがる変更は、モジュール名（例: `01-foundations`）
  - リポジトリ横断的な変更は `docs` / `common` / `fork` のような抽象カテゴリ
- `body` は説明文の段落ではなく、**変更したファイル・ディレクトリ単位の箇条書き**にする

`NN_xxx_openrouter.py` 追加コミットの典型的な形:
```
feat(<lesson>): OpenRouter対応版を追加

- <script>.py を追加
- （必要な場合）変換ヘルパー・日本語化ディレクトリ・サンプル入力等を追加
- openrouter パッケージを依存関係に追加
- fork.ja.md の進捗表を更新（<lesson>: 完了）
```

### コミットの粒度

- OpenRouter対応スクリプトの追加（`NN_xxx_openrouter.py`、依存関係の更新含む）は `feat` として、
  レッスン単位で1コミットにまとめる
- README.ja.md の翻訳は `docs` として、モジュール（`01-foundations`等）配下の
  全レッスン分をまとめて1コミットにする（モジュール全体の README.ja.md も同じコミットに含める）
- fork.ja.md の進捗表更新は、該当レッスンの feat コミットに含める