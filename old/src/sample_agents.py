from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.tools.baidusearch import BaiduSearchTools
from agno.tools.duckduckgo import DuckDuckGoTools
from agno.tools.tavily import TavilyTools

from src.llm import get_llm_by_type

# Setup your database
db = SqliteDb(db_file="agno_test.db")
db2 = SqliteDb(db_file="agno_test_tools.db")
db_kg = SqliteDb(db_file="agno_test_kg.db")

just_llm = Agent(
    name="Just LLM",
    model=get_llm_by_type("basic"),
)

llm_with_mem = Agent(
    name="LLM with Mem",
    model=get_llm_by_type("basic"),
    db=db,
    enable_user_memories=True,
)

llm_with_tools = Agent(
    name="LLM with Tools",
    model=get_llm_by_type("basic"),
    tools=[DuckDuckGoTools()],
    db=db2,
    enable_user_memories=True,
)
