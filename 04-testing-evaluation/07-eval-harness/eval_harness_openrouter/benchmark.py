"""評価ハーネス向けのパレート分析付きモデルベンチマーク（OpenRouter）。"""

import logging
import random
from typing import Any

from eval_harness_openrouter.models import BenchmarkEntry, EvalTask

logger = logging.getLogger(__name__)

# 現実的な性能特性を持つシミュレートされたモデル設定
# コスト（cost_per_1k_input/output）は05-benchmarkingのMODEL_CONFIGSと同じ実測値
# （client.models.list()、2026年8月時点、1Mトークンあたりのドルを1000で割った値）
DEFAULT_CONFIGS: list[dict[str, Any]] = [
    {
        "name": "deepseek-v4-flash",
        "model": "deepseek/deepseek-v4-flash-0731",
        "avg_latency_ms": 600.0,
        "cost_per_1k_input": 0.000065,
        "cost_per_1k_output": 0.00018,
        "accuracy_modifier": 0.75,
    },
    {
        "name": "glm-5.3-flash",
        "model": "z-ai/glm-5.3-flash",
        "avg_latency_ms": 1200.0,
        "cost_per_1k_input": 0.000075,
        "cost_per_1k_output": 0.00025,
        "accuracy_modifier": 0.85,
    },
    {
        "name": "deepseek-v4-pro",
        "model": "deepseek/deepseek-v4-pro-0813",
        "avg_latency_ms": 2500.0,
        "cost_per_1k_input": 0.00066,
        "cost_per_1k_output": 0.00198,
        "accuracy_modifier": 0.93,
    },
]


class BenchmarkRunner:
    """複数のモデル設定にわたってベンチマークを実行する。"""

    def __init__(self, configs: list[dict[str, Any]] | None = None) -> None:
        self.configs = configs or DEFAULT_CONFIGS

    def run_benchmark(
        self, tasks: list[EvalTask], configs: list[dict[str, Any]] | None = None
    ) -> list[BenchmarkEntry]:
        """複数のモデル設定とタスクにわたってシミュレートされたベンチマークを実行する。"""
        configs = configs or self.configs
        entries: list[BenchmarkEntry] = []

        for config in configs:
            logger.info("Benchmarking config: %s", config["name"])
            for task in tasks:
                entry = self._simulate_benchmark(task, config)
                entries.append(entry)

        logger.info(
            "Benchmark complete: %d entries across %d configs",
            len(entries),
            len(configs),
        )
        return entries

    def _simulate_benchmark(self, task: EvalTask, config: dict[str, Any]) -> BenchmarkEntry:
        """1つのタスク+設定の組み合わせについてベンチマーク実行をシミュレートする。"""
        # ばらつきを持たせてレイテンシをシミュレートする
        base_latency = config["avg_latency_ms"]
        latency = base_latency + random.uniform(-base_latency * 0.2, base_latency * 0.2)

        # タスクの難易度とモデルの能力に基づいて正確性をシミュレートする
        difficulty_modifier = {"easy": 1.0, "medium": 0.85, "hard": 0.7}.get(task.difficulty, 0.85)
        accuracy = min(1.0, config["accuracy_modifier"] * difficulty_modifier)

        # トークン使用量をシミュレートする
        input_tokens = random.randint(200, 400)
        output_tokens = random.randint(80, 200)
        total_tokens = input_tokens + output_tokens

        # コストを計算する
        cost = (
            input_tokens / 1000 * config["cost_per_1k_input"]
            + output_tokens / 1000 * config["cost_per_1k_output"]
        )

        return BenchmarkEntry(
            config_name=config["name"],
            task_id=task.id,
            accuracy=round(accuracy, 3),
            latency_ms=round(latency, 1),
            cost_usd=round(cost, 6),
            tokens=total_tokens,
        )

    def find_pareto_optimal(self, entries: list[BenchmarkEntry]) -> list[str]:
        """正確性・レイテンシ・コストのバランスでパレート最適な設定を見つける。"""
        # 設定ごとに指標を集計する
        config_metrics: dict[str, dict[str, float]] = {}
        for entry in entries:
            if entry.config_name not in config_metrics:
                config_metrics[entry.config_name] = {
                    "accuracy_sum": 0.0,
                    "latency_sum": 0.0,
                    "cost_sum": 0.0,
                    "count": 0,
                }
            metrics = config_metrics[entry.config_name]
            metrics["accuracy_sum"] += entry.accuracy
            metrics["latency_sum"] += entry.latency_ms
            metrics["cost_sum"] += entry.cost_usd
            metrics["count"] += 1

        # 平均を計算する
        averages: dict[str, dict[str, float]] = {}
        for name, metrics in config_metrics.items():
            count = metrics["count"]
            averages[name] = {
                "accuracy": metrics["accuracy_sum"] / count,
                "latency": metrics["latency_sum"] / count,
                "cost": metrics["cost_sum"] / count,
            }

        # パレート最適を見つける: 別の設定が全軸で優れていれば、その設定は劣後する
        pareto: list[str] = []
        config_names = list(averages.keys())

        for name in config_names:
            dominated = False
            for other_name in config_names:
                if name == other_name:
                    continue
                other = averages[other_name]
                current = averages[name]
                # 他の設定が高い正確性・低いレイテンシ・低いコストを持てば優越する
                if (
                    other["accuracy"] >= current["accuracy"]
                    and other["latency"] <= current["latency"]
                    and other["cost"] <= current["cost"]
                    and (
                        other["accuracy"] > current["accuracy"]
                        or other["latency"] < current["latency"]
                        or other["cost"] < current["cost"]
                    )
                ):
                    dominated = True
                    break
            if not dominated:
                pareto.append(name)

        logger.info("Pareto-optimal configs: %s", pareto)
        return pareto
