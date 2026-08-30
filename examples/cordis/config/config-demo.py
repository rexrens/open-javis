"""Tutorial chapter 5: configurable plugin with pydantic Config."""

from pydantic import BaseModel, Field

name = "config-demo"


class Config(BaseModel):
    greeting: str = "Hello"
    targets: list[str] = Field(default_factory=lambda: ["world"])


def apply(ctx, config: Config):
    for target in config.targets:
        print(f"{config.greeting}, {target}!")
