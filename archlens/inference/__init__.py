"""Inference: turning static evidence into an architecture model.

Two layers, in order of precedence:

* :mod:`archlens.inference.heuristics` - rule-based, always runs, no network.
* :mod:`archlens.inference.llm` - Claude refines the heuristic baseline.

All three submodules import eagerly; `llm` defers the `anthropic` import into
:func:`archlens.inference.llm.enrich`, so importing this package stays cheap
without needing a lazy `__getattr__` here.
"""

from __future__ import annotations

from . import flows, heuristics, llm, prompts

__all__ = ["flows", "heuristics", "llm", "prompts"]
