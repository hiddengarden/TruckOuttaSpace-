from agency.agents.rnd_agent import RnDAgent


class RecordingProvider:
    def __init__(self, reply: str = "use tool X"):
        self.reply = reply
        self.last_system = None

    def complete(self, system: str, user: str) -> str:
        self.last_system = system
        return self.reply


def test_suggest_improvements_includes_stack_focus_and_caveat():
    provider = RecordingProvider()

    result = RnDAgent(provider).suggest_improvements("- ollama: up", focus="video generation")

    assert result == "use tool X"
    assert "- ollama: up" in provider.last_system
    assert "video generation" in provider.last_system
    assert "no live web access" in provider.last_system


def test_suggest_improvements_without_focus_omits_focus_line():
    provider = RecordingProvider()

    RnDAgent(provider).suggest_improvements("- ollama: up")

    assert "Focus area" not in provider.last_system
