import json
from typing import Dict, Any


def build_response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """Helper to build an API Gateway response"""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json"
        },
        "body": json.dumps(body, default=str)
    }
