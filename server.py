from agno.os import AgentOS
from src.agents import chat_agent,visual_learning_agent
from src.sample_agents import just_llm, llm_with_mem, llm_with_tools
from src.workflow import workflow

_agent_os = AgentOS(
    os_id="my-first-os",
    description="My first AgentOS",
    agents=[chat_agent,visual_learning_agent],
    workflows=[workflow],
)

_agent_os_test = AgentOS(
    os_id="my-first-os",
    description="My first AgentOS",
    agents=[just_llm, llm_with_mem,llm_with_tools],
)

app = _agent_os.get_app()

# Create and use workflow
if __name__ == "__main__":
    # Default port is 7777; change with port=...
    _agent_os.serve(app="server:app", reload=True)
