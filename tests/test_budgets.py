from review.agent.budgets import BudgetConfig, BudgetEnforcer, LoopDetector
from review.agent.state import AgentState


def test_agent_state_records_action() -> None:
    state = AgentState(diff="dummy diff")
    assert state.step_count == 0

    step = state.record_action(
        tool_name="read_file",
        tool_input={"path": "main.py"},
        output_summary="File content",
    )
    assert state.step_count == 1
    assert step.step_number == 1
    assert step.tool_name == "read_file"
    assert step.tool_input == {"path": "main.py"}


def test_loop_detector_triggers_on_repeated_identical_calls() -> None:
    detector = LoopDetector(threshold=2)

    # First call: not a loop
    assert detector.record_and_check("read_file", {"path": "app.py"}) is False

    # Second identical call: loop triggered!
    assert detector.record_and_check("read_file", {"path": "app.py"}) is True


def test_loop_detector_ignores_different_arguments() -> None:
    detector = LoopDetector(threshold=2)

    assert detector.record_and_check("read_file", {"path": "app.py"}) is False
    assert detector.record_and_check("read_file", {"path": "other.py"}) is False
    assert detector.record_and_check("get_diff", {"path": "other.py"}) is False


def test_budget_enforcer_halts_on_step_limit() -> None:
    config = BudgetConfig(max_steps=2)
    enforcer = BudgetEnforcer(config)
    state = AgentState(diff="diff")

    # Step 1
    halt, _ = enforcer.check_and_update(state, "tool_a", {"x": 1})
    assert halt is False
    state.record_action("tool_a", {"x": 1}, "ok")

    # Step 2
    halt, _ = enforcer.check_and_update(state, "tool_b", {"x": 2})
    assert halt is False
    state.record_action("tool_b", {"x": 2}, "ok")

    # Step 3 attempt: limit reached!
    halt, reason = enforcer.check_and_update(state, "tool_c", {"x": 3})
    assert halt is True
    assert "step budget exceeded" in str(reason)
    assert state.is_finished is True


def test_budget_enforcer_halts_on_token_limit() -> None:
    config = BudgetConfig(max_tokens=1000)
    enforcer = BudgetEnforcer(config)
    state = AgentState(diff="diff")

    halt, reason = enforcer.check_and_update(state, "tool_a", {}, tokens=1500)
    assert halt is True
    assert "token budget exceeded" in str(reason)
    assert state.is_finished is True


def test_budget_enforcer_halts_on_loop() -> None:
    config = BudgetConfig(loop_threshold=2)
    enforcer = BudgetEnforcer(config)
    state = AgentState(diff="diff")

    # Call 1
    halt, _ = enforcer.check_and_update(state, "read_file", {"path": "a.py"})
    assert halt is False

    # Call 2 with identical input: loop!
    halt, reason = enforcer.check_and_update(state, "read_file", {"path": "a.py"})
    assert halt is True
    assert "loop detected" in str(reason)
    assert state.is_finished is True
