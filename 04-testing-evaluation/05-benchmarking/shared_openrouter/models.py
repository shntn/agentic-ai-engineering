"""
ベンチマークスクリプト向けの共有データクラスとモデル設定（OpenRouter）。

ModelConfig・BenchmarkResult・BenchmarkConfig・デフォルトのモデル設定を定義する。
"""

from dataclasses import dataclass


@dataclass
class ModelConfig:
    """ベンチマーク対象のモデルの設定。

    OpenRouterでは全モデルが同じAPIでアクセスできるため、元のコードにあった
    provider（"anthropic"/"openai"）フィールドは不要。
    """

    name: str
    model_id: str
    cost_per_input_token: float  # 100万トークンあたりのドル
    cost_per_output_token: float  # 100万トークンあたりのドル


@dataclass
class BenchmarkResult:
    """1つのベンチマークタスクを実行した結果。"""

    task_id: str
    config_name: str
    answer: str
    keyword_score: float  # 見つかった期待キーワードに基づく0.0〜1.0のスコア
    latency_ms: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    tool_calls: int


@dataclass
class BenchmarkConfig:
    """単一のベンチマーク設定（モデル + プロンプトの組み合わせ）。"""

    name: str
    model: ModelConfig
    prompt_strategy: str
    system_prompt: str


# デフォルトのモデル設定
# 価格は client.models.list() で取得した実測値（2026年8月時点）
MODEL_CONFIGS = [
    ModelConfig("DeepSeek V4 Pro", "deepseek/deepseek-v4-pro-0813", 0.66, 1.98),
    ModelConfig("DeepSeek V4 Flash", "deepseek/deepseek-v4-flash-0731", 0.065, 0.18),
    ModelConfig("GLM 5.3 Flash", "z-ai/glm-5.3-flash", 0.075, 0.25),
]
