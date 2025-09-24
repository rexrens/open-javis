from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.tools.duckduckgo import DuckDuckGoTools
from agno.tools.jina import JinaReaderTools
from agno.tools.tavily import TavilyTools

from src.config import get_config_by_type
from src.llm import get_llm_by_type
from src.prompts.models import Plan, Coordinate
from src.prompts.template import get_prompt_template

debug = True

coordinate_agent: Agent = Agent(
    name="Coordinate Agent",
    model=get_llm_by_type("basic"),
    instructions=get_prompt_template("coordinate"),
    add_datetime_to_context=True,
    debug_mode=debug,
    output_schema=Coordinate,
    use_json_mode=True,
)

plan_agent = Agent(
    name="Plan Agent",
    model=get_llm_by_type("basic"),
    instructions=get_prompt_template("planner"),
    add_datetime_to_context=True,
    debug_mode=debug,
    output_schema=Plan,
    use_json_mode=True,
)

research_agent = Agent(
    name="Research Agent",
    model=get_llm_by_type("reasoning"),
    tools=[
        TavilyTools(api_key=get_config_by_type("SEARCH_CONFIG").get('TAVILY_API_KEY')),
        JinaReaderTools(api_key=get_config_by_type("SEARCH_CONFIG").get('JINA_API_KEY'))
    ],
    instructions=get_prompt_template("researcher"),
    add_datetime_to_context=True,
    debug_mode=debug,
)

reporter_agent = Agent(
    name="Conclusion Agent",
    model=get_llm_by_type("reasoning"),
    role="""
    You are a distinguished academic researcher and scholarly writer. Your report must embody the highest standards of academic rigor and intellectual discourse. Write with the precision of a peer-reviewed journal article, employing sophisticated analytical frameworks, comprehensive literature synthesis, and methodological transparency. Your language should be formal, technical, and authoritative, utilizing discipline-specific terminology with exactitude. Structure arguments logically with clear thesis statements, supporting evidence, and nuanced conclusions. Maintain complete objectivity, acknowledge limitations, and present balanced perspectives on controversial topics. The report should demonstrate deep scholarly engagement and contribute meaningfully to academic knowledge
    You should act as an objective and analytical reporter who:
    - Presents facts accurately and impartially.
    - Organizes information logically.
    - Highlights key findings and insights.
    - Uses clear and concise language.
    - To enrich the report, includes relevant images from the previous steps.
    - Relies strictly on provided information.
    - Never fabricates or assumes information.
    - Clearly distinguishes between facts and analysis
    """,
    instructions=get_prompt_template("reporter"),
    markdown=True
)

visual_learning_agent = Agent(
    name="Visual Learning Agent",
    model=get_llm_by_type("vision"),
    role="Answer user questions about the image.",
    markdown=True,
)

chat_agent = Agent(
    name="Chat Agent",
    model=get_llm_by_type("basic"),
    role="Answer user questions in a conversational manner",
    tools=[TavilyTools(api_key=get_config_by_type("SEARCH_CONFIG").get('TAVILY_API_KEY'))],
    instructions=["Always include sources"],
    # Store the agent sessions in a sqlite database
    db=SqliteDb(
        db_file="tmp/chat.db",
    ),
    # Adds the current date and time to the context
    add_datetime_to_context=True,
    # Adds the history of the conversation to the messages
    enable_user_memories=True,
    add_history_to_context=True,
    # Number of history responses to add to the messages
    num_history_runs=5,
    # Adds Markdown formatting to the messages
    markdown=True,
)
