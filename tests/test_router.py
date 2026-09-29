from term5.brain.router import AttentionRouter
from term5.models import ReasoningMode


def test_router_selects_cheap_simple_path():
    d = AttentionRouter().decide("show git status")
    assert d.reasoning == ReasoningMode.NONE


def test_router_escalates_complex_work():
    d = AttentionRouter().decide("debug this intermittent security race and prove the root cause")
    assert d.reasoning in {ReasoningMode.HIGH, ReasoningMode.MAX}
