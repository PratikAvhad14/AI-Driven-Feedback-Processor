import boto3
import os
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Union
from .schemas import StatusResponse
from dotenv import load_dotenv
from utils.common_functions import build_response
from decimal import Decimal
from boto3.dynamodb.conditions import Key

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
    """Enhanced Lambda function to handle status requests with multiple query options

    Supports:
    - Lookup by request_id only (returns all versions if multiple exist)
    - Lookup by feedback_id only (returns single most recent item)
    - Combined request_id + feedback_id lookup (returns specific item)
    - Pagination support for large result sets
    """
    try:
        logger.info(f"Received event: {json.dumps(event)}")
        query_params = event.get("queryStringParameters", {}) or {}
        # Extract and validate parameters
        request_id = query_params.get("request_id")
        feedback_id = query_params.get("feedback_id")
        limit = int(query_params.get("limit", "100"))
        next_token = query_params.get("next_token")
        # Validate parameters
        if not any([request_id, feedback_id]):
            return build_response(400, {
                "error": "Missing required parameter",
                "details": "Must provide at least one of: request_id or feedback_id",
                "valid_parameters": [
                    {"request_id": "string"},
                    {"feedback_id": "string"},
                    {"request_id": "string", "feedback_id": "string"}
                ]
            })

        if limit > 1000:
            return build_response(400, {
                "error": "Limit too large",
                "details": "Maximum limit is 1000",
                "provided_limit": limit
            })

        # Determine query mode
        if request_id and feedback_id:
            # Combined lookup - most specific
            logger.info(
                f"Fetching specific record for request_id: {request_id}, feedback_id: {feedback_id}")
            item = get_by_request_and_feedback(request_id, feedback_id)
            if not item:
                return build_response(404, {
                    "error": "No matching record found",
                    "request_id": request_id,
                    "feedback_id": feedback_id
                })
            return validate_and_format_response(item)

        elif request_id:
            # request_id only lookup - may return multiple items
            logger.info(f"Fetching records for request_id: {request_id}")
            result = get_by_request_id(request_id, limit, next_token)

            if not result.get('items'):
                return build_response(404, {
                    "error": "No records found",
                    "request_id": request_id
                })

            response_data = {
                "items": result['items'],
                "count": len(result['items']),
                "request_id": request_id
            }

            if result.get('next_token'):
                response_data['next_token'] = result['next_token']

            return build_response(200, response_data)

        elif feedback_id:
            # feedback_id lookup - should return single item
            logger.info(f"Fetching by feedback_id: {feedback_id}")
            item = get_by_feedback_id(feedback_id)

            if not item:
                return build_response(404, {
                    "error": "No record found",
                    "feedback_id": feedback_id
                })

            return validate_and_format_response(item)

        # Fallback (should not be reached, but keeps static analysis happy)
        return build_response(500, {"error": "Unhandled parameter combination"})

    except ValueError as e:
        logger.error(f"Invalid parameter value: {str(e)}")
        return build_response(400, {
            "error": "Invalid parameter value",
            "details": str(e)
        })
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        return build_response(500, {
            "error": "Internal server error",
            "details": str(e),
            "request_id": request_id if 'request_id' in locals() else None,
            "feedback_id": feedback_id if 'feedback_id' in locals() else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trace_id": context.aws_request_id if context else None
        })


def get_by_request_and_feedback(request_id: str, feedback_id: str) -> Optional[Dict]:
    """Get specific item by both request_id and feedback_id"""
    try:
        response = table.query(
            IndexName='feedback_id-index',  # Assuming GSI on feedback_id
            KeyConditionExpression=Key('feedback_id').eq(
                feedback_id) & Key('request_id').eq(request_id),
            Limit=1
        )
        return response['Items'][0] if response['Items'] else None
    except Exception as e:
        logger.error(f"Combined lookup failed: {str(e)}")
        raise


def get_by_request_id(request_id: str, limit: int = 100, next_token: Optional[str] = None) -> Dict:
    """Get items by request_id with pagination support"""
    try:
        query_params = {
            'KeyConditionExpression': Key('request_id').eq(request_id),
            'Limit': limit,
            'ScanIndexForward': False  # Get most recent first
        }
        if next_token:
            query_params['ExclusiveStartKey'] = json.loads(next_token)

        response = table.query(**query_params)

        return {
            'items': response['Items'],
            'next_token': response.get('LastEvaluatedKey')
        }
    except Exception as e:
        logger.error(f"Request ID lookup failed: {str(e)}")
        raise


def format_timestamp(timestamp: Union[str, Decimal]) -> str:
    """Convert DynamoDB timestamp to ISO format"""
    if isinstance(timestamp, Decimal):
        return datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat()
    return timestamp


def test_lambda_handler():
    test_cases = [
        {
            "name": "request_id  Lookup - Success",
            "query_params": {
                "request_id": "e2f4b967-4345-46c5-b402-4e24adbd7647"
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
