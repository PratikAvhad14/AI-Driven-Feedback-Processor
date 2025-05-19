from pydantic import BaseModel, Field, validator
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from enum import Enum


class StatusEnum(str, Enum):
    """Enum for possible status values"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class StatusResponse(BaseModel):
    """
    Response model for status check requests, representing the processing state
    of an asynchronous operation.
    """
    request_id: str = Field(
        ...,
        description="Unique identifier for the processing request",
        examples=["550e8400-e29b-41d4-a716-446655440000"]
    )

    feedback_id: Optional[str] = Field(
        None,
        description="Optional identifier for user feedback reference",
        examples=["FBK12345"]
    )

    status: StatusEnum = Field(
        ...,
        description="Current processing status",
        examples=["completed"]
    )

    results: Optional[Dict[str, Any]] = Field(
        None,
        description="Processed results when status is completed",
        examples=[{"score": 0.95, "categories": ["urgent", "billing"]}]
    )

    updated_at: datetime = Field(
        ...,
        description="Timestamp of last status update in ISO format",
        examples=["2023-10-05T14:48:00.000Z"]
    )

    created_at: Optional[datetime] = Field(
        None,
        description="Timestamp when the request was initially created",
        examples=["2023-10-05T14:30:00.000Z"]
    )

    error_message: Optional[str] = Field(
        None,
        description="Error details if status is failed",
        examples=["Invalid input format"]
    )

    progress: Optional[float] = Field(
        None,
        ge=0,
        le=1,
        description="Progress percentage (0 to 1) for processing status",
        examples=[0.65]
    )

    @validator('status', pre=True)
    def normalize_status(cls, value):
        """Normalize status to lowercase before enum validation"""
        if isinstance(value, str):
            return value.lower()
        return value

    @validator('updated_at', 'created_at', pre=True)
    def parse_datetime(cls, value):
        """
        Ensure datetime fields are parsed correctly:
        - If ISO 8601 string: parse directly.
        - If UNIX timestamp (as string or float): convert to datetime.
        """
        if isinstance(value, (float, int)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if isinstance(value, str):
            try:
                # Handle UNIX timestamp as string
                if value.replace('.', '', 1).isdigit():
                    return datetime.fromtimestamp(float(value),tz=timezone.utc)
                return datetime.fromisoformat(value.replace('Z', '+00:00'))
            except Exception:
                raise ValueError("Invalid datetime format. Expected ISO 8601 format or UNIX timestamp")
        return value
