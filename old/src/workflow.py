import logging
from typing import List

from agno.db.sqlite import SqliteDb
from agno.workflow import StepInput, StepOutput, Step, Router, Steps
from agno.workflow.workflow import Workflow

from src.agents import plan_agent, research_agent, reporter_agent, coordinate_agent, chat_agent
from src.prompts.models import Plan, Coordinate

# 设置日志
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

def generate_search_prompt(current_plan, locale: str = "en-US") -> str:
    """
    Generate search prompt information for the research agent

    This function builds a prompt containing research background and current task
    based on the current plan and completed steps for the research agent to use
    when performing web searches.

    Args:
        current_plan: Current research plan object containing multiple steps
        locale (str, optional): Localization setting, e.g. 'en-US' or 'zh-CN'.
                               Defaults to 'en-US'

    Returns:
        str: Constructed search prompt information string
    """
    current_step = None
    completed_steps = []
    for step in current_plan.steps:
        if not step.execution_res:
            current_step = step
            break
        else:
            completed_steps.append(step)

    # Format completed steps information
    completed_steps_info = ""
    if completed_steps:
        completed_steps_info = "# Existing Research Findings\n\n"
        for i, step in enumerate(completed_steps):
            completed_steps_info += f"## Existing Finding {i + 1}: {step.title}\n\n"
            # completed_steps_info += f"<finding>\n{step.execution_res}\n</finding>\n\n" # tokens maker!!!!!!

    # Prepare the input for the agent with completed steps info
    agent_input = f"{completed_steps_info}# Current Task\n\n## Title\n\n{current_step.title}\n\n## Description\n\n{current_step.description}\n\n## Locale\n\n{locale}"

    return agent_input
def research_step(step_input: StepInput, session_state: dict) -> StepOutput:
    """
    Workflow step function to execute research tasks

    This function iterates through each step in the plan. If a step requires web search,
    it calls the research agent to perform the search and stores the search results
    in the step's execution result.

    Args:
        step_input (StepInput): Workflow step input object containing the result
                               of the previous step
        session_state (dict): Session state dictionary containing relevant information
                             for the current session

    Returns:
        StepOutput: Step output containing the updated plan object
    """
    plan = Plan.model_validate(step_input.previous_step_content)

    # call research to search plan
    for step in plan.steps:
        logger.info("----------investigate step as blow--------------")
        logger.info(step)
        logger.info("------------------------------------------------")
        if step.need_web_search and (step.execution_res is None):
            message = generate_search_prompt(plan)
            run_response = research_agent.run(message)
            step.execution_res = run_response # TODO test run_response
        else:
            logger.info("No need web search")

    # Return the content plan
    return StepOutput(content=plan)

def reporter_step(step_input: StepInput, session_state: dict) -> StepOutput:
    """
    Workflow step function to generate the final answer

    This function generates the final response based on the output of the previous step.
    If the previous step output is of type Coordinate, it directly returns the reply content;
    if it is of type Plan, it calls the reporter agent to generate a research report.

    Args:
        step_input (StepInput): Workflow step input object containing the result
                               of the previous step
        session_state (dict): Session state dictionary containing relevant information
                             for the current session

    Returns:
        StepOutput: Step output containing the final response content
    """
    plan_content = step_input.get_step_content("plan")
    plan = Plan.model_validate(plan_content)
    # conclusion
    input_ = f"# Research Requirements\n\n## Task\n\n{plan.title}\n\n## Description\n\n{plan.thought}\n\n"
    input_ += "IMPORTANT: Structure your report according to the format in the prompt. Remember to include:\n\n1. Key Points - A bulleted list of the most important findings\n2. Overview - A brief introduction to the topic\n3. Detailed Analysis - Organized into logical sections\n4. Survey Note (optional) - For more comprehensive reports\n5. Key Citations - List all references at the end\n\nFor citations, DO NOT include inline citations in the text. Instead, place all citations in the 'Key Citations' section at the end using the format: `- [Source Title](URL)`. Include an empty line between each citation for better readability.\n\nPRIORITIZE USING MARKDOWN TABLES for data presentation and comparison. Use tables whenever presenting comparative data, statistics, features, or options. Structure tables with clear headers and aligned columns. Example table format:\n\n| Feature | Description | Pros | Cons |\n|---------|-------------|------|------|\n| Feature 1 | Description 1 | Pros 1 | Cons 1 |\n| Feature 2 | Description 2 | Pros 2 | Cons 2 |"

    for p in plan.steps:
        if p.need_web_search and p.execution_res:
            input_ += f"\n\nBelow are some observations for the research task:\n\n{p.execution_res}"

    response = reporter_agent.run(input_.strip())

    return StepOutput(content=response.content)

def reply_step(step_input: StepInput, session_state: dict) -> StepOutput:
    coordinate_data = step_input.get_step_content("coordinate") or ""

    if isinstance(coordinate_data, Coordinate):
        intent = Coordinate.model_validate(coordinate_data)
        return StepOutput(content=intent.reply)
    else:
        return StepOutput(content="Please make a question")

reply_sequence = Steps(
    name="polite reply",
    description="Just give a simple reply",
    steps=[
        Step(name="reply", executor=reply_step),
    ],
)

# Define two completely different workflows as Steps
deep_search_sequence = Steps(
    name="reporter_sequence",
    description="Create research plan, execute web search for each step, and generate comprehensive report",
    steps=[
        Step(name="plan", agent=plan_agent),
        Step(name="research", executor=research_step),
        Step(name="report", executor=reporter_step),
    ],
)
def selector(step_input) -> List[Step]:
    """Route to appropriate pipeline"""

    if not step_input.input or not step_input.previous_step_content:
        return [reply_sequence]

    previous_step_content = step_input.previous_step_content

    if not previous_step_content or not isinstance(previous_step_content, Coordinate):
        return [reply_sequence]

    intent = Coordinate.model_validate(previous_step_content)
    if intent.hande_to_plan:
        return [deep_search_sequence]
    else:
        return [reply_sequence]


def final_step(step_input: StepInput, session_state: dict) -> StepOutput:
    return StepOutput(content=step_input.previous_step_content)

# TODO: add async call
workflow = Workflow(
    name="DeepSearch Workflow",
    description="""
    A deep search workflow,
    clarify user input and make a plan,
    then use web search tools to gather information according to the plan,
    finally make a report.
    """,
    db=SqliteDb(db_file="tmp/deepsearch.db"),
    steps=[
        Step(name="coordinate", agent=coordinate_agent),
        Router(
            name="Choose Plan or Common reply",
            description="Check if we should make a plan for topic",
            selector=selector,
            choices=[deep_search_sequence,reply_sequence],
        ),
        Step(name="Final", executor=final_step)
    ],
    session_state={
        "locale": "zh-CN",
        "max_plan_iterations": 1,
        "max_step_num": 5,
        "enable_background_investigation": True,
    }
)

