from dataclasses import dataclass
import hashlib
import json

import pytest

from execution import (
    AgentPermitExecutionGate,
    DuplicateExecution,
    ExecutionBlocked,
    TargetExecutionFailed,
)


@dataclass
class MockToolTarget:
    target_id: str = "Cloud Compute Inc."
    result: object = None
    error: Exception | None = None

    def __post_init__(self):
        self.calls = []

    def call(self, payload):
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        return self.result


class MockAgentPermit:
    def __init__(self, allowed=True, consume_allowed=True):
        self.allowed = allowed
        self.consume_allowed = consume_allowed
        self.can_calls = []
        self.consume_calls = []
        self.consumed = set()

    def can_execute(self, request_id, target, payload_hash):
        self.can_calls.append((request_id, target, payload_hash))
        return self.allowed and request_id not in self.consumed

    def consume_permit(self, request_id, target, payload_hash):
        self.consume_calls.append((request_id, target, payload_hash))
        if not self.consume_allowed or not self.can_execute(request_id, target, payload_hash):
            raise RuntimeError("consumption rejected")
        self.consumed.add(request_id)


def test_exact_scope_target_called_once():
    authorization = MockAgentPermit()
    target = MockToolTarget(result={"tx": "0xabc"})
    gate = AgentPermitExecutionGate(authorization)
    payload = {"value": 10, "data": "transfer"}

    receipt = gate.execute(7, target, payload)

    assert target.calls == [payload]
    assert receipt.event == "AgentPermitExecutionGateExecuted"
    assert receipt.target == target.target_id
    assert receipt.payload_hash == gate.payload_hash(payload)
    assert receipt.consumed is True
    assert gate.has_executed(7) is True


@pytest.mark.parametrize("target_id,payload", [
    ("Different Target", {"value": 10}),
    ("Cloud Compute Inc.", {"value": 11}),
])
def test_wrong_scope_is_blocked_without_target_call(target_id, payload):
    authorization = MockAgentPermit(allowed=False)
    target = MockToolTarget(target_id=target_id)
    gate = AgentPermitExecutionGate(authorization)

    with pytest.raises(ExecutionBlocked):
        gate.execute(7, target, payload)

    assert target.calls == []


@pytest.mark.parametrize("lifecycle_state", ["expired", "revoked", "consumed"])
def test_expired_revoked_and_consumed_authorization_are_blocked(lifecycle_state):
    authorization = MockAgentPermit(allowed=False)
    target = MockToolTarget()
    gate = AgentPermitExecutionGate(authorization)

    with pytest.raises(ExecutionBlocked):
        gate.execute(7, target, {"action": lifecycle_state})

    assert target.calls == []


def test_replay_is_blocked_after_first_attempt():
    authorization = MockAgentPermit()
    target = MockToolTarget(result="ok")
    gate = AgentPermitExecutionGate(authorization)
    gate.execute(7, target, {"action": "first"})

    with pytest.raises(DuplicateExecution):
        gate.execute(7, target, {"action": "replay"})

    assert len(target.calls) == 1


def test_authorization_lookup_failure_fails_closed():
    class BrokenAuthorization:
        def can_execute(self, request_id, target, payload_hash):
            raise RuntimeError("RPC unavailable")

        def consume_permit(self, request_id, target, payload_hash):
            raise AssertionError("consume must not run")

    target = MockToolTarget()
    gate = AgentPermitExecutionGate(BrokenAuthorization())

    with pytest.raises(ExecutionBlocked):
        gate.execute(7, target, {"action": "lookup"})

    assert target.calls == []


def test_consumption_failure_fails_closed_without_target_call():
    authorization = MockAgentPermit(consume_allowed=False)
    target = MockToolTarget()
    gate = AgentPermitExecutionGate(authorization)

    with pytest.raises(ExecutionBlocked):
        gate.execute(7, target, {"action": "consume"})

    assert target.calls == []
    assert gate.has_executed(7) is False


def test_authorization_and_consumption_are_reread_at_execution_time():
    authorization = MockAgentPermit(allowed=False)
    target = MockToolTarget(result="ok")
    gate = AgentPermitExecutionGate(authorization)

    with pytest.raises(ExecutionBlocked):
        gate.execute(7, target, {"action": "first"})

    authorization.allowed = True
    receipt = gate.execute(7, target, {"action": "second"})

    assert receipt.result == "ok"
    assert len(authorization.can_calls) >= 2
    assert len(authorization.consume_calls) == 1
    assert target.calls == [{"action": "second"}]


@pytest.mark.parametrize("failure", [RuntimeError("network"), False, {"success": False}])
def test_target_failure_after_consumption_is_not_replayable(failure):
    authorization = MockAgentPermit()
    target = MockToolTarget(result=failure if failure is not False else False)
    if isinstance(failure, BaseException):
        target.error = failure
    gate = AgentPermitExecutionGate(authorization)

    with pytest.raises(TargetExecutionFailed):
        gate.execute(7, target, {"action": "retryable"})

    assert gate.has_executed(7) is False
    assert 7 in authorization.consumed
    with pytest.raises(DuplicateExecution):
        gate.execute(7, target, {"action": "retry"})
    assert len(target.calls) == 1


def test_payload_commitment_is_deterministic_and_ordered():
    payload_a = {"b": 2, "a": 1}
    payload_b = json.loads(json.dumps(payload_a, sort_keys=False))
    expected = hashlib.sha256(b'{"a":1,"b":2}').hexdigest()
    assert AgentPermitExecutionGate.canonical_payload(payload_a) == '{"a":1,"b":2}'
    assert AgentPermitExecutionGate.payload_hash(payload_a) == expected
    assert AgentPermitExecutionGate.payload_hash(payload_a) == AgentPermitExecutionGate.payload_hash(payload_b)


@pytest.mark.parametrize(
    "payload",
    [
        {"nested": {"z": "é", "a": [True, None, 3.5]}},
        ["first", {"second": ["深", 2]}],
    ],
)
def test_nested_unicode_json_payloads_are_deterministic(payload):
    reordered = json.loads(json.dumps(payload, ensure_ascii=False))
    assert AgentPermitExecutionGate.payload_hash(payload) == AgentPermitExecutionGate.payload_hash(reordered)


def test_array_order_changes_payload_commitment():
    assert AgentPermitExecutionGate.payload_hash(["a", "b"]) != AgentPermitExecutionGate.payload_hash(["b", "a"])


def test_plain_string_whitespace_and_json_string_are_distinct():
    assert AgentPermitExecutionGate.payload_hash("a b") != AgentPermitExecutionGate.payload_hash("ab")
    assert AgentPermitExecutionGate.payload_hash("1") != AgentPermitExecutionGate.payload_hash('"1"')


@pytest.mark.parametrize("payload", [1, 1.0, True, None, b"bytes"])
def test_top_level_scalar_and_bytes_payloads_fail_closed(payload):
    with pytest.raises(ExecutionBlocked):
        AgentPermitExecutionGate.payload_hash(payload)


def test_gate_hash_matches_contract_hash_for_canonical_payload_text():
    payload = {"b": 2, "a": 1}
    canonical = AgentPermitExecutionGate.canonical_payload(payload)
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert AgentPermitExecutionGate.payload_hash(payload) == expected


def test_missing_target_identity_fails_closed():
    class NoIdentityTarget:
        def call(self, payload):
            raise AssertionError("target must not be called")

    gate = AgentPermitExecutionGate(MockAgentPermit())
    with pytest.raises(ExecutionBlocked):
        gate.execute(7, NoIdentityTarget(), {"action": "missing-target"})
