import json
import asyncio
import time
import logging
import boto3
import os
from decimal import Decimal
from dotenv import load_dotenv
from typing import Dict, List, Optional, Any
from boto3.dynamodb.conditions import Key

# Local imports
from agents import Runner
from .tools import (
    sentiment_analysis_tool,
    topic_categorization_tool,
    keyword_contextualization_tool,
    summary_generation_tool,
    tool_executor_tool,
)

load_dotenv(override=True)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# AWS Resources
DYNAMODB_TABLE_NAME = os.getenv("DYNAMODB_TABLE")
AWS_REGION = os.getenv("AWS_REGION")

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(DYNAMODB_TABLE_NAME)


class ToolExecutor:
    def __init__(self):
        self.tool_map = {
            "sentiment_analysis": sentiment_analysis_tool,
            "topic_categorization": topic_categorization_tool,
            "keyword_contextualization": keyword_contextualization_tool,
            "summary_generation": summary_generation_tool,
        }

    async def execute_tools(
        self, tools: list, input_text: str, execution_mode: str = "PARALLEL"
    ) -> dict:
        """
        Execute tools either in parallel or sequentially
        Args:
            tools: List of tool names to execute
            input_text: Text to process
            execution_mode: "PARALLEL" or "SEQUENTIAL"
        Returns:
            Dictionary of tool results
        """
        if execution_mode == "PARALLEL":
            return await self._execute_parallel(tools, input_text)
        return await self._execute_sequential(tools, input_text)

    async def _execute_parallel(self, tools: list, input_text: str) -> dict:
        """Execute multiple tools concurrently"""
        tasks = []
        for tool_name in tools:
            if tool_name not in self.tool_map:
                logger.warning(f"Unknown tool skipped: {tool_name}")
                continue
            tasks.append(
                Runner.run(starting_agent=self.tool_map[tool_name], input=input_text)
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)
        return self._format_results(tools, results)

    async def _execute_sequential(self, tools: List[str], input_text: str) -> Dict:
        """Execute tools one after another with error isolation"""
        results = {}
        for tool_name in tools:
            try:
                if tool_name not in self.tool_map:
                    logger.warning(f"Unknown tool skipped: {tool_name}")
                    continue

                result = await Runner.run(
                    starting_agent=self.tool_map[tool_name], input=input_text
                )
                results[tool_name] = result.final_output.model_dump()
            except Exception as e:
                results[tool_name] = {"error": str(e)}
                logger.error(f"Tool {tool_name} failed: {str(e)}")
        return results

    def _format_results(self, tools: List[str], results: List) -> Dict:
        """Standardize output format for both success and error cases"""
        output = {}
        for tool_name, result in zip(tools, results):
            if isinstance(result, Exception):
                error_msg = str(result)
                if isinstance(result, asyncio.TimeoutError):
                    error_msg = "Tool execution timed out"
                output[tool_name] = {"error": error_msg}
                logger.error(f"Tool {tool_name} failed: {error_msg}")
            else:
                output[tool_name] = result.final_output.model_dump()
        return output


async def get_state(request_id: str) -> Optional[Dict]:
    """Get the current state for a request_id"""
    try:
        response = table.get_item(Key={"request_id": request_id})
        return response.get("Item")
    except Exception as e:
        logger.error(f"DynamoDB get_item failed: {str(e)}")
        raise


