from agency.inference.provider import LLMProvider

_SYSTEM_TEMPLATE = (
    "You are the R&D advisor for a self-hosted content-automation pipeline.\n"
    "Current stack:\n{stack}\n"
    "Suggest concrete open-source tools or techniques that could improve or "
    "optimize this pipeline. Name real projects. Explicitly flag anything "
    "you're unsure is still maintained, since you have no live web access.\n"
    "{focus_line}"
)


class RnDAgent:
    """One instance for the whole system: suggests better open-source tooling.

    No live web search is wired in here -- this reasons from the model's own
    training knowledge only, so suggestions can be stale or wrong about
    current tooling. Real "keeping abreast of new technology" needs a
    search-capable backend; that's a documented gap (see README roadmap),
    not a silently fabricated capability.
    """

    def __init__(self, provider: LLMProvider):
        self._provider = provider

    def suggest_improvements(self, stack_description: str, focus: str | None = None) -> str:
        system = _SYSTEM_TEMPLATE.format(
            stack=stack_description, focus_line=f"Focus area: {focus}\n" if focus else ""
        )
        return self._provider.complete(system, "What would you improve, and why?").strip()
