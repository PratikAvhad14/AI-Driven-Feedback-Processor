from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Literal
from datetime import datetime


# --- Sentiment Analysis Output ---
class SentimentAnalysisOutput(BaseModel):
    sentiment: Literal["positive", "negative", "neutral"] = Field(..., description="Overall sentiment classification")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Confidence score of the sentiment result")
    explanation: Optional[str] = Field(None, description="Optional rationale behind the sentiment classification")


# --- Topic Categorization Output ---
class TopicCategorizationOutput(BaseModel):
    primary_topic: str = Field(..., description="Main topic identified from feedback")
    secondary_topics: List[str] = Field(default_factory=list, description="Other relevant topics")
    confidence_scores: Dict[str, float] = Field(default_factory=dict, description="Confidence scores for each topic")


# --- Keyword Contextualization Output ---
class KeywordContextualizationOutput(BaseModel):
    keyword: str = Field(..., description="Extracted keyword from feedback")
    context: str = Field(..., description="Context or sentence where the keyword appears")
    relevance_score: float = Field(..., gt=0.0, le=1.0, description="Relevance score indicating importance")


class KeywordContextualizationResult(BaseModel):
    extracted_keywords: List[KeywordContextualizationOutput] = Field(..., description="List of context-aware keywords")


# --- Summary Generation Output ---
class SummaryGenerationOutput(BaseModel):
    summary_text: str = Field(..., description="Concise summary of the feedback")
    actionable_insights: List[str] = Field(default_factory=list, description="Action items derived from feedback")
    clarity_score: Optional[float] = Field(None, ge=0.0, le=1.0, description="Optional clarity score")


# --- Tool Executor Output ---
class ToolExecutorOutput(BaseModel):
    feedback_id: str = Field(..., description="Unique identifier of the feedback")
    sentiment: Optional[SentimentAnalysisOutput] = None
    topics: Optional[TopicCategorizationOutput] = None
    keywords: Optional[KeywordContextualizationResult] = None
    summary: Optional[SummaryGenerationOutput] = None
    processed_at: datetime = Field(default_factory=datetime.utcnow, description="Time the feedback was processed")

