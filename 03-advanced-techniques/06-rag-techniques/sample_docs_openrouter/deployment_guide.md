# TechFlowデプロイガイド

## CI/CDパイプライン

TechFlowは継続的インテグレーション・デプロイにGitHub Actionsを使用しています。フィーチャーブランチへのすべてのプッシュがCIパイプラインをトリガーします。`main`へのマージはステージングへのデプロイをトリガーします。本番環境へのデプロイには手動承認が必要です。

### パイプラインのステージ

1. **Lint & Format** — すべてのPythonサービスに対してruffとblackを実行。違反が見つかるとビルドが失敗する。Node.jsサービスにはESLintを使用。
2. **Unit Tests** — カバレッジレポート付きでpytestを実行。最低カバレッジ閾値: 80%。テストは4ワーカーで並列実行される。
3. **Integration Tests** — Dockerコンテナ内でPostgreSQL・Redis・Kafkaを起動。サービススタック全体に対してエンドツーエンドのAPIテストを実行。タイムアウト: 15分。
4. **Security Scan** — 依存関係の脆弱性にはSnyk、Pythonのセキュリティ問題にはBanditを実行。重大な脆弱性はビルドをブロックする。
5. **Build & Push** — DockerイメージをビルドしてAWS ECRにプッシュ。イメージにはgitのSHAと、mainブランチには`latest`タグが付く。
6. **Deploy to Staging** — `main`ブランチでは自動実行。ECSタスク定義を更新し、ローリングデプロイをトリガーする。
7. **Deploy to Production** — チームリードによる手動承認が必要。Blue-Greenデプロイ戦略を使用。

### ビルド時間
- 平均CIパイプライン: 8分
- 平均デプロイ時間（ステージング）: 4分
- 平均デプロイ時間（本番）: 6分（Blue-Green切り替えを含む）

## 環境設定

### 環境変数
各サービスは、AWS Systems Manager Parameter Store経由で管理される環境変数から設定を読み込みます。環境ごとの値（ステージング vs 本番）は別々のパスに保存されます:

```
/techflow/staging/auth-service/DATABASE_URL
/techflow/production/auth-service/DATABASE_URL
```

### 必須環境変数（全サービス共通）
- `ENVIRONMENT` — `staging`または`production`
- `LOG_LEVEL` — `DEBUG`、`INFO`、`WARNING`、`ERROR`（デフォルト: `INFO`）
- `KAFKA_BOOTSTRAP_SERVERS` — カンマ区切りのKafkaブローカーアドレス
- `REDIS_URL` — 認証情報付きのRedis接続文字列
- `SENTRY_DSN` — エラートラッキングのエンドポイント

### サービス固有の変数
- **Auth Service**: `DATABASE_URL`、`JWT_SECRET_KEY`、`OAUTH2_CLIENT_IDS`、`SESSION_TTL_SECONDS`
- **Project Service**: `DATABASE_URL`、`READ_REPLICA_URL`、`PGBOUNCER_MAX_CONNECTIONS`
- **Notification Service**: `SES_REGION`、`FCM_CREDENTIALS`、`WEBHOOK_SIGNING_SECRET`
- **File Service**: `S3_BUCKET`、`CLOUDFRONT_DOMAIN`、`MAX_UPLOAD_SIZE_MB`、`CLAMAV_HOST`

## データベースマイグレーション

データベースマイグレーションはAlembic（Pythonサービス）で管理され、デプロイ時に自動適用されます。

### マイグレーションのプロセス
1. 開発者がマイグレーションを作成: `alembic revision --autogenerate -m "add_labels_table"`
2. PRでマイグレーションをレビュー（すべてのマイグレーションは後方互換である必要がある）
3. デプロイ時、ECSタスクのinitコンテナがサービス起動前に`alembic upgrade head`を実行
4. マイグレーションが失敗した場合、デプロイは自動的にロールバックされる

### マイグレーションのルール
- **常に後方互換であること**: ローリングデプロイ中、古いコードが新しいスキーマで動作しなければならない
- **同一リリースでカラムを削除しない**: 最初のリリースでコードの参照を削除し、次のリリースでカラムを削除する
- **インデックスは並行して追加する**: テーブルロックを避けるため`CREATE INDEX CONCURRENTLY`を使用する
- **大規模なデータマイグレーション**: マイグレーションスクリプト内ではなく、バックグラウンドジョブとして実行する（デプロイタイムアウトを避けるため）
- **マイグレーションのタイムアウト**: 1マイグレーションあたり60秒。これを超えるとマイグレーションは強制終了され、デプロイは失敗する。

## ロールバック手順

