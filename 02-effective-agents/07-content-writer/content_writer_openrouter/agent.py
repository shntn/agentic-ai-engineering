"""
チュートリアル02-07のすべてのパターンを組み合わせたコンテンツライターエージェント。

パイプラインの各フェーズは、それぞれのパターンに対応する:
- _classify()             → ルーティング (03)
- _plan() / _replan()     → オーケストレーター (05)
- _research_parallel()    → Web検索を伴う並列ワーカー (05)
- _write()                → タイプ別のトーンを持つプロンプトチェイニング (02)
- _evaluate()             → 評価者・最適化 (06)
- _write_social()         → 並列化のファンアウト (04)
- _generate_seo()         → 並列化の投票 (04)

Human-in-the-Loop (07) のチェックポイントは HumanCheckpointEvent としてyieldされ、
エントリーポイント側で on_human_checkpoint コールバックを通じて処理される。

非同期ジェネレーター（run_stream）が型付きイベントをyieldするため、UI層は
エージェントロジックと結合することなく進捗をレンダリングできる。
"""

import asyncio
import json
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, AsyncGenerator, cast

from openrouter import OpenRouter
from openrouter.components import ChatResult
from openrouter.errors import TooManyRequestsResponseError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from common import OpenRouterTokenTracker, setup_logging
from openrouter_adapter import to_openrouter_tools

from . import prompts
from .models import (
    ClassificationResult,
    ClassifyDoneEvent,
    ClassifyStartEvent,
    CompleteEvent,
    ContentType,
    EvaluateDoneEvent,
    EvaluateStartEvent,
    EvaluationResult,
    HumanCheckpointEvent,
    PlanDoneEvent,
    PlanStartEvent,
    RefineStartEvent,
    ResearchDoneEvent,
    ResearchSection,
    ResearchSectionDoneEvent,
    ResearchStartEvent,
    SeoCandidateEvent,
    SeoDoneEvent,
    SeoResult,
    SeoStartEvent,
    SocialContent,
    SocialDoneEvent,
    SocialStartEvent,
    SocialWriterDoneEvent,
    Source,
    Subtopic,
    WriteDoneEvent,
    WriteStartEvent,
    WritingResult,
)
from .models import AgentEvent  # noqa: F401 — 型ヒントのための再エクスポート
from .tools import (
    CLASSIFY_TOOLS,
    EVALUATION_TOOLS,
    PLANNING_TOOLS,
    REQUEST_TIMEOUT_MS,
    SEO_EVALUATION_TOOLS,
    WEB_SEARCH_TOOL,
    ToolExecutor,
)

logger = setup_logging(__name__)

# Human checkpointコールバックの型
HumanCheckpointFn = Callable[[HumanCheckpointEvent], tuple[bool, str]]


