import pytest

from execution import (
    AgentPermitExecutionGate,
    DuplicateExecution,
    ExecutionBlocked,
    TargetExecutionFailed,
)


class MockAgentPermit:
    def __init__(self, allowed=False):
        self.allowed = allowed
        self.calls = []

    def can_execute(self, request_id):
        self.calls.append(request_id)
        return self.allowed


class MockToolTarget:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def call(self, payload):
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        return self.result


def test_blocked_execution_never_calls_target_or_marks_request():
    authorization = MockAgentPermit(allowed=False)
    target = MockToolTarget(result={"ok": True})
    gate = AgentPermitExecutionGate(authorization)

    with pytest.raises(ExecutionBlocked):
        gate.execute(7, target, {"value": 10, "data": "transfer"})

    assert authorization.calls == [7]
    assert target.calls == []
    assert gate.has_executed(7) is False


def test_authorized_execution_forwards_payload_and_returns_receipt():
    authorization = MockAgentPermit(allowed=True)
    target = MockToolTarget(result={"tx": "0xabc"})
    gate = AgentPermitExecutionGate(authorization)
    payload = {"value": 10, "data": "transfer"}

    receipt = gate.execute(7, target, payload)

    assert authorization.calls == [7]
    assert target.calls == [payload]
    assert receipt.event == "AgentPermitExecutionGateExecuted"
    assert receipt.request_id == 7
    assert receipt.result == {"tx": "0xabc"}
    assert receipt.payload == payload
    assert gate.has_executed(7) is True


def test_duplicate_execution_is_blocked_without_second_target_call():
    authorization = MockAgentPermit(allowed=True)
    target = MockToolTarget(result="ok")
    gate = AgentPermitExecutionGate(authorization)
    gate.execute(7, target, {"action": "first"})

    with pytest.raises(DuplicateExecution):
        gate.execute(7, target, {"action": "second"})

    assert target.calls == [{"action": "first"}]
    assert authorization.calls == [7]


@pytest.mark.parametrize("failure", [RuntimeError("network"), False, {"success": False}])
def test_target_failure_is_not_marked_and_can_be_retried(failure):
    authorization = MockAgentPermit(allowed=True)
    target = MockToolTarget(result=failure if failure is not False else False)
    if isinstance(failure, BaseException):
        target = MockToolTarget(error=failure)
    gate = AgentPermitExecutionGate(authorization)

    with pytest.raises(TargetExecutionFailed):
        gate.execute(7, target, {"action": "retryable"})

    assert gate.has_executed(7) is False
    assert len(target.calls) == 1

    target.error = None
    target.result = {"success": True}
    receipt = gate.execute(7, target, {"action": "retryable"})
    assert receipt.result == {"success": True}
    assert gate.has_executed(7) is True


def test_authorization_is_reread_at_execution_time():
    authorization = MockAgentPermit(allowed=False)
    target = MockToolTarget(result="ok")
    gate = AgentPermitExecutionGate(authorization)

    with pytest.raises(ExecutionBlocked):
        gate.execute(7, target, {"action": "first"})

    authorization.allowed = True
    receipt = gate.execute(7, target, {"action": "second"})

    assert receipt.result == "ok"
    assert authorization.calls == [7, 7]
    assert target.calls == [{"action": "second"}]