### 自動ロールバック
ECSはデプロイ中にコンテナのヘルスチェックを監視します。新しいコンテナが5分以内にヘルスチェックに失敗した場合、デプロイは自動的に前回のタスク定義にロールバックされます。ヘルスチェックエンドポイント: `GET /health`が3秒以内に200を返す必要があります。

### 手動ロールバック
本番デプロイを手動でロールバックするには:

1. AWS Console > ECS > Cluster > Serviceに移動
2. 「Update service」をクリック
3. 前回のタスク定義リビジョンを選択
4. 「Force new deployment」にチェック
5. 「Update」をクリック

またはCLI経由:
```bash
aws ecs update-service --cluster techflow-prod \
  --service api-gateway \
  --task-definition api-gateway:42 \
  --force-new-deployment
```

ロールバックは通常3〜4分で完了します。前回のDockerイメージは常にECRで利用可能です（イメージは90日間保持されます）。

### データベースのロールバック
データベースマイグレーションを元に戻す必要がある場合:
```bash
alembic downgrade -1  # 1リビジョン分ロールバック
```
これは、マイグレーションに適切な`downgrade()`関数がある場合にのみ機能します。すべてのマイグレーションにはdowngradeステップを含める必要があります。

## ヘルスチェック

各サービスは2つのヘルスエンドポイントを公開しています:

- `GET /health` — 基本的な生存確認。プロセスが動作していれば200を返す。ECSがコンテナのヘルス監視に使用。
- `GET /health/ready` — 準備状態確認。データベース接続・Redis接続・Kafkaコンシューマーグループの状態を検証する。サービスがリクエストを処理できる場合のみ200を返す。ロードバランサーがトラフィックのルーティングに使用。

ヘルスチェックの間隔: ECSは`/health`を30秒ごとにチェックします。ロードバランサーは`/health/ready`を10秒ごとにチェックします。準備確認が3回連続で失敗すると、サービスはロードバランサーから除外されます。

## スケーリングポリシー

### オートスケーリング構成
各サービスには独立したオートスケーリングルールがあります:

| サービス | 最小 | 最大 | スケールアップの条件 | スケールダウンの条件 |
|---------|-----|-----|-----------------|-------------------|
| API Gateway | 4 | 12 | CPU > 60%が3分間 | CPU < 30%が10分間 |
| Auth Service | 3 | 8 | CPU > 70%が3分間 | CPU < 30%が10分間 |
| Project Service | 4 | 16 | CPU > 65%が3分間 | CPU < 25%が15分間 |
| Notification Service | 2 | 6 | キューの深さ > 10,000 | キューの深さ < 1,000 |
| Search Service | 2 | 8 | CPU > 70%が3分間 | CPU < 30%が10分間 |
| File Service | 2 | 6 | CPU > 70%が5分間 | CPU < 30%が15分間 |

### ピーク時間帯
トラフィックは平日の東部標準時（EST）午前9:00〜11:00と午後2:00〜4:00にピークを迎えます。コールドスタートによるレイテンシを避けるため、EST午前8:45に各サービスを最大容量の75%までプリスケーリングするよう設定されています。

## モニタリング

### Prometheus & Grafana
すべてのサービスは`/metrics`エンドポイント（Prometheus形式）でメトリクスを公開しています。主要なダッシュボード:
- **Service Health**: リクエストレート・エラーレート・レイテンシパーセンタイル（p50、p95、p99）
- **Database**: クエリレイテンシ・接続プール使用率・レプリケーションラグ
- **Kafka**: コンシューマーラグ・パーティション分布・メッセージスループット
- **Business Metrics**: アクティブユーザー数・作成されたタスク数・エンドポイント別のAPI呼び出し数

### アラートルール
- **P1（即座にページング）**: エラーレート > 5%が2分間、サービスの完全停止、データベースのレプリケーションラグ > 30秒
- **P2（Slackアラート）**: エラーレート > 1%が5分間、p95レイテンシ > 500ms、ディスク使用率 > 80%
- **P3（チケット）**: p95レイテンシ > 200ms、メモリ使用率 > 70%、証明書の有効期限が14日以内

### インシデント対応
1. **検知**: PagerDuty（P1）またはSlack（P2/P3）による自動アラート
2. **トリアージ**: オンコールエンジニアが5分以内に重大度と影響範囲を評価
3. **周知**: P1インシデントでは10分以内にstatus.techflow.comのステータスページを更新
4. **解決**: 修正を適用し、監視で復旧を確認
5. **ポストモーテム**: すべてのP1・P2インシデントについて48時間以内に文書化。タイムライン・根本原因・アクションアイテムを含む。
