"""adk_brain must fail closed: when there's no Gemini key or ADK is disabled it returns
None (no network, no import) so the state machine falls back to direct Gemini / heuristic.
The live ADK path (Gemini calling tools through the SequentialAgent) is exercised manually
against a real key; CI just guards the fallback contract."""
from autosre import adk_brain, config


def test_unavailable_without_key(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "USE_ADK", True)
    assert adk_brain.available() is False
    assert adk_brain.decide("airbag-target") is None  # no network, instant


def test_unavailable_when_flag_off(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "key")
    monkeypatch.setattr(config, "USE_ADK", False)
    assert adk_brain.available() is False
    assert adk_brain.decide("airbag-target") is None


def test_sequential_agent_graph_is_wired():
    """The required-stack ADK graph is actually built as documented: a SequentialAgent(triage→decide),
    not just claimed. Guards the 'Gemini reasons through ADK' wiring against regression."""
    from autosre import agent as adk_agent
    root = adk_agent.root_agent
    assert type(root).__name__ == "SequentialAgent" and root.name == "airbag_brain"
    assert len(root.sub_agents) == 2  # triage LlmAgent (calls the tools) + decision LlmAgent
