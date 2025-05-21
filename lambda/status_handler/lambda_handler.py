import boto3
import os
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Union
from dotenv import load_dotenv
from utils.common_functions import build_response
from decimal import Decimal
from boto3.dynamodb.conditions import Key, Attr

load_dotenv(override=True)

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

AWS_REGION = os.getenv("AWS_REGION")
DYNAMODB_TABLE = os.getenv("DYNAMODB_TABLE")
FEEDBACK_ID_GSI_NAME = os.getenv("FEEDBACK_ID_INDEX")

if not DYNAMODB_TABLE:
    raise ValueError("DYNAMODB_TABLE environment variable is not set")

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(DYNAMODB_TABLE)


def validate_and_format_response(item: Dict[str, Any]) -> Dict[str, Any]:
    """Transform DynamoDB item into the standardized response format"""
    try:
        # Extract timestamp from original input or use current time
        feedback_timestamp = item.get('original_input', {}).get('timestamp')
        if not feedback_timestamp:
            feedback_timestamp = datetime.now(timezone.utc).isoformat()

        # Calculate execution time in milliseconds (if available)
        execution_time_ms = None
        guardrail = item.get("guardrail_result", {})
        exec_time = guardrail.get("execution_time")
        if exec_time is not None:
            execution_time_ms = int(float(exec_time) * 1000)

        # -------------------------
        # Build dynamic response
        # -------------------------
        tool_results: Dict[str, Any] = item.get("tool_results", {})

        executed_tools = list(tool_results.keys())

        dynamic_results: Dict[str, Any] = {}
        for tool_name, tool_data in tool_results.items():
            if tool_name == "summary_generation":
                dynamic_results["summarization"] = {
                    "summary": tool_data.get("summary_text", ""),
                    "actionable_recommendations": tool_data.get(
                        "actionable_insights", []
                    ),
                }
            elif tool_name == "sentiment_analysis":
                dynamic_results["sentiment_analysis"] = {
                    "sentiment": tool_data.get("sentiment", "unknown")
                }
            elif tool_name == "topic_categorization":
                dynamic_results["topic_categorization"] = {
                    "categories": tool_data.get("categories", [])
                }
            elif tool_name == "keyword_contextualization":
                dynamic_results["keyword_contextualization"] = {
                    "keywords": tool_data.get("keywords", [])
                }
            else:
                # Fallback: include data as-is under the original tool name
                dynamic_results[tool_name] = tool_data

        # -------------------------
        # Assemble final response
        # -------------------------
        formatted_response = {
            "metadata": {
                "request_id": item.get("request_id"),
                "feedback_id": item.get("feedback_id"),
                "timestamp": feedback_timestamp,
                "status": item.get("status", "").lower(),
                "execution_time_ms": execution_time_ms,
            },
            "analysis": {
                "executed_tools": executed_tools,
                "results": dynamic_results,
            },
            "context": {
                "customer_name": item.get("original_input", {}).get("customer_name"),
                "original_instructions": item.get("instructions", ""),
            },
        }

        # Clean up empty/None values
        for section in ["metadata", "analysis", "context"]:
            formatted_response[section] = {
                k: v for k, v in formatted_response[section].items()
                if v not in (None, "", [])
            }

        return build_response(200, formatted_response)

    except Exception as validation_error:
        logger.error(f"Data transformation error: {str(validation_error)}", exc_info=True)
        return build_response(500, {
            "error": "Data transformation failed",
            "details": str(validation_error)
        })


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Enhanced Lambda function to handle status requests with multiple query options via JSON body"""
    try:
        logger.info(f"Received event: {json.dumps(event)}")

        # Parse JSON body
        body = {}
        if event.get("body"):
            body = json.loads(event["body"])
        qs = event.get("queryStringParameters") or {}

        request_id = body.get("request_id") or qs.get("request_id")
        feedback_id = body.get("feedback_id") or qs.get("feedback_id")
        limit = int(body.get("limit") or qs.get("limit", 100))
        next_token = body.get("next_token") or qs.get("next_token")

        # Validate parameters
        if not any([request_id, feedback_id]):
            return build_response(400, {
                "error": "Missing required parameter",
                "details": "Must provide at least one of: request_id or feedback_id in the request body"
            })

        if limit > 1000:
            return build_response(400, {
                "error": "Limit too large",
                "details": "Maximum limit is 1000"
            })

        # Determine query mode
        if request_id and feedback_id:
            # Combined lookup
            item = get_by_request_and_feedback(request_id, feedback_id)
            if not item:
                return build_response(404, {
                    "error": "No matching record found",
                    "request_id": request_id,
                    "feedback_id": feedback_id
                })
            return validate_and_format_response(item)

        elif request_id:
            # request_id only lookup
            query_result = get_by_request_id(request_id, limit, next_token)
            items = query_result.get("items", [])

            if not items:
                return build_response(404, {
                    "error": "No records found",
                    "request_id": request_id
                })

            # Take the most recent matching item
            first_item = items[0]

            return validate_and_format_response(first_item)

        elif feedback_id:
            # feedback_id lookup
            item = get_by_feedback_id(feedback_id)
            if not item:
                return build_response(404, {
                    "error": "No record found",
                    "feedback_id": feedback_id
                })
            return validate_and_format_response(item)

        return build_response(400, {"error": "Invalid parameters"})

    except ValueError as e:
        logger.error(f"Invalid parameter value: {str(e)}")
        return build_response(400, {"error": str(e)})
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        return build_response(500, {
            "error": "Internal server error",
            "details": str(e)
        })


def get_by_request_and_feedback(request_id: str, feedback_id: str) -> Optional[Dict]:
    """Get specific item by both request_id and feedback_id"""
    try:
        response = table.query(
            KeyConditionExpression=Key('request_id').eq(request_id),
            FilterExpression=Attr('feedback_id').eq(feedback_id),
            Limit=1
        )
        print("response", response)
        return response['Items'][0] if response['Items'] else None
    except Exception as e:
        logger.error(f"Combined lookup failed: {str(e)}")
        raise


def get_by_request_id(request_id: str, limit: int = 100, next_token: Optional[str] = None) -> Dict:
    """Get items by request_id with pagination"""
    try:
        query_params = {
            'KeyConditionExpression': Key('request_id').eq(request_id),
            'Limit': limit,
            'ScanIndexForward': False
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


def get_by_feedback_id(feedback_id: str) -> Optional[Dict[str, Any]]:
    """Get item by GSI (feedback_id)"""
    try:
        response = table.query(
            IndexName=FEEDBACK_ID_GSI_NAME,
            KeyConditionExpression=Key('feedback_id').eq(feedback_id),
            Limit=1
        )
        return response['Items'][0] if response['Items'] else None
    except Exception as e:
        logger.error(f"Error fetching by feedback_id: {str(e)}")
        return None


def format_dynamo_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Format DynamoDB item for response"""
    return {
        "request_id": item.get("request_id"),
        "feedback_id": item.get("feedback_id"),
        "status": item.get("status"),
        "results": item.get("results"),
        "updated_at": format_timestamp(item.get("updated_at")),
        "created_at": format_timestamp(item.get("created_at")),
        "error_message": item.get("error_message"),
        "progress": float(item.get("progress", 0))
    }


def format_timestamp(timestamp: Union[str, Decimal, None]) -> Optional[str]:
    """Convert DynamoDB timestamp to ISO format"""
    if timestamp is None:
        return None
    if isinstance(timestamp, Decimal):
        return datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat()
    return timestamp


def test_lambda_handler():
    test_cases = [
        # {
        #     "name": "feedback_id Lookup - Success",
        #     "body": {"feedback_id": "67890"},
        #     "expected_status": 200
        # },
        {
            "name": "request_id Lookup - Success",
            "body": {"request_id": "4cf62ebd-61b6-4581-b1b6-4fec0e3ef6e4"},
            "expected_status": 200
        }
    ]

    for case in test_cases:
        print(f"\n=== {case['name']} ===")
        event = {"body": json.dumps(case["body"])}
        response = lambda_handler(event, None)
        print("Status Code:", response["statusCode"])
        print("Body:", json.dumps(json.loads(response["body"]), indent=2))
        print("Test", "PASSED" if response["statusCode"] == case["expected_status"] else "FAILED")


if __name__ == "__main__":
    test_lambda_handler()
