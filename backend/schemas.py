from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class RankedItem(Strict):
    article_id: str
    score: int = Field(ge=0, le=100)
    reason_ko: str
    event_group: str
    search_terms: str


class Ranking(Strict):
    items: list[RankedItem]


class SearchTerms(Strict):
    terms_ko_en: str


class Slide(Strict):
    headline: str = Field(min_length=1, max_length=90)
    body: str = Field(min_length=1, max_length=320)


class Scene(Strict):
    headline: str = Field(min_length=1, max_length=75)
    body: str = Field(min_length=1, max_length=190)
    seconds: int = Field(ge=5, le=12)


class Fact(Strict):
    claim: str
    evidence: str = Field(min_length=12, max_length=280)


class Content(Strict):
    title: str = Field(min_length=1, max_length=100)
    summary_ko: str
    interpretation_ko: str
    facts: list[Fact] = Field(min_length=1, max_length=5)
    reference_ids: list[str] = Field(min_length=1, max_length=8)
    carousel: list[Slide] = Field(min_length=4, max_length=7)
    reel: list[Scene] = Field(min_length=4, max_length=7)
    caption: str = Field(min_length=1, max_length=1400)
    review_notes_ko: list[str]


class Report(Strict):
    summary_ko: str
    observations: list[str]
    experiments: list[str]
    limitations: list[str]