class ContentWriterAgent:
    """チュートリアル02-07のすべてのパターンを組み合わせた、完全なコンテンツ生成エージェント。"""

    def __init__(
        self,
        model: str,
        research_model: str,
        token_tracker: OpenRouterTokenTracker,
    ) -> None:
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.research_model = research_model
        self.token_tracker = token_tracker
        self.tool_executor = ToolExecutor()

    # ─── LLM呼び出しのプリミティブ ──────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type(TooManyRequestsResponseError),
        wait=wait_exponential(multiplier=2, min=30, max=120),
        stop=stop_after_attempt(6),
        before_sleep=before_sleep_log(logger, log_level=20),
    )
    def _call_api(self, **kwargs: Any) -> ChatResult:
        """レート制限時にリトライする、低レベルのAPI呼び出し。"""
        response: ChatResult = self.client.chat.send(**kwargs)
        return response

    def _call_llm(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        use_light: bool = True,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
    ) -> ChatResult:
        """単一のLLM呼び出しを行い、トークンを追跡する。"""
        model = self.research_model if use_light else self.model
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        # デフォルトのタイムアウト設定は短すぎるため、timeout_ms の設定を追加
        if timeout_ms:
            kwargs["timeout_ms"] = timeout_ms
        tool_names = [t.get("name", t.get("type", "unknown")) for t in tools or []]
        logger.info("Calling %s, tools=%s", model, tool_names)

        response: ChatResult = self._call_api(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning={"effort": "none"},
            messages=[{"role": "system", "content": system}, *messages],
            **kwargs,
        )
        self.token_tracker.track(response.usage)
        return response

    def _call_tool(self, system: str, user_message: str, tools: list, tool_name: str) -> dict:
        """tool_choiceによる構造化出力を返すLLM呼び出し。"""
        response = self._call_llm(
            system,
            [{"role": "user", "content": user_message}],
            use_light=False,
            max_tokens=1024,
            tools=to_openrouter_tools(tools),
            tool_choice={"type": "function", "function": {"name": tool_name}},
        )
        tool_calls = response.choices[0].message.tool_calls or []
        if tool_calls:
            return cast(dict[Any, Any], json.loads(tool_calls[0].function.arguments))
        raise ValueError(f"No tool call in response for {tool_name}")

    def _run_agent_loop(
        self,
        system: str,
        user_message: str,
        *,
        tools: list[dict[str, Any]],
        use_light: bool = True,
        max_tokens: int = 4096,
        max_turns: int = 5,
    ) -> tuple[str, list[Source]]:
        """ツールを使ってLLMを実行する。OpenRouterのWeb検索はサーバー側で解決されるため、
        Anthropic版と異なり複数ターンにわたって継続する必要はない。"""
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        response = self._call_llm(
            system,
            messages,
            use_light=use_light,
            max_tokens=max_tokens,
            tools=tools,
            timeout_ms=REQUEST_TIMEOUT_MS,
        )
        text = str(response.choices[0].message.content or "")
        sources: list[Source] = []
        return text, sources

    def _call_text(
        self,
        system: str,
        user_message: str,
        *,
        use_light: bool = True,
        max_tokens: int = 4096,
        temperature: float = 1.0,
    ) -> str:
        """ツールを使わない、単一ターンのLLM呼び出し。"""
        response = self._call_llm(
            system,
            [{"role": "user", "content": user_message}],
            use_light=use_light,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return str(response.choices[0].message.content or "")

    # ─── パイプラインの各フェーズ ─────────────────────────────────────────────

    def _classify(self, topic: str) -> ClassificationResult:
        """構造化出力でトピックをコンテンツタイプに分類する（ルーティングパターン）。"""
        logger.info("Phase: classify — %s", topic[:50])
        data = self._call_tool(
            prompts.CLASSIFICATION_SYSTEM, topic, CLASSIFY_TOOLS, "classify_content"
        )
        return ClassificationResult(**data)

    def _plan(
        self, topic: str, content_type: ContentType, key_aspects: list[str]
    ) -> list[Subtopic]:
        """2〜4個のサブトピックからなるリサーチ計画を作成する（オーケストレーターパターン）。"""
        logger.info("Phase: plan — %s (%s)", topic[:50], content_type.value)
        aspects_text = ", ".join(key_aspects) if key_aspects else "一般的な概要"
        result = self._call_tool(
            f"あなたは{content_type.value}記事のリサーチプランナーです。"
            f"取り上げるべき主要な側面: {aspects_text}。"
            "トピックを、独立して調査可能な2〜3個の焦点を絞ったサブトピックに"
            "分解してください。それぞれが異なる角度をカバーするようにしてください。",
            f"次のトピックのリサーチ計画を立ててください: {topic}",
            PLANNING_TOOLS,
            "create_research_plan",
        )
        return [Subtopic(**s) for s in result["subtopics"]]

    def _replan(self, topic: str, plan_text: str, feedback: str) -> list[Subtopic]:
        """人間からのフィードバックを基にリサーチ計画を修正する。"""
        logger.info("Phase: replan")
        result = self._call_tool(
            f"次のフィードバックを基にこのリサーチ計画を修正してください: {feedback}",
            f"元の計画:\n{plan_text}\n\nトピック: {topic}",
            PLANNING_TOOLS,
            "create_research_plan",
        )
        return [Subtopic(**s) for s in result["subtopics"]]

    def _research_section(self, subtopic: Subtopic) -> ResearchSection:
        """Web検索を使って1つのサブトピックを調査する（ワーカーパターン）。"""
        logger.info("Phase: research — %s", subtopic.title)
        content, sources = self._run_agent_loop(
            prompts.RESEARCH_SYSTEM,
            subtopic.research_prompt,
            tools=[WEB_SEARCH_TOOL],
            max_tokens=1024,
        )
        return ResearchSection(title=subtopic.title, content=content, sources=sources)

    def _write(
        self,
        topic: str,
        sections: list[ResearchSection],
        content_type: ContentType,
        feedback: str | None = None,
        previous_draft: str | None = None,
    ) -> tuple[str, list[Source]]:
        """タイプ別のトーンで記事を執筆・修正する（チェイニングパターン）。"""
        logger.info("Phase: write — %s (%s)", topic[:50], content_type.value)

        research_text = "\n\n".join(f"## {s.title}\n{s.content}" for s in sections)

        if feedback and previous_draft:
            # 修正: 追加の概念のためにWeb検索を使用する
            user_msg = (
                f"トピック: {topic}\n\n"
                f"リサーチ:\n{research_text}\n\n"
                f"対応すべきフィードバック:\n{feedback}\n\n"
                f"前回の下書き:\n{previous_draft}\n\n"
                "すべてのフィードバックに対応するよう下書きを修正してください。"
            )
            return self._run_agent_loop(
                prompts.get_revision_system(content_type),
                user_msg,
                tools=[WEB_SEARCH_TOOL],
                use_light=False,
                max_tokens=8192,
            )
        else:
            # 初回執筆: 質の高い構成と完全性のため
            user_msg = (
                f"リサーチ:\n{research_text}\n\n"
                f"次のトピックについて完全な{content_type.value}を書いてください: {topic}"
            )
            content = self._call_text(
                prompts.get_writing_system(content_type),
                user_msg,
                use_light=False,
                max_tokens=8192,
            )
            return content, []

    def _evaluate(self, topic: str, draft: str) -> EvaluationResult:
        """5次元の構造化スコアリングで下書きを評価する（評価者パターン）。"""
        logger.info("Phase: evaluate")
        data = self._call_tool(
            prompts.EVALUATION_SYSTEM,
            f"トピック: {topic}\n\n評価する下書き:\n\n{draft}",
            EVALUATION_TOOLS,
            "evaluate_draft",
        )
        return EvaluationResult(**data)

    # ─── ソーシャルメディア（04からの並列化ファンアウト） ──────────────────────

    def _write_linkedin(self, article: str) -> str:
        """LinkedIn向けのプロフェッショナルな要約を生成する。"""
        return self._call_text(
            prompts.LINKEDIN_SYSTEM,
            f"次の記事からLinkedIn投稿を作成してください:\n\n{article[:2000]}",
            max_tokens=2048,
        )

    def _write_twitter(self, article: str) -> str:
        """5件のツイートからなるTwitter/Xスレッドを生成する。"""
        return self._call_text(
            prompts.TWITTER_SYSTEM,
            f"次の記事からツイートスレッドを作成してください:\n\n{article[:2000]}",
            max_tokens=2048,
        )

    def _write_newsletter(self, article: str) -> str:
        """ニュースレターの件名と導入段落を生成する。"""
        return self._call_text(
            prompts.NEWSLETTER_SYSTEM,
            f"次の記事からニュースレターの導入文を作成してください:\n\n{article[:2000]}",
            max_tokens=2048,
        )

    def _write_social(self, article: str) -> SocialContent:
        """ソーシャルメディアコンテンツを並行して生成する（ファンアウトパターン）。"""
        logger.info("Phase: social media fan-out")
        results: dict[str, str] = {}
        writers = {
            "linkedin": self._write_linkedin,
            "twitter": self._write_twitter,
            "newsletter": self._write_newsletter,
        }
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(fn, article): name for name, fn in writers.items()}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                except Exception as e:
                    logger.error("Social writer %s failed: %s", name, e)
                    results[name] = f"Error: {e}"
        return SocialContent(**results)

    # ─── SEOタイトル（04からの並列化投票） ──────────────────────────────

    def _generate_seo_title(self, article: str, temperature: float) -> str:
        """与えられたtemperatureで単一のSEOタイトル候補を生成する。"""
        return self._call_text(
            prompts.SEO_TITLE_SYSTEM,
            f"次に対するSEOタイトルを生成してください:\n\n{article[:500]}",
            max_tokens=128,
            temperature=temperature,
        )

    def _vote_best_title(self, titles: list[str], article: str) -> dict:
        """構造化出力を使って最も良いSEOタイトルを選ぶ（投票パターン）。"""
        titles_text = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles))
        return self._call_tool(
            prompts.SEO_EVALUATOR_SYSTEM,
            f"記事の要約: {article[:300]}\n\n候補タイトル:\n{titles_text}",
            SEO_EVALUATION_TOOLS,
            "pick_best_title",
        )

    def _generate_seo(self, article: str) -> SeoResult:
        """投票パターンでSEOタイトルを生成する: 3つの候補 → 評価者が最良のものを選ぶ。"""
        logger.info("Phase: SEO title voting")
        temperatures = [0.3, 0.7, 1.0]
        titles: list[str] = []

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures_list = [
                executor.submit(self._generate_seo_title, article, temp) for temp in temperatures
            ]
            for future in as_completed(futures_list):
                try:
                    titles.append(future.result().strip())
                except Exception as e:
                    logger.error("SEO title generation failed: %s", e)

        if not titles:
            return SeoResult(winning_title="", reasoning="All candidates failed", candidates=[])

        result = self._vote_best_title(titles, article)
        return SeoResult(
            winning_title=result["winning_title"],
            reasoning=result["reasoning"],
            candidates=titles,
        )

    # ─── 非同期イベントストリーム ──────────────────────────────────────────────

    async def run_stream(
        self,
        topic: str,
        *,
        score_threshold: float = 7.0,
        max_refinements: int = 2,
        on_human_checkpoint: HumanCheckpointFn | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """パイプラインの進行に応じて型付きイベントをyieldする非同期ジェネレーター。"""
        logger.info("Pipeline start — topic=%s, threshold=%.1f", topic[:50], score_threshold)

        def _checkpoint(
            checkpoint_id: str, title: str, content: str, question: str
        ) -> tuple[bool, str]:
            """提供されていれば、human checkpointコールバックを呼び出す。"""
            if on_human_checkpoint is None:
                return True, ""
            event = HumanCheckpointEvent(
                checkpoint_id=checkpoint_id,
                title=title,
                content=content,
                question=question,
            )
            return on_human_checkpoint(event)

        # === フェーズ1: 分類（03のルーティングパターン） ===
        yield ClassifyStartEvent()
        classification = await asyncio.to_thread(self._classify, topic)
        yield ClassifyDoneEvent(classification=classification)

        content_type = classification.content_type
        other_types = [t for t in ContentType if t != content_type]
        approved, feedback = await asyncio.to_thread(
            _checkpoint,
            "classification",
            "Classification",
            f"Type: {content_type.value.upper()}\n"
            f"Topic: {classification.topic}\n"
            f"Aspects: {', '.join(classification.key_aspects)}\n"
            f"Reasoning: {classification.reasoning}\n\n"
            f"Other options: {', '.join(t.value for t in other_types)}",
            f"Classified as '{content_type.value}'. Correct?",
        )
        if not approved and feedback in [t.value for t in ContentType]:
            content_type = ContentType(feedback)
            logger.info("Classification overridden to: %s", content_type.value)

        # === フェーズ2: リサーチ計画（05のオーケストレーターパターン） ===
        yield PlanStartEvent()
        subtopics = await asyncio.to_thread(
            self._plan, topic, content_type, classification.key_aspects
        )
        yield PlanDoneEvent(subtopics=subtopics)

        # === フェーズ3: 並列リサーチ（05のワーカーパターン） ===
        yield ResearchStartEvent(count=len(subtopics))

        def _research_parallel() -> list[ResearchSection]:
            results: list[ResearchSection] = []
            with ThreadPoolExecutor(max_workers=len(subtopics)) as executor:
                futures = {
                    executor.submit(self._research_section, sub): sub.title for sub in subtopics
                }
                for future in as_completed(futures):
                    title = futures[future]
                    try:
                        results.append(future.result())
                    except Exception as e:
                        logger.error("Research failed for %s: %s", title, e)
            return results

        sections = await asyncio.to_thread(_research_parallel)
        for section in sections:
            yield ResearchSectionDoneEvent(title=section.title, sources=section.sources)

        if not sections:
            raise ValueError("All research workers failed — cannot continue")
        yield ResearchDoneEvent(sections=sections)

        # Referencesセクションのため、全フェーズにわたるソースを蓄積する
        all_sources: list[Source] = []
        for s in sections:
            all_sources.extend(s.sources)

        # === フェーズ4: 執筆（02のチェイニングパターン） ===
        yield WriteStartEvent(iteration=1)
        draft, write_sources = await asyncio.to_thread(self._write, topic, sections, content_type)
        all_sources.extend(write_sources)
        yield WriteDoneEvent(
            iteration=1, content_length=len(draft), content=draft, sources=write_sources
        )

        # === フェーズ5: 評価 + 修正ループ（06の評価者・最適化パターン） ===
        yield EvaluateStartEvent(iteration=1)
        evaluation = await asyncio.to_thread(self._evaluate, topic, draft)
        yield EvaluateDoneEvent(iteration=1, evaluation=evaluation)

        iteration = 1
        if evaluation.avg_score < score_threshold:
            for iteration in range(2, max_refinements + 2):
                yield RefineStartEvent(iteration=iteration)
                feedback_text = (
                    f"Issues: {json.dumps(evaluation.issues, ensure_ascii=False)}\n"
                    f"Suggestions: {json.dumps(evaluation.suggestions, ensure_ascii=False)}"
                )
                draft, refine_sources = await asyncio.to_thread(
                    self._write,
                    topic,
                    sections,
                    content_type,
                    feedback=feedback_text,
                    previous_draft=draft,
                )
                all_sources.extend(refine_sources)
                yield WriteDoneEvent(
                    iteration=iteration,
                    content_length=len(draft),
                    content=draft,
                    sources=refine_sources,
                )

                yield EvaluateStartEvent(iteration=iteration)
                evaluation = await asyncio.to_thread(self._evaluate, topic, draft)
                yield EvaluateDoneEvent(iteration=iteration, evaluation=evaluation)

                if evaluation.avg_score >= score_threshold:
                    break

        # === Human checkpoint: 最終記事のレビュー（修正ループ付き） ===
        # ユーザーが却下した場合、評価の問題点とユーザーのフィードバックを組み合わせて
        # 06の評価者・最適化パターンと同様に修正する
        human_approved = False
        human_review_rounds = 0
        max_human_reviews = 3

        while not human_approved and human_review_rounds < max_human_reviews:
            human_review_rounds += 1
            preview = draft[:500] + "..." if len(draft) > 500 else draft
            approved, user_feedback = await asyncio.to_thread(
                _checkpoint,
                "final_review",
                "Final Review",
                preview,
                "Approve article and publish?",
            )

            if approved:
                human_approved = True
                break

            # 評価の問題点とユーザーのフィードバックを組み合わせ、的を絞った修正を行う
            feedback_parts = []
            if evaluation.issues:
                feedback_parts.append("Evaluation issues: " + "; ".join(evaluation.issues))
            if evaluation.suggestions:
                feedback_parts.append(
                    "Evaluation suggestions: " + "; ".join(evaluation.suggestions)
                )
            if user_feedback:
                feedback_parts.append(f"User feedback: {user_feedback}")

            if not feedback_parts:
                feedback_parts.append("Please improve the overall quality of the article.")

            combined_feedback = "\n".join(feedback_parts)
            logger.info("Human review round %d — refining with feedback", human_review_rounds)

            iteration += 1
            yield RefineStartEvent(iteration=iteration)
            draft, refine_sources = await asyncio.to_thread(
                self._write,
                topic,
                sections,
                content_type,
                feedback=combined_feedback,
                previous_draft=draft,
            )
            all_sources.extend(refine_sources)
            yield WriteDoneEvent(
                iteration=iteration,
                content_length=len(draft),
                content=draft,
                sources=refine_sources,
            )

            yield EvaluateStartEvent(iteration=iteration)
            evaluation = await asyncio.to_thread(self._evaluate, topic, draft)
            yield EvaluateDoneEvent(iteration=iteration, evaluation=evaluation)

        # 最終記事にReferencesセクションを追加する
        # URLで重複排除しつつ順序を保つ
        seen_urls: set[str] = set()
        unique_sources: list[Source] = []
        for src in all_sources:
            if src.url not in seen_urls:
                seen_urls.add(src.url)
                unique_sources.append(src)

        if unique_sources:
            refs = "\n\n---\n\n## References\n\n"
            refs += "\n".join(f"- [{s.title}]({s.url})" for s in unique_sources)
            draft += refs

        social: SocialContent | None = None
        seo: SeoResult | None = None

        if human_approved:
            # === フェーズ6: ソーシャルメディア一斉配信（04の並列化ファンアウト） ===
            yield SocialStartEvent()
            social = await asyncio.to_thread(self._write_social, draft)
            for name in ["linkedin", "twitter", "newsletter"]:
                content = getattr(social, name, "")
                if content and not content.startswith("Error:"):
                    yield SocialWriterDoneEvent(name=name)
            yield SocialDoneEvent(social=social)

            # === フェーズ7: SEOタイトル投票（04の並列化投票） ===
            yield SeoStartEvent()
            seo = await asyncio.to_thread(self._generate_seo, draft)
            for title in seo.candidates:
                yield SeoCandidateEvent(title=title)
            yield SeoDoneEvent(seo=seo)

        # === 完了 ===
        title_line = next((ln.strip("# ") for ln in draft.split("\n") if ln.strip()), topic)
        yield CompleteEvent(
            result=WritingResult(
                content_type=content_type,
                title=title_line,
                content=draft,
                final_score=evaluation.avg_score,
                iterations=iteration,
                sources=unique_sources,
                social=social,
                seo=seo,
            )
        )
