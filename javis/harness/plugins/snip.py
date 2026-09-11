"""组合行：工具输出截断中间件（``tools/post-execute``），不 provide 服务。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from ..compression import MAX_TOOL_OUTPUT_CHARS, make_snip_listener
from ..types import Events

name = "javis.harness.plugins.snip"


class Config(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    tool_output_max_chars: int = MAX_TOOL_OUTPUT_CHARS


def apply(ctx, config):
    ctx.on(Events.TOOLS_POST_EXECUTE, make_snip_listener(config.tool_output_max_chars))