def convert_floats_to_decimals(obj: Any) -> Any:
    """Recursively convert all floats to Decimals in a data structure"""
    if isinstance(obj, float):
        # Convert via string to avoid precision issues
        return Decimal(str(obj))
    elif isinstance(obj, dict):
        return {k: convert_floats_to_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [convert_floats_to_decimals(i) for i in obj]
    return obj


async def update_state(request_id: str, status: str, **kwargs) -> bool:
    """Update state in DynamoDB using just request_id as key"""
    update_expr = ["SET #status = :status, updated_at = :now"]
    attr_values = {":status": status, ":now": Decimal(str(time.time()))}

    # Handle optional fields
    if "results" in kwargs:
        update_expr.append("tool_results = :results")
        attr_values[":results"] = convert_floats_to_decimals(kwargs["results"])

    if "error" in kwargs:
        update_expr.append("error_message = :error")
        attr_values[":error"] = str(kwargs["error"])

    if "feedback_id" in kwargs:
        update_expr.append("feedback_id = :feedback_id")
        attr_values[":feedback_id"] = kwargs["feedback_id"]

    try:
        # Convert all numeric values in attr_values to Decimal
        attr_values = convert_floats_to_decimals(attr_values)

        response = table.update_item(
            Key={"request_id": request_id},
            UpdateExpression=", ".join(update_expr),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=attr_values,
            ReturnValues="UPDATED_NEW",
        )

        logger.debug(f"State updated successfully: {response}")
        return True

    except table.meta.client.exceptions.ConditionalCheckFailedException:
        logger.warning(f"Conditional update failed for request {request_id}")
        return False
    except table.meta.client.exceptions.ProvisionedThroughputExceededException:
        logger.warning("Throughput exceeded - retry might succeed")
        return False
    except Exception as e:
        logger.error(
            f"Failed to update state for request {request_id}: {str(e)}",
            exc_info=True,
            extra={
                "request_id": request_id,
                "update_expression": ", ".join(update_expr),
                "attribute_values": attr_values,
            },
        )
        return False


async def handle_record(record: Dict) -> bool:
    """Process a single SQS record"""
    try:
        message = json.loads(record["body"])
        request_id = message.get("request_id")
        feedback_id = message.get("feedback_id")
        instructions = message.get("instructions")

        logger.info(f"Processing request {request_id}")

        # Get current state
        state = await get_state(request_id)
        if not state:
            logger.error(f"No state found for request_id: {request_id}")
            return False

        # Update status to PROCESSING first
        await update_state(request_id, "PROCESSING", feedback_id=feedback_id)

        # Get tool execution plan
        try:
            tool_plan = await Runner.run(
                starting_agent=tool_executor_tool, input=instructions
            )
            if isinstance(tool_plan.final_output, str):
                try:
                    final_output_parsed = json.loads(tool_plan.final_output)
                    tools_to_execute = final_output_parsed.get("use_tools", [])
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to decode final_output JSON: {str(e)}")
                    await update_state(
                        request_id, "FAILED", error="Invalid tool plan format", feedback_id=feedback_id
                    )
                    return False
            else:
                tools_to_execute = tool_plan.final_output.use_tools
        except Exception as e:
            logger.error(f"Tool planning failed: {str(e)}")
            await update_state(
                request_id, "FAILED", error=str(e), feedback_id=feedback_id
            )
            return False

        # Execute tools
        executor = ToolExecutor()
        try:
            tool_results = await executor.execute_tools(
                tools_to_execute,
                state["original_input"].get("feedback_text", ""),
                state.get("execution_mode", "PARALLEL"),
            )

            await update_state(
                request_id, "COMPLETED", results=tool_results, feedback_id=feedback_id
            )
            return True

        except Exception as e:
            logger.error(f"Tool execution failed: {str(e)}")
            await update_state(
                request_id, "FAILED", error=str(e), feedback_id=feedback_id
            )
            return False

    except Exception as e:
        logger.error(f"Record processing failed: {str(e)}")
        return False


def lambda_handler(event, context):
    """Main Lambda entry point"""

    async def process_records():
        results = await asyncio.gather(
            *[handle_record(record) for record in event.get("Records", [])],
            return_exceptions=True,
        )

        success_count = sum(1 for r in results if r is True)
        if success_count != len(event.get("Records", [])):
            raise Exception(
                f"Processed {success_count}/{len(event.get('Records', []))} records successfully"
            )

    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(process_records())
        return {
            "statusCode": 200,
            "body": json.dumps({"message": "Processing completed"}),
        }
    except Exception as e:
        logger.error(f"Lambda handler failed: {str(e)}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


if __name__ == "__main__":
    # Test with mock SQS event
    test_event = {
        "Records": [
            {
                "body": json.dumps(
                    {
                        "request_id": "test123",
                        "feedback_id": "feedback456",
                        "instructions": "Analyze sentiment and summarize",
                    }
                )
            }
        ]
    }

    # Initialize test state in DynamoDB
    table.put_item(
        Item={
            "request_id": "test123",
            "feedback_id": "feedback456",
            "status": "PROCESSING",
            "original_input": {
                "feedback_text": "The product is great and delivery was also awesome",
                "instructions": "Analyze sentiment and summarize",
            },
            "created_at": str(time.time()),
            "updated_at": str(time.time()),
            "execution_mode": "PARALLEL",
        }
    )

    # Run the handler
    result = lambda_handler(test_event, None)
    print("Execution result:", result)

    # Check final state
    final_state = table.get_item(Key={"request_id": "test123"}).get("Item", {})
    print("Final state:", json.dumps(final_state, indent=2, default=str))
