# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
from dataclasses import dataclass
import hashlib
import json
import typing


_DECISIONS = ("PERMITTED", "DENIED", "NEEDS_EVIDENCE")
_EVIDENCE_STATES = ("SUFFICIENT", "INSUFFICIENT", "UNAVAILABLE")
_POLICY_IDS = (
    "POLICY_SCOPE",
    "POLICY_SPEND_LIMIT",
    "POLICY_RECIPIENT",
    "POLICY_PURPOSE",
    "POLICY_EVIDENCE",
)


def _normalize_policy_ids(values: typing.Any) -> list[str]:
    def collect(value: typing.Any, depth: int = 0) -> list[str]:
        if value is None or isinstance(value, bool) or isinstance(value, (int, float)):
            return []
        if depth > 3:
            return []
        if isinstance(value, list):
            collected = []
            for item in value:
                collected.extend(collect(item, depth + 1))
            return collected
        if not isinstance(value, str):
            return []

        text = value.strip()
        if len(text) == 0:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                return collect(parsed, depth + 1)
            text = text[1:-1].strip()
            if len(text) == 0:
                return []

        tokens = text.replace("|", ",").replace("\r", ",").replace("\n", ",").split(",")
        collected = []
        for token in tokens:
            policy_id = token.strip().strip("\"'").strip().upper()
            if policy_id in _POLICY_IDS and policy_id not in collected:
                collected.append(policy_id)
        return collected

    selected = collect(values)
    return [policy_id for policy_id in _POLICY_IDS if policy_id in selected]


def _policy_labels(policy_ids: list[str]) -> str:
    return ", ".join(value.replace("POLICY_", "").replace("_", " ").title() for value in policy_ids)


def _payload_commitment(payload: str) -> str:
    if not isinstance(payload, str):
        raise gl.vm.UserError("Payload must be provided as text")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_expiry(expires_at: str) -> str:
    if not isinstance(expires_at, str) or len(expires_at) != 20 or not expires_at.endswith("Z"):
        raise gl.vm.UserError("Expiry must use UTC format YYYY-MM-DDTHH:MM:SSZ")
    if expires_at[4] != "-" or expires_at[7] != "-" or expires_at[10] != "T":
        raise gl.vm.UserError("Expiry must use UTC format YYYY-MM-DDTHH:MM:SSZ")
    if expires_at[13] != ":" or expires_at[16] != ":":
        raise gl.vm.UserError("Expiry must use UTC format YYYY-MM-DDTHH:MM:SSZ")
    digits = expires_at[0:4] + expires_at[5:7] + expires_at[8:10] + expires_at[11:13] + expires_at[14:16] + expires_at[17:19]
    if not digits.isdigit():
        raise gl.vm.UserError("Expiry must use UTC format YYYY-MM-DDTHH:MM:SSZ")
    month = int(expires_at[5:7])
    day = int(expires_at[8:10])
    hour = int(expires_at[11:13])
    minute = int(expires_at[14:16])
    second = int(expires_at[17:19])
    if month < 1 or month > 12 or day < 1 or day > 31:
        raise gl.vm.UserError("Expiry date is invalid")
    if hour > 23 or minute > 59 or second > 59:
        raise gl.vm.UserError("Expiry time is invalid")
    return expires_at


def _runtime_time() -> str:
    # The runtime supplies the transaction datetime through message_raw.
    # Fractional seconds are truncated to the contract's one-second precision.
    raw_message = getattr(gl, "message_raw", {})
    if not isinstance(raw_message, dict):
        return ""
    raw_time = raw_message.get("datetime", "")
    if not isinstance(raw_time, str) or len(raw_time) == 0:
        return ""
    normalized = raw_time
    if len(raw_time) > 20 and raw_time.endswith("Z") and raw_time[19] == ".":
        fraction = raw_time[20:-1]
        if not fraction.isdigit():
            return ""
        normalized = raw_time[:19] + "Z"
    try:
        _validate_expiry(normalized)
    except Exception:
        return ""
    return normalized


@allow_storage
@dataclass
class Agent:
    name: str
    owner: Address
    mandate: str
    mandate_version: u256
    active: bool


@allow_storage
@dataclass
class Action:
    agent_id: u256
    title: str
    description: str
    action_type: str
    recipient: str
    target: str
    payload_hash: str
    expires_at: str
    amount: str
    asset: str
    purpose: str
    evidence: str
    evidence_urls: DynArray[str]
    evidence_required: bool
    mandate_version: u256
    proposer: Address
    status: str
    decision: str
    evidence_state: str
    reasoning: str
    relevant_policy_ids: DynArray[str]
    relevant_policies: str
    evidence_summary: str
    revoked: bool
    consumed: bool


