from agents import Agent

from utils.prompt_template import (
    SENTIMENT_ANALYSIS_INSTRUCTIONS,
    TOPIC_CATEGORIZATION_INSTRUCTIONS,
    KEYWORD_CONTEXTUALIZATION_INSTRUCTIONS,
    SUMMARY_GENERATION_INSTRUCTIONS,
    TOOL_EXECUTOR_INSTRUCTIONS
)
from .schemas import (
    SentimentAnalysisOutput,
    TopicCategorizationOutput,
    KeywordContextualizationOutput,
    SummaryGenerationOutput,
    ToolExecutorOutput
)

sentiment_analysis_tool = Agent(
    name="Sentiment Analysis Tool",
    model="gpt-4o-mini",
    instructions=SENTIMENT_ANALYSIS_INSTRUCTIONS,
    output_type=SentimentAnalysisOutput
)

topic_categorization_tool = Agent(
    name="Topic Categorization Tool",
    model="gpt-4o-mini",
    instructions=TOPIC_CATEGORIZATION_INSTRUCTIONS,
    output_type=TopicCategorizationOutput
)

keyword_contextualization_tool = Agent(
    name="Keyword Contextualization Tool",
    model="gpt-4o-mini",
    instructions=KEYWORD_CONTEXTUALIZATION_INSTRUCTIONS,
    output_type=KeywordContextualizationOutput
)


summary_generation_tool = Agent(
    name="Summary Generation Tool",
    model="gpt-4o-mini",
    instructions=SUMMARY_GENERATION_INSTRUCTIONS,
    output_type=SummaryGenerationOutput
)


tool_executor_tool = Agent(
    name="Tool Executor Tool",
    model="gpt-4o-mini",
    instructions=TOOL_EXECUTOR_INSTRUCTIONS,
    output_type=ToolExecutorOutput,
)
