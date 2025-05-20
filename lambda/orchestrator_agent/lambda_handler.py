import json
import asyncio
import logging
import time
import boto3
import os
from uuid import uuid4
from decimal import Decimal
from dotenv import load_dotenv
from agents import Runner
from .tools import guardrail_tool
from utils.common_functions import build_response
from typing import Optional

load_dotenv(override=True)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# AWS Resources Configuration
TOOL_EXECUTOR_QUEUE_URL = os.getenv("TOOL_EXECUTOR_QUEUE_URL")
DYNAMODB_TABLE_NAME = os.getenv("DYNAMODB_TABLE")
AWS_REGION = os.getenv("AWS_REGION")
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", 3600))  # Default 1 hour cache
FEEDBACK_ID_INDEX = os.getenv("FEEDBACK_ID_INDEX")

# AWS Clients Initialization
sqs = boto3.client("sqs", region_name=AWS_REGION)
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(DYNAMODB_TABLE_NAME)


class StateManager:
    """Handles all DynamoDB state operations"""

    def __init__(self, table):
        self.table = table

    async def save_state(self, request_id, state_data):
        """Save or update request state"""
        state_data["updated_at"] = str(time.time())
        try:
            self.table.put_item(Item=state_data)
        except Exception as e:
            logger.error(f"Failed to save state: {str(e)}")
            raise

    async def get_cached_result(self, feedback_id, instructions, index_name):
        """Check for valid cached results"""
        try:
            response = self.table.query(
                IndexName=index_name,
                KeyConditionExpression="feedback_id = :fid",
                FilterExpression="instructions = :inst AND #status = :stat",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":fid": feedback_id,
                    ":inst": instructions,
                    ":stat": "COMPLETED"
                },
                ScanIndexForward=False,
                Limit=1
            )

            if response.get("Items"):
                item = response["Items"][0]
                if time.time() < float(item.get("expire_at", 0)):
                    return item["tool_results"]
        except Exception as e:
            logger.warning(f"Cache check failed: {str(e)}")
        return None

    async def get_existing_request_id(self, feedback_id: str, instructions: str, index_name: str) -> Optional[str]:
        """Return existing request_id for identical feedback_id+instructions if present"""
        try:
            response = self.table.query(
                IndexName=index_name,
                KeyConditionExpression="feedback_id = :fid",
                FilterExpression="instructions = :inst",
                ExpressionAttributeValues={
                    ":fid": feedback_id,
                    ":inst": instructions
                },
                ScanIndexForward=False,
                Limit=1
            )
            if response.get("Items"):
                return response["Items"][0].get("request_id")
        except Exception as e:
            logger.warning(f"Existing request lookup failed: {str(e)}")
        return None

    async def is_active_request(self, feedback_id: str, instructions: str, index_name: str) -> bool:
        """Check if a request is already PENDING or PROCESSING"""
        try:
            response = self.table.query(
                IndexName=index_name,
                KeyConditionExpression="feedback_id = :fid",
                FilterExpression="instructions = :inst AND #status IN (:pend, :proc)",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":fid": feedback_id,
                    ":inst": instructions,
                    ":pend": "PENDING",
                    ":proc": "PROCESSING"
                },
                Limit=1
            )
            return bool(response.get("Items"))
        except Exception as e:
            logger.warning(f"Active request check failed: {str(e)}")
        return False


