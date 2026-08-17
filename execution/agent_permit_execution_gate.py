"""A reference enforcement boundary for AgentPermit-authorized actions.

The gate is deliberately small: it does not execute transactions itself and
does not pretend to be a wallet, API gateway, or tool runner. A production
integration should place the same authorization check in its real execution
path and adapt its downstream target to ExecutionTarget.
"""

from dataclasses import dataclass
from typing import Any, Protocol


class AgentPermitAuthorization(Protocol):
    """Contract-facing authorization surface used by the gate."""

    def can_execute(self, request_id: int) -> bool:
        """Return the current onchain authorization for request_id."""


class ExecutionTarget(Protocol):
    """Downstream tool, wallet, API, or infrastructure action."""

    def call(self, payload: Any) -> Any:
        """Forward one already-authorized action payload."""


class ExecutionGateError(RuntimeError):
    """Base class for errors raised by the enforcement boundary."""


class ExecutionBlocked(ExecutionGateError):
    """Raised when current AgentPermit authorization is false/unavailable."""


class DuplicateExecution(ExecutionGateError):
    """Raised when a request already succeeded through this gate instance."""


class TargetExecutionFailed(ExecutionGateError):
    """Raised when the downstream target raises or explicitly reports failure."""


@dataclass(frozen=True)
class ExecutionReceipt:
    """Structured receipt returned only after downstream success."""

    event: str
    request_id: int
    result: Any
    payload: Any


class AgentPermitExecutionGate:
    """Enforce a fresh can_execute check immediately before forwarding.

    Authorization is intentionally never cached. Duplicate protection is
    local to this gate instance and is recorded only after the target returns
    successfully. A target return value of False or a mapping/object with
    success is False is treated as an explicit downstream failure; None is
    otherwise a valid success result for side-effecting targets.
    """

    EVENT_NAME = "AgentPermitExecutionGateExecuted"

    def __init__(self, authorization: AgentPermitAuthorization):
        self._authorization = authorization
        self._executed_request_ids: set[int] = set()

    def has_executed(self, request_id: int) -> bool:
        """Return whether this gate recorded a successful execution."""

        return request_id in self._executed_request_ids

    @staticmethod
    def _explicit_target_failure(result: Any) -> bool:
        if result is False:
            return True
        if isinstance(result, dict):
            return result.get("success") is False
        return getattr(result, "success", True) is False

    def execute(
        self,
        request_id: int,
        target: ExecutionTarget,
        payload: Any,
    ) -> ExecutionReceipt:
        """Authorize and forward one request to a downstream target.

        request_id is the AgentPermit action ID (the deployed contract accepts
        it as u256). The target receives exactly payload and nothing is
        forwarded when authorization is false.
        """

        if request_id in self._executed_request_ids:
            raise DuplicateExecution(
                f"Request {request_id} was already executed through this gate"
            )

        # Security boundary: this is the source of truth, reread at execution
        # time. Permit JSON, a copied AP-xxxx ID, and request.decision are not
        # authorization inputs.
        try:
            authorized = self._authorization.can_execute(request_id)
        except Exception as exc:
            raise ExecutionBlocked(
                f"Execution blocked: authorization unavailable for request {request_id}"
            ) from exc
        if not authorized:
            raise ExecutionBlocked(f"Execution blocked for request {request_id}")

        try:
            result = target.call(payload)
        except Exception as exc:
            raise TargetExecutionFailed(
                f"Downstream execution failed for request {request_id}"
            ) from exc
        if self._explicit_target_failure(result):
            raise TargetExecutionFailed(
                f"Downstream target reported failure for request {request_id}"
            )

        # Record only after the target succeeds, so a failed action remains
        # retryable after the downstream issue is fixed.
        self._executed_request_ids.add(request_id)
        return ExecutionReceipt(
            event=self.EVENT_NAME,
            request_id=request_id,
            result=result,
            payload=payload,
        )
