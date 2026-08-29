"""出力検証: PII漏洩・コンテンツポリシー・グラウンデッドネスのチェック。"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from openrouter.components import ChatResult

logger = logging.getLogger(__name__)


def _strip_code_fences(text: str) -> str:
    """LLMがJSONに付けることがあるMarkdownコードフェンス（```json ... ```）を取り除く。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3].strip()
    return text


# 入力ガードのPIIパターンを再利用する
PII_PATTERNS = {
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "credit_card": r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",
    "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
}

CONTENT_POLICY_PROMPT = (
    "以下のAIアシスタントの応答に、コンテンツポリシー違反がないか確認してください。\n\n"
    "確認項目:\n"
    "- 有害・危険・違法な助言\n"
    "- 内部のシステム詳細・プロンプト・設定の漏洩\n"
    "- 不適切または非専門的な内容\n"
    "- 無許可の約束・コミットメント\n\n"
    "JSONオブジェクトのみで回答してください。issueフィールドは必ず日本語で記述してください:\n"
    '{{"passed": true/false, "issue": "<違反があれば日本語で説明、なければ空文字列>"}}\n\n'
    "確認対象の応答:\n{output}"
)

GROUNDEDNESS_PROMPT = (
    "以下の応答が、与えられたコンテキストに基づいているかを評価してください。\n\n"
    "応答内の事実に関する主張それぞれについて、コンテキストが裏付けているか確認してください。\n"
    "会話的な言い回しは無視し、具体的な事実・数値・手順のみを確認してください。\n\n"
    "コンテキスト:\n<context>\n{context}\n</context>\n\n"
    "応答:\n<response>\n{output}\n</response>\n\n"
    "JSONオブジェクトのみで回答してください:\n"
    '{{"score": <0.0〜1.0>, "unsupported_claims": ["claim1", ...]}}'
)


@dataclass
class OutputCheckResult:
    """出力検証の結果。"""

    passed: bool
    issues: list[str] = field(default_factory=list)
    pii_found: dict[str, list[str]] = field(default_factory=dict)
    groundedness_score: float = 1.0
    unsupported_claims: list[str] = field(default_factory=list)
    checks: dict = field(default_factory=dict)


class OutputGuard:
    """ユーザーに返す前にエージェントの出力を検証する。"""

    def __init__(self, client: Any, classifier_model: str, token_tracker: Any):
        self.client = client
        self.classifier_model = classifier_model
        self.token_tracker = token_tracker

    def check(self, output: str, context: str | None = None) -> OutputCheckResult:
        """出力チェックを実行する: PII漏洩・コンテンツポリシー・グラウンデッドネス。"""
        issues: list[str] = []
        checks: dict[str, dict] = {}

        # チェック1: PII漏洩
        pii_found = self._scan_pii_leakage(output)
        pii_detail = "none detected" if not pii_found else f"found: {', '.join(pii_found.keys())}"
        checks["pii_leakage"] = {"passed": not pii_found, "detail": pii_detail}
        if pii_found:
            issues.append(f"PII detected in output: {', '.join(pii_found.keys())}")

        # チェック2: コンテンツポリシー
        try:
            policy_ok, policy_issue = self._check_content_policy(output)
            checks["content_policy"] = {"passed": policy_ok, "detail": policy_issue or "compliant"}
            if not policy_ok:
                issues.append(f"Content policy violation: {policy_issue}")
        except Exception as e:
            logger.warning("Content policy check failed: %s", e)
            checks["content_policy"] = {"passed": True, "detail": "check unavailable"}

        # チェック3: グラウンデッドネス（コンテキストが渡された場合のみ）
        groundedness_score = 1.0
        unsupported: list[str] = []
        if context:
            try:
                groundedness_score, unsupported = self._check_groundedness(output, context)
                grounded_ok = groundedness_score >= 0.5
                checks["groundedness"] = {
                    "passed": grounded_ok,
                    "detail": f"score: {groundedness_score:.2f}",
                }
                if not grounded_ok:
                    issues.append(
                        f"Low groundedness ({groundedness_score:.2f}): "
                        f"{len(unsupported)} unsupported claims"
                    )
            except Exception as e:
                logger.warning("Groundedness check failed: %s", e)
                checks["groundedness"] = {"passed": True, "detail": "check unavailable"}
        else:
            checks["groundedness"] = {"passed": True, "detail": "no context provided"}

        return OutputCheckResult(
            passed=len(issues) == 0,
            issues=issues,
            pii_found=pii_found,
            groundedness_score=groundedness_score,
            unsupported_claims=unsupported,
            checks=checks,
        )

    def _scan_pii_leakage(self, output: str) -> dict[str, list[str]]:
        """出力に漏洩してはいけないPIIが含まれていないか確認する。"""
        found: dict[str, list[str]] = {}
        for pii_type, pattern in PII_PATTERNS.items():
            matches = re.findall(pattern, output)
            if matches:
                flat = [m if isinstance(m, str) else m[0] for m in matches]
                found[pii_type] = flat
        return found

    def _check_content_policy(self, output: str) -> tuple[bool, str]:
        """分類器モデルで出力がコンテンツポリシーを満たしているか確認する。"""
        # reasoning={"effort": "none"}を付けないと、思考モデルではreasoningトークンが
        # max_tokensを消費し尽くし、contentが空になることがある
        response: ChatResult = self.client.chat.send(
            model=self.classifier_model,
            max_tokens=150,
            reasoning={"effort": "none"},
            messages=[
                {"role": "user", "content": CONTENT_POLICY_PROMPT.format(output=output)},
            ],
        )
        assert response.usage is not None
        self.token_tracker.track(response.usage)

        raw = str(response.choices[0].message.content or "").strip()
        try:
            result = json.loads(_strip_code_fences(raw))
            passed = bool(result.get("passed", True))
            issue = result.get("issue", "")
            return passed, issue
        except (json.JSONDecodeError, ValueError, AttributeError):
            logger.warning("Failed to parse content policy response: %s", raw[:100])
            return True, ""

    def _check_groundedness(self, output: str, context: str) -> tuple[float, list[str]]:
        """出力が与えられたコンテキストにどれだけ基づいているかをスコアリングする。"""
        # reasoning={"effort": "none"}を付けないと、思考モデルではreasoningトークンが
        # max_tokensを消費し尽くし、contentが空になることがある
        response: ChatResult = self.client.chat.send(
            model=self.classifier_model,
            max_tokens=300,
            reasoning={"effort": "none"},
            messages=[
                {
                    "role": "user",
                    "content": GROUNDEDNESS_PROMPT.format(output=output, context=context),
                },
            ],
        )
        assert response.usage is not None
        self.token_tracker.track(response.usage)

        raw = str(response.choices[0].message.content or "").strip()
        try:
            result = json.loads(_strip_code_fences(raw))
            score = float(result.get("score", 1.0))
            unsupported = result.get("unsupported_claims", [])
            return score, unsupported
        except (json.JSONDecodeError, ValueError, AttributeError):
            logger.warning("Failed to parse groundedness response: %s", raw[:100])
            return 1.0, []
