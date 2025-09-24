
from pathlib import Path
from typing import Any, Dict

from agno.agent import Agent
from agno.media import Image
from agno.models.openai import OpenAILike
from agno.models.deepseek import DeepSeek
from typing import Literal

from src.config import load_yaml_config

# Define available LLM types
LLMType = Literal["basic", "reasoning", "vision"]

# Define agent-LLM mapping
AGENT_LLM_MAP: dict[str, LLMType] = {
    "coordinator": "basic",
    "planner": "basic",
    "researcher": "basic",
    "coder": "basic",
    "reporter": "basic",
    "podcast_script_writer": "basic",
    "ppt_composer": "basic",
    "prose_writer": "basic",
    "media_learning": "vision",
}


# Cache for LLM instances
_llm_cache: dict[LLMType, OpenAILike] = {}

def _create_llm_use_conf(llm_type: LLMType, conf: Dict[str, Any]) -> OpenAILike:
    llm_type_map = {
        "reasoning": conf.get("REASONING_MODEL"),
        "basic": conf.get("BASIC_MODEL"),
        "vision": conf.get("VLM_MODEL"),
        "media":  conf.get("VLM_MODEL"),
    }
    llm_conf = llm_type_map.get(llm_type)

    if not llm_conf:
        raise ValueError(f"Unknown LLM type: {llm_type}")
    if not isinstance(llm_conf, dict):
        raise ValueError(f"Invalid LLM Conf: {llm_type}")

    if llm_conf["base_url"] == "https://api.deepseek.com":
        return DeepSeek(**llm_conf)
    return OpenAILike(**llm_conf)


def get_llm_by_type(
    llm_type: LLMType,
) -> OpenAILike:
    """
    Get LLM instance by type. Returns cached instance if available.
    """
    if llm_type in _llm_cache:
        return _llm_cache[llm_type]

    conf = load_yaml_config(
        str((Path(__file__).parent.parent / "conf.yaml").resolve())
    )

    llm = _create_llm_use_conf(llm_type, conf)
    _llm_cache[llm_type] = llm
    return llm


if __name__ == "__main__":
    # basic_llm = get_llm_by_type("basic")
    # Agent(model=basic_llm).print_response("Hello")
    #
    # reasoning_llm = get_llm_by_type("reasoning")
    # Agent(model=reasoning_llm).print_response("Hello")

    image_path = Path(__file__).parent.joinpath("sample.jpg")

    v = Agent(model=get_llm_by_type("vision"))
    v.print_response(
        "describe the image in Chinese",
        images=[Image(filepath=image_path)],
        stream=True,)