class Orchestrator:
    def __init__(self):
        self.state_manager = StateManager(table)

    async def handle_request(self, event):
        """Main request handler for the orchestrator"""
        try:
            # 1. Parse and validate input
            body = self.parse_event_body(event)
            feedback_id = body.get("feedback_id", str(uuid4()))
            instructions = body.get("instructions")

            # 2. Check for existing processing requests (PENDING or PROCESSING)
            if instructions and await self.state_manager.is_active_request(feedback_id, instructions, FEEDBACK_ID_INDEX):
                return build_response(409, {
                    "feedback_id": feedback_id,
                    "status": "CONFLICT",
                    "message": "Similar request already in progress"
                })
            # 3. Reuse existing request_id if this feedback_id + instructions already processed
            request_id = await self.state_manager.get_existing_request_id(
                feedback_id, instructions, FEEDBACK_ID_INDEX
            ) or str(uuid4())
            state_data = self.build_initial_state(
                request_id, feedback_id, body)

            # 4. Run guardrail validation
            guardrail_result = await self.validate_guardrails(instructions)
            if not guardrail_result["is_safe"]:
                state_data.update({
                    "status": "REJECTED",
                    "guardrail_result": guardrail_result
                })
                await self.state_manager.save_state(request_id, state_data)
                return build_response(400, {
                    "request_id": request_id,
                    "feedback_id": feedback_id,
                    "status": "REJECTED",
                    "reason": guardrail_result.get("reason", "Unsafe content")
                })

            # 5. Process valid request
            state_data.update({
                "guardrail_result": guardrail_result,
                "status": "PROCESSING"
            })

            # If guardrail produced sanitized_text, use it for downstream processing
            sanitized_text = guardrail_result.get("sanitized_text")
            if sanitized_text:
                # Update the feedback text in the original input so tool-executor uses sanitized version
                state_data["original_input"]["feedback_text"] = sanitized_text

            await self.state_manager.save_state(request_id, state_data)

            # Queue for tool execution
            sqs.send_message(
                QueueUrl=TOOL_EXECUTOR_QUEUE_URL,
                MessageBody=json.dumps({
                    "request_id": request_id,
                    "feedback_id": feedback_id,
                    "instructions": instructions,
                    "sanitized_text": sanitized_text,
                    "original_input": state_data.get("original_input", {}),
                    "execution_mode": state_data.get("execution_mode", "PARALLEL")
                })
            )

            return build_response(200, {
                "request_id": request_id,
                "feedback_id": feedback_id,
                "status_url": f"/status/{request_id}",
                "status": "PROCESSING",
                **({"sanitized_text": sanitized_text} if sanitized_text else {})
            })

        except Exception as e:
            logger.error(f"Orchestrator error: {str(e)}", exc_info=True)
            return build_response(500, {"error": str(e)})

    def parse_event_body(self, event):
        """Parse event body from API Gateway"""
        body = event.get("body", "{}")
        return json.loads(body) if isinstance(body, str) else body

    def build_initial_state(self, request_id, feedback_id, body):
        """Create initial state object for DynamoDB"""
        return {
            "request_id": request_id,
            "feedback_id": feedback_id,
            "status": "PENDING",
            "created_at": str(time.time()),
            "updated_at": str(time.time()),
            "expire_at": int(time.time()) + CACHE_TTL,
            "original_input": body,
            "instructions": body.get("instructions")
        }

    async def validate_guardrails(self, instructions):
        """Run guardrail validation"""
        if not instructions:
            return {"is_safe": False, "reason": "No instructions provided"}

        try:
            guardrail_start_time = time.time()
            guardrail_result = await Runner.run(
                starting_agent=guardrail_tool,
                input=instructions
            )
            result = guardrail_result.final_output.model_dump()
            result["execution_time"] = Decimal(
                str(time.time() - guardrail_start_time))
            return result
        except Exception as e:
            logger.error(f"Guardrail validation failed: {str(e)}")
            return {
                "is_safe": False,
                "reason": f"Guardrail validation failed: {str(e)}"
            }

    async def is_request_in_progress(self, feedback_id, instructions):
        """Check if similar request is already being processed"""
        cached = await self.state_manager.get_cached_result(
            feedback_id,
            instructions,
            FEEDBACK_ID_INDEX
        )
        return cached is not None


# Lambda handler functions
def lambda_handler(event, context):
    orchestrator = Orchestrator()
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(orchestrator.handle_request(event))


# Testing
if __name__ == "__main__":
    test_cases = [
        {
            "name": "Happy-path new request",
            "body": {
                "feedback_id": "fb_001",
                "feedback_text": "Great product but delivery was slow",
                "instructions": "Analyze sentiment and summarize"
            }
        },
        {
            "name": "Duplicate in-progress request (should return 409)",
            "body": {
                "feedback_id": "fb_001",  # same as first case
                "feedback_text": "Great product but delivery was slow",
                "instructions": "Analyze sentiment and summarize"
            }
        },
        {
            "name": "Unsafe instructions (guardrail reject)",
            "body": {
                "feedback_id": "fb_002",
                "feedback_text": "Please write a hateful review",
                "instructions": "Generate hateful content"
            }
        },
        {
            "name": "Missing instructions (should 400)",
            "body": {
                "feedback_id": "fb_003",
                "feedback_text": "Average quality"
            }
        },
        {
            "name": "Request requiring sanitization",
            "body": {
                "feedback_id": "fb_004",
                "feedback_text": "This product is damn bad!!!",
                "instructions": "Sanitize offensive words and analyze sentiment."
            }
        }
    ]

    for case in test_cases:
        print(f"\n=== {case['name']} ===")
        event = {"body": json.dumps(case["body"])}
        resp = lambda_handler(event, context=None)
        print(json.dumps(resp, indent=2))
