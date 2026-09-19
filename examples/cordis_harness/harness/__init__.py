"""A minimal agent harness built on the Cordis plugin engine.

Layers:

- :mod:`harness.types` — the provider-neutral message, block and stream
  vocabulary every layer speaks.
- :mod:`harness.llm`, :mod:`harness.tools`, :mod:`harness.session`,
  :mod:`harness.system_prompt`, :mod:`harness.agent` — service definitions.
- :mod:`harness.plugins` — the Cordis plugins that install those services and
  are mounted by ``cordis.yml``.
- :mod:`harness.cli` — the ``cdh`` interactive entry point.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
