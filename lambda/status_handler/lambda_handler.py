import boto3
import os
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from .schemas import StatusResponse
from dotenv import load_dotenv
from utils.common_functions import build_response

load_dotenv(override=True)

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

AWS_REGION = os.getenv("AWS_REGION")
DYNAMODB_TABLE = os.getenv("DYNAMODB_TABLE")
FEEDBACK_ID_GSI_NAME = os.getenv("FEEDBACK_ID_GSI_NAME", "feedback_id-index")

if not DYNAMODB_TABLE:
    raise ValueError("DYNAMODB_TABLE environment variable is not set")

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(DYNAMODB_TABLE)


def get_by_request_id_only(request_id: str) -> List[Dict[str, Any]]:
    """Get all items with the given request_id"""
    try:
        response = table.query(
            KeyConditionExpression="request_id = :request_id",
            ExpressionAttributeValues={":request_id": request_id}
        )
        return response.get("Items", [])
    except Exception as e:
        logger.error(f"Error fetching by request_id only: {str(e)}")
        return []


def get_by_composite_key(request_id: str, created_at: str) -> Optional[Dict[str, Any]]:
    """Get specific item by composite primary key"""
    try:
        response = table.get_item(
            Key={"request_id": request_id, "created_at": created_at}
        )
        return response.get("Item")
    except Exception as e:
        logger.error(f"Error fetching by composite key: {str(e)}")
        return None


def get_by_feedback_id(feedback_id: str) -> Optional[Dict[str, Any]]:
    """Get item by GSI (feedback_id)"""
    try:
        response = table.query(
            IndexName=FEEDBACK_ID_GSI_NAME,
            KeyConditionExpression="feedback_id = :feedback_id",
            ExpressionAttributeValues={":feedback_id": feedback_id}
        )
        items = response.get("Items", [])
        return items[0] if items else None
    except Exception as e:
        logger.error(f"Error fetching by feedback_id: {str(e)}")
        return None


def validate_and_format_response(item: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and format the DynamoDB item into our response schema"""
    try:
        status_response = StatusResponse(
            request_id=item["request_id"],
            error_message=item.get("error_message"),
            created_at=item.get("created_at", None),
            feedback_id=item.get("feedback_id", None),
            status=item["status"],
            results=item.get("results", None),
            updated_at=item["updated_at"],
            progress=item.get("progress", 0)
        )
        return build_response(200, status_response.model_dump())
    except Exception as validation_error:
        logger.error(f"Data validation error: {str(validation_error)}")
        return build_response(500, {
            "error": "Data validation failed",
            "details": str(validation_error)
        })


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Lambda function to handle status requests with multiple query options"""
    try:
        logger.info(f"Received event: {json.dumps(event)}")
        query_params = event.get("queryStringParameters", {}) or {}

        request_id = query_params.get("request_id")
        created_at = query_params.get("created_at")
        feedback_id = query_params.get("feedback_id")

        # Determine query mode based on provided parameters
        if all([request_id, created_at]):
            # Mode 1: Composite key lookup
            logger.info(
                f"Fetching by composite key (request_id: {request_id}, "
                f"created_at: {created_at})"
            )
            item = get_by_composite_key(request_id, created_at)
            if not item:
                return build_response(404, {
                    "error": "No matching record found with the provided composite key"
                })
            return validate_and_format_response(item)

        elif request_id and not created_at:
            # Mode 2: request_id only lookup
            logger.info(f"Fetching all records for request_id: {request_id}")
            items = get_by_request_id_only(request_id)
            if not items:
                return build_response(404, {"error": f"No records found for request_id: {request_id}"})
            return build_response(200, {"items": items, "count": len(items)})

        elif feedback_id:
            # Mode 3: feedback_id lookup
            logger.info(f"Fetching by feedback_id: {feedback_id}")
            item = get_by_feedback_id(feedback_id)
            if not item:
                return build_response(404, {"error": f"No record found with feedback_id: {feedback_id}"})
            return validate_and_format_response(item)

        else:
            # Invalid parameter combination
            logger.error("Invalid parameter combination")
            return build_response(400, {
                "error": "Invalid query parameters",
                "details": "Must provide one of: (request_id + created_at), request_id only, or feedback_id only",
                "valid_combinations": [
                    {"request_id": "string", "created_at": "string"},
                    {"request_id": "string"},
                    {"feedback_id": "string"}
                ]
            })

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        return build_response(500, {
            "error": "Internal server error",
            "request_id": request_id if 'request_id' in locals() else None,
            "feedback_id": feedback_id if 'feedback_id' in locals() else None,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })


def test_lambda_handler():
    test_cases = [
        {
            "name": "Composite Key Lookup - Success",
            "query_params": {
                "request_id": "test123",
                "created_at": "1747596267.144289"
            },
            "expected_status": 200
        },
        {
            "name": "request_id Only Lookup - Success",
            "query_params": {
                "request_id": "test123"
            },
            "expected_status": 200
        },
        {
            "name": "feedback_id Lookup - Success",
            "query_params": {
                "feedback_id": "feedback-789"
            },
            "expected_status": 200
        },
        {
            "name": "request_id Only - No Results",
            "query_params": {
                "request_id": "non-existent-id"
            },
            "expected_status": 404
        },
        {
            "name": "Invalid Parameter Combination",
            "query_params": {
                "created_at": "1747596267.144289"
            },
            "expected_status": 400
        }
    ]

    for case in test_cases:
        print(f"\n=== {case['name']} ===")

        event = {
            "queryStringParameters": case["query_params"]
        }

        response = lambda_handler(event, None)

        print("Status Code:", response["statusCode"])
        try:
            parsed_body = json.loads(response["body"])
        except (TypeError, json.JSONDecodeError):
            parsed_body = response["body"]

        print("Body:", json.dumps(parsed_body, indent=2, default=str))
        print("Test", "PASSED" if response["statusCode"]
              == case["expected_status"] else "FAILED")


if __name__ == "__main__":
    test_lambda_handler()
