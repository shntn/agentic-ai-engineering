"""
パイプラインデータおよび型付きイベントシステムのためのPydanticモデル。

データモデルはLLMの構造化出力を検証する。イベントモデルは非同期ジェネレーター経由で
型安全な進捗トラッキングを可能にする——エントリーポイントはパターンマッチングを使い、
各イベントタイプを適切なRich UIでレンダリングする。
"""

from enum import Enum
from typing import Literal, Union

from pydantic import BaseModel, Field, computed_field


# ─── データモデル ─────────────────────────────────────────────────────────────


class ContentType(str, Enum):
    """パイプラインが生成できるコンテンツタイプ。"""

    BLOG = "blog"
    TUTORIAL = "tutorial"
    CONCEPT = "concept"


class ClassificationResult(BaseModel):
    """ルーティングの出力: コンテンツタイプ + トピック分析。"""

    content_type: ContentType
    topic: str
    key_aspects: list[str]
    reasoning: str


class Source(BaseModel):
    """リサーチや修正の過程で見つかったWebソース。"""

    title: str
    url: str


class Subtopic(BaseModel):
    """リサーチ計画の1項目: 調査する1つのサブトピック。"""

    title: str
    research_prompt: str


class ResearchSection(BaseModel):
    """リサーチの出力: 統合された調査結果の1セクション。"""

    title: str
    content: str
    sources: list[Source] = []


class EvaluationResult(BaseModel):
    """下書きの5次元品質評価。"""

    clarity: int = Field(ge=1, le=10)
    technical_accuracy: int = Field(ge=1, le=10)
    structure: int = Field(ge=1, le=10)
    engagement: int = Field(ge=1, le=10)
    human_voice: int = Field(ge=1, le=10)
    issues: list[str] = []
    suggestions: list[str] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def avg_score(self) -> float:
        """5つの次元すべての平均値。"""
        return (
            self.clarity
            + self.technical_accuracy
            + self.structure
            + self.engagement
            + self.human_voice
        ) / 5


class SocialContent(BaseModel):
    """ファンアウトの出力: 3つのプラットフォーム向けソーシャルメディアコンテンツ。"""

    linkedin: str = ""
    twitter: str = ""
    newsletter: str = ""


class SeoResult(BaseModel):
    """投票の出力: 候補の中から選ばれた最良のSEOタイトル。"""

    winning_title: str
    reasoning: str
    candidates: list[str] = []


class WritingResult(BaseModel):
    """パイプラインの最終出力: 記事 + 任意のプロモパック。"""

    content_type: ContentType
    title: str
    content: str
    final_score: float
    iterations: int
    sources: list[Source] = []
    social: SocialContent | None = None
    seo: SeoResult | None = None


# ─── 型付きイベント ────────────────────────────────────────────────────────────
# 各イベントはLiteralなstageフィールドを持つPydanticモデル。
# 非同期ジェネレーターがこれらをyieldし、エントリーポイントがmatch/caseでレンダリングする。


class ClassifyStartEvent(BaseModel):
    """分類が開始されたときに発行される。"""

    stage: Literal["classify_start"] = "classify_start"


class ClassifyDoneEvent(BaseModel):
    """分類が完了したときに発行される。"""

    stage: Literal["classify_done"] = "classify_done"
    classification: ClassificationResult


class HumanCheckpointEvent(BaseModel):
    """エージェントが人間の入力を必要とするときに発行される。"""

    stage: Literal["human_checkpoint"] = "human_checkpoint"
    checkpoint_id: str
    title: str
    content: str
    question: str


class PlanStartEvent(BaseModel):
    """リサーチ計画が開始されたときに発行される。"""

    stage: Literal["plan_start"] = "plan_start"


class PlanDoneEvent(BaseModel):
    """リサーチ計画が完了したときに発行される。"""

    stage: Literal["plan_done"] = "plan_done"
    subtopics: list[Subtopic]


class ResearchStartEvent(BaseModel):
    """並行リサーチが開始されたときに発行される。"""

    stage: Literal["research_start"] = "research_start"
    count: int


class ResearchSectionDoneEvent(BaseModel):
    """1つのリサーチワーカーが完了したときに発行される。"""

    stage: Literal["research_section_done"] = "research_section_done"
    title: str
    sources: list[Source] = []


class ResearchDoneEvent(BaseModel):
    """すべてのリサーチワーカーが完了したときに発行される。"""

    stage: Literal["research_done"] = "research_done"
    sections: list[ResearchSection]


class WriteStartEvent(BaseModel):
    """執筆が開始されたときに発行される。"""

    stage: Literal["write_start"] = "write_start"
    iteration: int


class WriteDoneEvent(BaseModel):
    """執筆が完了したときに発行される。"""

    stage: Literal["write_done"] = "write_done"
    iteration: int
    content_length: int
    content: str
    sources: list[Source] = []


class EvaluateStartEvent(BaseModel):
    """評価が開始されたときに発行される。"""

    stage: Literal["evaluate_start"] = "evaluate_start"
    iteration: int


class EvaluateDoneEvent(BaseModel):
    """評価が完了したときに発行される。"""

    stage: Literal["evaluate_done"] = "evaluate_done"
    iteration: int
    evaluation: EvaluationResult


class RefineStartEvent(BaseModel):
    """修正の書き直しが開始されたときに発行される。"""

    stage: Literal["refine_start"] = "refine_start"
    iteration: int


class SocialStartEvent(BaseModel):
    """ソーシャルメディアのファンアウトが開始されたときに発行される。"""

    stage: Literal["social_start"] = "social_start"


class SocialWriterDoneEvent(BaseModel):
    """1つのソーシャルメディアライターが完了したときに発行される。"""

    stage: Literal["social_writer_done"] = "social_writer_done"
    name: str


class SocialDoneEvent(BaseModel):
    """すべてのソーシャルメディアライターが完了したときに発行される。"""

    stage: Literal["social_done"] = "social_done"
    social: SocialContent


class SeoStartEvent(BaseModel):
    """SEOタイトル投票が開始されたときに発行される。"""

    stage: Literal["seo_start"] = "seo_start"


class SeoCandidateEvent(BaseModel):
    """1つのSEOタイトル候補が生成されたときに発行される。"""

    stage: Literal["seo_candidate"] = "seo_candidate"
    title: str


class SeoDoneEvent(BaseModel):
    """SEOタイトル投票が完了したときに発行される。"""

    stage: Literal["seo_done"] = "seo_done"
    seo: SeoResult


class CompleteEvent(BaseModel):
    """パイプライン全体が完了したときに発行される。"""

    stage: Literal["complete"] = "complete"
    result: WritingResult


AgentEvent = Union[
    ClassifyStartEvent,
    ClassifyDoneEvent,
    HumanCheckpointEvent,
    PlanStartEvent,
    PlanDoneEvent,
    ResearchStartEvent,
    ResearchSectionDoneEvent,
    ResearchDoneEvent,
    WriteStartEvent,
    WriteDoneEvent,
    EvaluateStartEvent,
    EvaluateDoneEvent,
    RefineStartEvent,
    SocialStartEvent,
    SocialWriterDoneEvent,
    SocialDoneEvent,
    SeoStartEvent,
    SeoCandidateEvent,
    SeoDoneEvent,
    CompleteEvent,
]
