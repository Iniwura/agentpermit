"""Reference execution enforcement for exact AgentPermit scopes.

This adapter does not execute transactions itself and does not pretend to be a
wallet, API gateway, or tool runner. Production systems must route execution
through an enforcement boundary that presents the exact target and payload
commitment to AgentPermit.
"""

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Protocol


class AgentPermitAuthorization(Protocol):
    """Contract-facing authorization and lifecycle surface."""

    def can_execute(self, request_id: int, target: str, payload_hash: str) -> bool:
        """Return current authorization for the exact requested scope."""

    def consume_permit(self, request_id: int, target: str, payload_hash: str) -> None:
        """Atomically consume the exact executable permit scope."""


class ExecutionTarget(Protocol):
    """Downstream tool, wallet, API, or infrastructure action."""

    target_id: str

    def call(self, payload: Any) -> Any:
        """Forward one already-authorized action payload."""


class ExecutionGateError(RuntimeError):
    """Base class for errors raised by the enforcement boundary."""


class ExecutionBlocked(ExecutionGateError):
    """Raised when exact AgentPermit authorization is false/unavailable."""


class DuplicateExecution(ExecutionGateError):
    """Raised when a request has already been consumed through this gate."""


class TargetExecutionFailed(ExecutionGateError):
    """Raised when the downstream target raises or explicitly reports failure."""


@dataclass(frozen=True)
class ExecutionReceipt:
    """Structured receipt returned after exact-scope consumption and success."""

    event: str
    request_id: int
    target: str
    payload_hash: str
    result: Any
    payload: Any
    consumed: bool = True


class AgentPermitExecutionGate:
    """Consume exact authorization immediately before forwarding execution.

    The contract accepts a canonical payload string and hashes its exact UTF-8
    bytes. The gate accepts that same string, or a top-level JSON object/list and
    canonicalizes it with sorted keys, compact separators, UTF-8 characters, and
    JSON-compatible values before hashing. Top-level scalar values and bytes are
    rejected so a plain string cannot collide with a numeric or boolean payload.
    The contract proposer/frontend must submit the resulting canonical string.
    The contract consumes the permit before the target call. If the target then
    fails, the permit remains consumed by design; the external call is not
    atomic with the onchain state transition and must not be silently replayed.
    """

    EVENT_NAME = "AgentPermitExecutionGateExecuted"

    def __init__(self, authorization: AgentPermitAuthorization):
        self._authorization = authorization
        self._consumed_request_ids: set[int] = set()
        self._executed_request_ids: set[int] = set()

    def has_executed(self, request_id: int) -> bool:
        """Return whether the downstream target completed successfully."""

        return request_id in self._executed_request_ids

    @staticmethod
    def canonical_payload(payload: Any) -> str:
        """Return the exact string the contract must hash."""

        if isinstance(payload, str):
            return payload
        if not isinstance(payload, (dict, list)):
            raise ExecutionBlocked(
                "Payload must be a canonical string, object, or list"
            )
        try:
            return json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise ExecutionBlocked("Payload cannot be canonically committed") from exc

    @staticmethod
    def payload_hash(payload: Any) -> str:
        """Hash canonical UTF-8 payload bytes using the contract rule."""

        return hashlib.sha256(
            AgentPermitExecutionGate.canonical_payload(payload).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def target_identity(target: ExecutionTarget) -> str:
        """Read an explicit stable target identity and fail closed if absent."""

        for attribute in ("target_id", "identity", "address"):
            value = getattr(target, attribute, None)
            if callable(value):
                value = value()
            if isinstance(value, str) and value:
                return value
        raise ExecutionBlocked("Target has no stable target_id/identity/address")

    def execute(
        self,
        request_id: int,
        target: ExecutionTarget,
        payload: Any,
    ) -> ExecutionReceipt:
        """Authorize, consume, then forward one exact-scope request."""

        if request_id in self._consumed_request_ids:
            raise DuplicateExecution(
                f"Request {request_id} was already consumed through this gate"
            )

        target_id = self.target_identity(target)
        commitment = self.payload_hash(payload)

        try:
            authorized = self._authorization.can_execute(
                request_id, target_id, commitment
            )
        except Exception as exc:
            raise ExecutionBlocked(
                f"Execution blocked: authorization unavailable for request {request_id}"
            ) from exc
        if not authorized:
            raise ExecutionBlocked(f"Execution blocked for request {request_id}")

        try:
            consumed = self._authorization.consume_permit(
                request_id, target_id, commitment
            )
        except Exception as exc:
            raise ExecutionBlocked(
                f"Execution blocked: permit consumption failed for request {request_id}"
            ) from exc
        if consumed is False:
            raise ExecutionBlocked(
                f"Execution blocked: permit consumption failed for request {request_id}"
            )
        self._consumed_request_ids.add(request_id)

        try:
            result = target.call(payload)
        except Exception as exc:
            raise TargetExecutionFailed(
                f"Downstream execution failed after consuming request {request_id}"
            ) from exc
        if result is False:
            raise TargetExecutionFailed(
                f"Downstream target reported failure for request {request_id}"
            )
        if isinstance(result, dict) and result.get("success") is False:
            raise TargetExecutionFailed(
                f"Downstream target reported failure for request {request_id}"
            )
        if getattr(result, "success", True) is False:
            raise TargetExecutionFailed(
                f"Downstream target reported failure for request {request_id}"
            )

        self._executed_request_ids.add(request_id)
        return ExecutionReceipt(
            event=self.EVENT_NAME,
            request_id=request_id,
            target=target_id,
            payload_hash=commitment,
            result=result,
            payload=payload,
        )