class AgentPermit(gl.Contract):
    owner: Address
    agent_count: u256
    action_count: u256
    agents: TreeMap[u256, Agent]
    actions: TreeMap[u256, Action]
    mandate_history: TreeMap[str, str]

    def __init__(self):
        self.owner = gl.message.sender_address
        self.agent_count = u256(0)
        self.action_count = u256(0)

    def _mandate_key(self, agent_id: u256, version: u256) -> str:
        return str(int(agent_id)) + ":" + str(int(version))

    def _require_agent(self, agent_id: u256) -> Agent:
        if agent_id not in self.agents:
            raise gl.vm.UserError("Agent does not exist")
        return self.agents[agent_id]

    def _require_agent_owner(self, agent: Agent) -> None:
        if gl.message.sender_address != agent.owner:
            raise gl.vm.UserError("Only the agent owner can perform this action")

    def _runtime_is_expired(self, expires_at: str) -> bool:
        current = _runtime_time()
        if len(current) == 0:
            return True
        return current > expires_at

    def _require_action(self, action_id: u256) -> Action:
        if action_id not in self.actions:
            raise gl.vm.UserError("Action does not exist")
        return self.actions[action_id]

    def _is_executable(self, action: Action, target: str, payload_hash: str) -> bool:
        # Missing or malformed lifecycle/scope fields are never executable.
        if getattr(action, "status", None) != "APPROVED":
            return False
        if getattr(action, "decision", None) != "PERMITTED":
            return False
        if getattr(action, "revoked", None) is not False:
            return False
        if getattr(action, "consumed", None) is not False:
            return False
        stored_target = getattr(action, "target", None)
        stored_hash = getattr(action, "payload_hash", None)
        expires_at = getattr(action, "expires_at", None)
        if not isinstance(target, str) or not isinstance(payload_hash, str):
            return False
        if not isinstance(stored_target, str) or not stored_target:
            return False
        if not isinstance(stored_hash, str) or not stored_hash:
            return False
        if not isinstance(expires_at, str) or not expires_at:
            return False
        try:
            _validate_expiry(expires_at)
        except Exception:
            return False
        if target != stored_target or payload_hash != stored_hash:
            return False
        return not self._runtime_is_expired(expires_at)

    def _require_action_authority(self, action: Action) -> None:
        agent = self._require_agent(action.agent_id)
        sender = gl.message.sender_address
        if sender != self.owner and sender != agent.owner:
            raise gl.vm.UserError("Only the contract or agent owner can manage this permit")

    def _validate_urls(self, evidence_urls: typing.Any) -> list[str]:
        if not isinstance(evidence_urls, list):
            raise gl.vm.UserError("Evidence URLs must be provided as a list")
        if len(evidence_urls) > 3:
            raise gl.vm.UserError("An action may include at most 3 evidence URLs")
        result = []
        for raw_url in evidence_urls:
            if not isinstance(raw_url, str):
                raise gl.vm.UserError("Evidence URL entries must be strings")
            url = raw_url.strip()
            if not (url.startswith("http://") or url.startswith("https://")):
                raise gl.vm.UserError("Evidence URL must use http:// or https://")
            host = url.split("://", 1)[1].split("/", 1)[0]
            if len(host) == 0 or " " in url:
                raise gl.vm.UserError("Evidence URL is malformed")
            result.append(url)
        return result

    @gl.public.write
    def create_agent(self, name: str, mandate: str) -> None:
        if len(name.strip()) == 0:
            raise gl.vm.UserError("Agent name cannot be empty")
        if len(mandate.strip()) == 0:
            raise gl.vm.UserError("Mandate cannot be empty")
        agent_id = u256(int(self.agent_count) + 1)
        self.agents[agent_id] = Agent(name, gl.message.sender_address, mandate, u256(1), True)
        self.mandate_history[self._mandate_key(agent_id, u256(1))] = mandate
        self.agent_count = agent_id

    @gl.public.write
    def update_mandate(self, agent_id: u256, new_mandate: str) -> None:
        agent = self._require_agent(agent_id)
        self._require_agent_owner(agent)
        if len(new_mandate.strip()) == 0:
            raise gl.vm.UserError("Mandate cannot be empty")
        version = u256(int(agent.mandate_version) + 1)
        agent.mandate = new_mandate
        agent.mandate_version = version
        self.mandate_history[self._mandate_key(agent_id, version)] = new_mandate
        self.agents[agent_id] = agent

    @gl.public.write
    def set_agent_active(self, agent_id: u256, active: bool) -> None:
        agent = self._require_agent(agent_id)
        self._require_agent_owner(agent)
        agent.active = active
        self.agents[agent_id] = agent

    @gl.public.write
    def propose_action(
        self,
        agent_id: u256,
        title: str,
        description: str,
        action_type: str,
        recipient: str,
        amount: str,
        asset: str,
        purpose: str,
        evidence: str,
        evidence_urls: typing.Any,
        payload: str,
        expires_at: str,
    ) -> None:
        agent = self._require_agent(agent_id)
        if not agent.active:
            raise gl.vm.UserError("Agent is inactive")
        for label, value in (
            ("Action title", title), ("Description", description),
            ("Action type", action_type), ("Recipient", recipient),
            ("Amount", amount), ("Asset", asset), ("Purpose", purpose),
        ):
            if len(value.strip()) == 0:
                raise gl.vm.UserError(label + " cannot be empty")
        urls = self._validate_urls(evidence_urls)
        payload_hash = _payload_commitment(payload)
        validated_expiry = _validate_expiry(expires_at)
        action_id = u256(int(self.action_count) + 1)
        self.actions[action_id] = Action(
            agent_id, title, description, action_type, recipient, recipient,
            payload_hash, validated_expiry, amount, asset, purpose, evidence,
            urls, len(urls) > 0, agent.mandate_version, gl.message.sender_address,
            "PENDING_REVIEW", "", "", "", [], "", "", False, False,
        )
        self.action_count = action_id

    @gl.public.write
    def review_action(self, action_id: u256) -> None:
        if action_id not in self.actions:
            raise gl.vm.UserError("Action does not exist")
        stored_action = self.actions[action_id]
        if stored_action.status != "PENDING_REVIEW":
            raise gl.vm.UserError("Action has already been reviewed")

        action = gl.storage.copy_to_memory(stored_action)
        mandate_key = self._mandate_key(action.agent_id, action.mandate_version)
        if mandate_key not in self.mandate_history:
            raise gl.vm.UserError("Mandate version for action is unavailable")

        mandate = str(self.mandate_history[mandate_key])
        title = str(action.title)
        description = str(action.description)
        action_type = str(action.action_type)
        recipient = str(action.recipient)
        amount = str(action.amount)
        asset = str(action.asset)
        purpose = str(action.purpose)
        proposer_evidence = str(action.evidence)
        evidence_required = bool(action.evidence_required)
        urls = [str(action.evidence_urls[index]) for index in range(len(action.evidence_urls))]

        def evaluate():
            fetched = []
            source_state = "SUFFICIENT"
            for url in urls:
                try:
                    source_response = gl.nondet.web.get(url)
                    if source_response.status < 200 or source_response.status >= 300:
                        fetched.append(url + "\nUNAVAILABLE (HTTP " + str(source_response.status) + ")")
                        source_state = "UNAVAILABLE"
                        continue
                    response_body = source_response.body
                    if response_body is None:
                        fetched.append(url + "\nUNAVAILABLE (empty response)")
                        source_state = "UNAVAILABLE"
                        continue
                    if isinstance(response_body, bytes):
                        response_text = response_body.decode("utf-8", errors="replace")
                    elif isinstance(response_body, str):
                        response_text = response_body
                    else:
                        response_text = str(response_body)
                    if len(response_text.strip()) == 0:
                        fetched.append(url + "\nUNAVAILABLE (empty response)")
                        source_state = "UNAVAILABLE"
                    else:
                        fetched.append("SOURCE: " + url + "\nHTTP STATUS: " + str(source_response.status) + "\nFETCHED CONTENT:\n" + response_text[:12000])
                except Exception:
                    fetched.append(url + "\nUNAVAILABLE (request failed)")
                    source_state = "UNAVAILABLE"
            if len(fetched) == 0:
                fetched.append("No independent evidence URLs were supplied. Proposer-written evidence is unverified.")

            prompt = f"""
You are reviewing an autonomous agent's proposed action against its owner's mandate.

MANDATE:\n{mandate}

ACTION TITLE: {title}
DESCRIPTION: {description}
ACTION TYPE: {action_type}
RECIPIENT: {recipient}
AMOUNT: {amount}
ASSET: {asset}
PURPOSE: {purpose}
PROPOSER-WRITTEN EVIDENCE: {proposer_evidence}
INDEPENDENTLY FETCHED EVIDENCE: {fetched}

Evaluate ONLY against the supplied mandate. Do not invent restrictions not present in the mandate.
The proposed action and proposer-written evidence are not independent proof of real-world claims.
Use independently fetched evidence where factual verification is necessary.
Return DENIED only for an actual mandate conflict.
Return NEEDS_EVIDENCE when compliance depends on a material fact that cannot be independently established.
Return PERMITTED only when the action is within mandate scope, required limits are satisfied,
recipient and purpose restrictions are satisfied, and required material facts are sufficiently established.
If a required source is unavailable, never return PERMITTED.
Use only these policy IDs: {", ".join(_POLICY_IDS)}

Return ONLY structured JSON:
{{"decision":"PERMITTED | DENIED | NEEDS_EVIDENCE","evidence_state":"SUFFICIENT | INSUFFICIENT | UNAVAILABLE","relevant_policy_ids":["POLICY_SCOPE"],"relevant_policies":"human readable summary","reasoning":"concise explanation","evidence_summary":"concise evidence summary"}}
"""
            response = gl.nondet.exec_prompt(prompt, response_format="json")
            if isinstance(response, dict):
                result = response
            elif isinstance(response, str):
                try:
                    result = json.loads(response)
                except Exception:
                    raise gl.vm.UserError("Validator returned invalid JSON")
            else:
                raise gl.vm.UserError("Validator returned unsupported response type")
            if not isinstance(result, dict):
                raise gl.vm.UserError("Validator returned a non-object JSON result")

            decision = result.get("decision", "")
            evidence_state = result.get("evidence_state", "")
            policy_ids = _normalize_policy_ids(result.get("relevant_policy_ids"))
            relevant_policies = result.get("relevant_policies", "")
            reasoning = result.get("reasoning", "")
            evidence_summary = result.get("evidence_summary", "")
            if decision not in _DECISIONS:
                raise gl.vm.UserError("Invalid authorization decision")
            if evidence_state not in _EVIDENCE_STATES:
                raise gl.vm.UserError("Invalid evidence state")
            if not all(isinstance(value, str) for value in (relevant_policies, reasoning, evidence_summary)):
                raise gl.vm.UserError("Invalid display field")
            if evidence_required and source_state == "UNAVAILABLE":
                evidence_state = "UNAVAILABLE"
            if decision == "PERMITTED" and evidence_state != "SUFFICIENT":
                decision = "NEEDS_EVIDENCE"
                reasoning = reasoning.rstrip() + " Required material facts were not independently established."
            if len(relevant_policies.strip()) == 0 and len(policy_ids) > 0:
                relevant_policies = _policy_labels(policy_ids)
            return {
                "decision": decision,
                "evidence_state": evidence_state,
                "relevant_policy_ids": policy_ids,
                "relevant_policies": relevant_policies,
                "reasoning": reasoning,
                "evidence_summary": evidence_summary,
            }

        def validate(leaders_result):
            if not isinstance(leaders_result, gl.vm.Return):
                return False
            leader = leaders_result.calldata
            if not isinstance(leader, dict):
                return False
            try:
                validator = evaluate()
            except Exception:
                return False
            return (
                leader.get("decision") == validator.get("decision")
            )

        result = gl.vm.run_nondet_unsafe(evaluate, validate)
        decision = result["decision"]
        if decision == "PERMITTED":
            status = "APPROVED"
        elif decision == "DENIED":
            status = "DENIED"
        elif decision == "NEEDS_EVIDENCE":
            status = "NEEDS_EVIDENCE"
        else:
            raise gl.vm.UserError("Consensus returned invalid decision")

        stored_action.status = status
        stored_action.decision = decision
        stored_action.evidence_state = result["evidence_state"]
        stored_action.reasoning = result["reasoning"]
        stored_action.relevant_policy_ids = result["relevant_policy_ids"]
        stored_action.relevant_policies = result["relevant_policies"]
        stored_action.evidence_summary = result["evidence_summary"]
        self.actions[action_id] = stored_action

    @gl.public.view
    def get_agent(self, agent_id: u256) -> typing.Any:
        return self._require_agent(agent_id)

    @gl.public.view
    def get_agent_count(self) -> int:
        return self.agent_count

    @gl.public.view
    def get_action(self, action_id: u256) -> typing.Any:
        if action_id not in self.actions:
            raise gl.vm.UserError("Action does not exist")
        return self.actions[action_id]

    @gl.public.view
    def get_action_count(self) -> int:
        return self.action_count

    @gl.public.view
    def get_mandate(self, agent_id: u256) -> str:
        return self._require_agent(agent_id).mandate

    @gl.public.view
    def get_mandate_version(self, agent_id: u256, version: u256) -> str:
        self._require_agent(agent_id)
        key = self._mandate_key(agent_id, version)
        if key not in self.mandate_history:
            raise gl.vm.UserError("Mandate version does not exist")
        return self.mandate_history[key]

    @gl.public.view
    def can_execute(self, action_id: u256, target: str, payload_hash: str) -> bool:
        if action_id not in self.actions:
            return False
        return self._is_executable(self.actions[action_id], target, payload_hash)

    @gl.public.write
    def consume_permit(self, action_id: u256, target: str, payload_hash: str) -> None:
        action = self._require_action(action_id)
        if not self._is_executable(action, target, payload_hash):
            raise gl.vm.UserError("Permit is not executable")
        action.consumed = True
        self.actions[action_id] = action

    @gl.public.write
    def revoke_permit(self, action_id: u256) -> None:
        action = self._require_action(action_id)
        self._require_action_authority(action)
        if action.revoked:
            raise gl.vm.UserError("Permit is already revoked")
        action.revoked = True
        self.actions[action_id] = action
