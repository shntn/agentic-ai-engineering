# TechFlow API v3 リファレンス

## 認証

すべてのAPIリクエストは、以下の2つの方法のいずれかで認証が必要です:

### APIキー認証
`X-TechFlow-Key`ヘッダーにAPIキーを含めてください。APIキーはAdmin DashboardのSettings > API Keysから生成できます。各キーには設定可能なスコープ（read、write、admin）とオプションの有効期限があります。キーは64文字で、`tfk_`というプレフィックスが付きます。

```
X-TechFlow-Key: tfk_abc123...
```

### OAuth2認証
ユーザー向けアプリケーションでは、認可コードフローを使ったOAuth2を使用してください。developers.techflow.comでアプリケーションを登録すると`client_id`と`client_secret`を取得できます。認可エンドポイントは`https://auth.techflow.com/oauth2/authorize`、トークンエンドポイントは`https://auth.techflow.com/oauth2/token`です。アクセストークンは1時間で失効します。ユーザーの操作なしで新しいトークンを取得するにはリフレッシュトークンを使用してください。リフレッシュトークンは30日間有効です。

## レート制限

レート制限はAPIキーまたはOAuth2トークンごとに適用されます:

- **Basicプラン**: 100リクエスト/分、5,000リクエスト/日
- **Proプラン**: 500リクエスト/分、50,000リクエスト/日
- **Enterpriseプラン**: 2,000リクエスト/分、日次リクエスト無制限

レート制限に達すると、APIは待機すべき秒数を示す`Retry-After`ヘッダー付きのHTTP 429を返します。レート制限に関するヘッダーはすべてのレスポンスに含まれます: `X-RateLimit-Limit`、`X-RateLimit-Remaining`、`X-RateLimit-Reset`。

## ページネーション

リストエンドポイントは、カーソルベースのページネーションを使ってページ分割された結果を返します。各レスポンスには`next_cursor`フィールドが含まれます。これを`cursor`クエリパラメータとして渡すと次のページを取得できます。デフォルトのページサイズは25件、最大は100件です。ページサイズは`limit`クエリパラメータで設定できます。

```json
{
  "data": [...],
  "next_cursor": "eyJpZCI6MTAwfQ==",
  "has_more": true
}
```

## 主要エンドポイント

### Projects
- `GET /v3/projects` — 全プロジェクトを一覧表示。`status`フィルタ（active、archived、draft）と`sort`（created_at、updated_at、name）に対応。
- `POST /v3/projects` — プロジェクトを作成。必須フィールド: `name`（最大128文字）、`workspace_id`。オプション: `description`（最大2000文字）、`template_id`、`visibility`（private、team、public）。
- `GET /v3/projects/{id}` — メンバー数・タスク数・ストレージ使用量を含むプロジェクト詳細を取得。
- `PATCH /v3/projects/{id}` — プロジェクトのフィールドを更新。部分更新に対応。
- `DELETE /v3/projects/{id}` — プロジェクトをアーカイブする（論理削除）。アーカイブされたプロジェクトは90日間保持される。

### Tasks
- `GET /v3/projects/{id}/tasks` — タスクを一覧表示。フィルタに対応: `status`（todo、in_progress、review、done）、`assignee_id`、`priority`（low、medium、high、critical）、`due_before`、`due_after`、`label`。
- `POST /v3/projects/{id}/tasks` — タスクを作成。必須: `title`（最大256文字）。オプション: `description`（Markdown、最大10000文字）、`assignee_id`、`priority`、`due_date`、`labels[]`、`parent_task_id`。
- `GET /v3/tasks/{id}` — コメントと活動履歴を含む完全な詳細付きでタスクを取得。
- `PATCH /v3/tasks/{id}` — タスクを更新。すべてのフィールドがオプション。

### Users
- `GET /v3/users` — ワークスペースのメンバーを一覧表示。`role`フィルタ（owner、admin、member、guest）に対応。
- `GET /v3/users/{id}` — 役割・チーム・活動統計を含むユーザープロフィールを取得。
- `POST /v3/users/invite` — メールアドレスでユーザーを招待。必須: `email`、`role`。オプション: `team_ids[]`。

### Webhooks
- `POST /v3/webhooks` — Webhookを登録。必須: `url`（HTTPSのみ）、`events[]`。サポートされるイベント: `project.created`、`project.updated`、`task.created`、`task.updated`、`task.completed`、`member.added`、`member.removed`。
- `GET /v3/webhooks` — 配信統計付きで登録済みWebhookを一覧表示。
- `DELETE /v3/webhooks/{id}` — Webhookの登録を削除。

Webhookのペイロードは、Webhookシークレットを使ってHMAC-SHA256で署名されます。処理前に`X-TechFlow-Signature`ヘッダーを検証してください。配信に失敗した場合、指数バックオフで3回リトライされます（1分後、5分後、30分後）。

## エラーコード

| コード | 意味 | よくある原因 |
|------|---------|-------------|
| 400 | Bad Request | 不正なJSONまたは必須フィールドの欠落 |
| 401 | Unauthorized | APIキーが欠落・不正、またはトークンが期限切れ |
| 403 | Forbidden | スコープまたは権限が不足 |
| 404 | Not Found | リソースが存在しない、またはアーカイブ済み |
| 409 | Conflict | リソースの重複（例: プロジェクト名） |
| 422 | Unprocessable | JSONとしては有効だが意味的なエラーがある（例: 不正な日付） |
| 429 | Rate Limited | リクエストが多すぎる——Retry-Afterヘッダーを確認 |
| 500 | Server Error | 内部エラー——リクエストIDを添えてサポートに連絡 |

すべてのエラーレスポンスには、サポート追跡用の`request_id`と、人間が読める`message`フィールドが含まれます。

## バージョニング

このAPIはURLベースのバージョニング（`/v3/`）を使用しています。破壊的変更は新しいメジャーバージョンでのみ導入されます。非推奨のエンドポイントは、削除予定日を示す`Sunset`ヘッダーを返します。現在のバージョン（v3）は2024年1月にリリースされました。v2は2025年6月に廃止予定です。
