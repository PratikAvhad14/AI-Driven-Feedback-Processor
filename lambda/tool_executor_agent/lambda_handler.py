import json
import asyncio
import time
import logging
import boto3
import os
from decimal import Decimal
from dotenv import load_dotenv
from agents import Runner
from .tools import (
    sentiment_analysis_tool,
    topic_categorization_tool,
    keyword_contextualization_tool,
    summary_generation_tool,
    tool_executor_tool
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
            "summary_generation": summary_generation_tool
        }

    async def execute_tools(self, tools: list, input_text: str, execution_mode: str = "PARALLEL") -> dict:
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
                Runner.run(
                    starting_agent=self.tool_map[tool_name],
                    input=input_text
                )
            )
 
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return self._format_results(tools, results)

    async def _execute_sequential(self, tools: list, input_text: str) -> dict:
        """Execute tools one after another"""
        results = {}
        for tool_name in tools:
            try:
                if tool_name not in self.tool_map:
                    logger.warning(f"Unknown tool skipped: {tool_name}")
                    continue

                result = await Runner.run(
                    starting_agent=self.tool_map[tool_name],
                    input=input_text
                )
                results[tool_name] = result.final_output.model_dump()
            except Exception as e:
                results[tool_name] = {"error": str(e)}
                logger.error(f"Tool {tool_name} failed: {str(e)}")
        return results

    def _format_results(self, tools: list, results: list) -> dict:
        """Format parallel execution results"""
        output = {}
        for tool_name, result in zip(tools, results):
            if isinstance(result, Exception):
                output[tool_name] = {"error": str(result)}
                logger.error(f"Tool {tool_name} failed: {str(result)}")
            else:
                output[tool_name] = result.final_output.model_dump()
        return output


async def handle_record(record: dict):
    """Process a single SQS record"""
    try:
        message = json.loads(record["body"])
        request_id = message["request_id"]
        feedback_id = message.get("feedback_id")
        instructions = message.get("instructions")

        # Get current state from DynamoDB
        state = table.get_item(Key={"request_id": request_id}).get("Item", {})
        if not state:
            logger.error(f"No state found for request_id: {request_id}")
            return

        # Determine execution mode (default to parallel)
        execution_mode = state.get("execution_mode", "PARALLEL")
        input_text = state["original_input"].get("feedback_text", "")

        # Get tool execution plan
        try:
            tool_plan = await Runner.run(
                starting_agent=tool_executor_tool,
                input=instructions
            )
            tools_to_execute = tool_plan.final_output.use_tools
        except Exception as e:
            logger.error(f"Tool planning failed: {str(e)}")
            await update_state(request_id, "FAILED", error=str(e))
            return

        # Execute tools
        executor = ToolExecutor()
        try:
            tool_results = await executor.execute_tools(
                tools_to_execute,
                input_text,
                execution_mode
            )

            # Update state with results
            await update_state(
                request_id,
                "COMPLETED",
                results=tool_results,
                feedback_id=feedback_id
            )

        except Exception as e:
            logger.error(f"Tool execution failed: {str(e)}")
            await update_state(
                request_id,
                "FAILED",
                error=str(e),
                feedback_id=feedback_id
            )

    except Exception as e:
        logger.error(f"Record processing failed: {str(e)}")


async def update_state(request_id: str, status: str, **kwargs):
    """Update DynamoDB state with additional fields"""
    update_expr = "SET #status = :status, updated_at = :now"
    attr_values = {
        ":status": status,
        ":now": Decimal(str(time.time()))
    }

    if "results" in kwargs:
        update_expr += ", tool_results = :results"
        attr_values[":results"] = kwargs["results"]

    if "error" in kwargs:
        update_expr += ", error_message = :error"
        attr_values[":error"] = kwargs["error"]

    try:
        table.update_item(
            Key={"request_id": request_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=attr_values
        )
    except Exception as e:
        logger.error(f"Failed to update state: {str(e)}")
        raise


def lambda_handler(event, context):
    """Main Lambda entry point"""
    loop = asyncio.get_event_loop()
    try:
        # Process all SQS records
        for record in event.get("Records", []):
            loop.run_until_complete(handle_record(record))

        return {
            "statusCode": 200,
            "body": "Tool execution completed"
        }
    except Exception as e:
        logger.error(f"Lambda handler failed: {str(e)}")
        return {
            "statusCode": 500,
            "body": f"Error: {str(e)}"
        }
    finally:
        loop.close()


# Testing
if __name__ == "__main__":
    # Mock SQS event
    test_event = {
        "Records": [
            {
                "body": json.dumps({
                    "request_id": "test123",
                    "feedback_id": "feedback456",
                    "instructions": "Analyze sentiment and summarize"
                })
            }
        ]
    }

    # Initialize test state in DynamoDB
    table.put_item(Item={
        "request_id": "test123",
        "feedback_id": "feedback456",
        "status": "PROCESSING",
        "original_input": {
            "feedback_text": "The product is great but delivery was slow",
            "instructions": "Analyze sentiment and summarize"
        },
        "created_at": str(time.time()),
        "updated_at": str(time.time())
    })

    # Run the handler
    result = lambda_handler(test_event, None)
    print("Execution result:", result)

    # Check final state
    final_state = table.get_item(Key={
        "request_id": "test123",
        "created_at": "1747596267.144289"}).get("Item", {})
    print("Final state:", json.dumps(final_state, indent=2, default=str))
