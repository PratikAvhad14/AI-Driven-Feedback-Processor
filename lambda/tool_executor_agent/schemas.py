from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Literal


# --- Sentiment Analysis Output ---
class SentimentAnalysisOutput(BaseModel):
    sentiment: Literal["positive", "negative", "neutral"] = Field(
        ..., description="Overall sentiment classification")
    confidence_score: float = Field(
        ..., description="Confidence score of the sentiment result (0-1)z")
    explanation: Optional[str] = Field(
        None,
        description="Optional rationale behind the sentiment classification"
    )

    # Define explicit model config to control required fields
    model_config = {
        "json_schema_extra": {
            "required": ["sentiment", "confidence_score"]
        }
    }


# --- Topic Categorization Output ---
class TopicCategorizationOutput(BaseModel):
    primary_topic: str = Field(
        ..., description="Main topic identified from feedback")
    secondary_topics: Optional[List[str]] = Field(
        default_factory=list, description="Other relevant topics")
    confidence_scores: Optional[Dict[str, float]] = Field(
        default_factory=dict, description="Confidence scores for each topic")

    # Define explicit model config to control required fields
    model_config = {
        "json_schema_extra": {
            "required": ["primary_topic"]
        }
    }


# --- Keyword Contextualization Output ---
class KeywordContextualizationOutput(BaseModel):
    keyword: str = Field(..., description="Extracted keyword from feedback")
    context: str = Field(
        ..., description="Context or sentence where the keyword appears")
    relevance_score: float = Field(
        ..., description="Relevance score indicating importance (0-1)")

    # Define explicit model config to control required fields
    model_config = {
        "json_schema_extra": {
            "required": ["keyword", "context", "relevance_score"]
        }
    }


class KeywordContextualizationResult(BaseModel):
    extracted_keywords: List[KeywordContextualizationOutput] = Field(
        ..., description="List of context-aware keywords")

    # Define explicit model config to control required fields
    model_config = {
        "json_schema_extra": {
            "required": ["extracted_keywords"]
        }
    }


# --- Summary Generation Output ---
class SummaryGenerationOutput(BaseModel):
    summary_text: str = Field(...,
                              description="Concise summary of the feedback")
    actionable_insights: Optional[List[str]] = Field(
        default_factory=list, description="Action items derived from feedback")
    clarity_score: Optional[float] = Field(
        None, description="Optional clarity score (0-1)")

    # Define explicit model config to control required fields
    model_config = {
        "json_schema_extra": {
            "required": ["summary_text"]
        }
    }


# --- Tool Executor Output ---
class ToolExecutorOutput(BaseModel):
    use_tools: List[str] = Field(
        ..., description="List of tools to use for processing")
    feedback_id: Optional[str] = Field(
        None, description="Unique identifier of the feedback")
    sentiment: Optional[SentimentAnalysisOutput] = None
    topics: Optional[TopicCategorizationOutput] = None
    keywords: Optional[KeywordContextualizationResult] = None
    summary: Optional[SummaryGenerationOutput] = None
    processed_at: Optional[str] = Field(
        None, description="Time the feedback was processed (ISO 8601 string)")

    # Define explicit model config to control required fields
    model_config = {
        "json_schema_extra": {
            "required": ["use_tools"]
        }
    }
