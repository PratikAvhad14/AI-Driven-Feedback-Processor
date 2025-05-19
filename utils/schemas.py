from pydantic import BaseModel, Field, validator
from typing import List, Dict, Optional, Literal
from datetime import datetime, timezone


# ----- Input Models -----
class FeedbackInput(BaseModel):
    feedback_id: str = Field(..., description="Unique identifier for the feedback")
    customer_name: Optional[str] = Field(None, max_length=100)
    feedback_text: str = Field(..., min_length=10, description="Customer feedback content")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    instructions: Optional[str] = Field(None, description="Tool execution guidance")

    @validator("feedback_text")
    def validate_feedback(cls, v):
        if len(v.split()) < 3:
            raise ValueError("Feedback must be at least 3 words")
        return v


# ----- Tool Output Models -----
class GuardrailOutput(BaseModel):
    is_safe: bool = Field(..., description="Whether content passes safety checks")
    risk_level: Literal["low", "medium", "high"] = Field(..., description="Severity of detected risks")
    violations: List[str] = Field(default_factory=list, description="List of violated policies")
    sanitized_text: Optional[str] = Field(None, description="Cleaned version of input text if modifications were needed")

    def to_dynamodb(self) -> Dict:
        return {
            "is_safe": self.is_safe,
            "risk_level": self.risk_level,
            "has_violations": len(self.violations) > 0
        }


class SentimentResult(BaseModel):
    sentiment: Literal["positive", "negative", "neutral"]
    confidence: float = Field(..., ge=0.0, le=1.0)


class TopicResult(BaseModel):
    primary_topic: str
    related_topics: List[str] = Field(default_factory=list)


class KeywordResult(BaseModel):
    keyword: str
    relevance: float = Field(..., gt=0.0, le=1.0)


class SummaryResult(BaseModel):
    summary: str
    actionable_items: List[str] = Field(default_factory=list)


# ----- State Management Models -----
class ToolExecutionRecord(BaseModel):
    tool_name: str
    status: Literal["SUCCESS", "FAILED", "PENDING"]
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    output: Optional[Dict] = None
    error: Optional[str] = None


class AgentState(BaseModel):
    feedback_id: str
    status: Literal["PENDING", "PROCESSING", "COMPLETED", "FAILED"]
    tools: Dict[str, ToolExecutionRecord] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def add_tool_result(self, tool_name: str, result: BaseModel):
        self.tools[tool_name] = ToolExecutionRecord(
            tool_name=tool_name,
            status="SUCCESS",
            output=result.model_dump()
        )
        self.updated_at = datetime.now(timezone.utc)
