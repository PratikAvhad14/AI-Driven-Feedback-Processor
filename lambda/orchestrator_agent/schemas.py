from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Literal


# ----- Tool Output Models -----
class GuardrailOutput(BaseModel):
    is_safe: bool = Field(..., description="Whether content passes safety checks")
    risk_level: Literal["low", "medium", "high"] = Field(..., description="Severity of detected risks")
    violations: List[str] = Field(default_factory=list, description="List of violated policies")
    sanitized_text: Optional[str] = Field(None, description="Cleaned version of input text if modifications were needed")

    #  convert an instance of the model into a simplified dictionary format suitable for storing in DynamoDB
    def to_dynamodb(self) -> Dict:
        return {
            "is_safe": self.is_safe,
            "risk_level": self.risk_level,
            "has_violations": len(self.violations) > 0
        }
