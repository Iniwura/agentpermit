import json
import pytest


MANDATE = (
    "May spend up to 500 USDC per transaction on cloud compute, developer "
    "infrastructure, API access, and research tools. Must not transfer funds "
    "to personal wallets. Material factual claims require verifiable evidence."
)


def deploy(direct_deploy):
    return direct_deploy("contracts/agent_permit.py")


def create_agent(contract, mandate=MANDATE):
    contract.create_agent("ResearchOps-01", mandate)


def propose(contract, *, recipient="Cloud Compute Inc.", amount="300", purpose="One month of GPU infrastructure", urls=None):
    contract.propose_action(
        1, "GPU compute", "Pay for a month of GPU infrastructure", "PAYMENT",
        recipient, amount, "USDC", purpose, "Provider offers GPU compute.", urls or [],
    )


def response(decision="PERMITTED", evidence_state="SUFFICIENT", ids=None, reasoning="Within mandate.", summary="Facts established.", **extra):
    return json.dumps({
        "decision": decision,
        "evidence_state": evidence_state,
        "relevant_policy_ids": ids if ids is not None else ["POLICY_SCOPE", "POLICY_SPEND_LIMIT"],
        "relevant_policies": "Scope and spend limit",
        "reasoning": reasoning,
        "evidence_summary": summary,
        **extra,
    })


_MISSING = object()


def response_with_policy_ids(value=_MISSING, **kwargs):
    payload = json.loads(response(**kwargs))
    if value is _MISSING:
        payload.pop("relevant_policy_ids", None)
    else:
        payload["relevant_policy_ids"] = value
    return json.dumps(payload)



def mock_review(direct_vm, value):
    direct_vm.mock_llm("You are reviewing an autonomous agent's proposed action", value)


def consensus_variant(direct_vm, direct_deploy, leader, validator, leader_body=None, validator_body=None):
    contract = deploy(direct_deploy)
    create_agent(contract)
    url = "https://evidence.example/provider"
    propose(contract, urls=[url] if leader_body is not None else [])
    if leader_body is not None:
        direct_vm.mock_web(url, {"method": "GET", "status": 200, "body": leader_body})
    mock_review(direct_vm, leader)
    contract.review_action(1)
    direct_vm.clear_mocks()
    if validator_body is not None:
        direct_vm.mock_web(url, {"method": "GET", "status": 200, "body": validator_body})
    mock_review(direct_vm, validator)
    return contract, direct_vm.run_validator()


def test_contract_initialization(direct_deploy):
    contract = deploy(direct_deploy)
    assert contract.get_agent_count() == 0
    assert contract.get_action_count() == 0


def test_create_agent(direct_deploy):
    contract = deploy(direct_deploy)
    create_agent(contract)
    agent = contract.get_agent(1)
    assert agent.name == "ResearchOps-01"
    assert agent.mandate_version == 1
    assert agent.active is True


@pytest.mark.parametrize("name,mandate,message", [
    ("", MANDATE, "name cannot be empty"),
    ("ResearchOps", " ", "Mandate cannot be empty"),
])
def test_create_agent_rejects_empty_fields(direct_vm, direct_deploy, name, mandate, message):
    contract = deploy(direct_deploy)
    with direct_vm.expect_revert(message):
        contract.create_agent(name, mandate)


def test_update_mandate_and_preserve_history(direct_deploy):
    contract = deploy(direct_deploy)
    create_agent(contract)
    contract.update_mandate(1, "Updated mandate")
    assert contract.get_mandate(1) == "Updated mandate"
    assert contract.get_agent(1).mandate_version == 2
    assert contract.get_mandate_version(1, 1) == MANDATE
    assert contract.get_mandate_version(1, 2) == "Updated mandate"


def test_non_owner_cannot_update_mandate(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)
    create_agent(contract)
    direct_vm.sender = "0x2222222222222222222222222222222222222222"
    with direct_vm.expect_revert("Only the agent owner"):
        contract.update_mandate(1, "Unauthorized")


def test_inactive_agent_rejects_actions(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)
    create_agent(contract)
    contract.set_agent_active(1, False)
    with direct_vm.expect_revert("inactive"):
        propose(contract)


def test_propose_action(direct_deploy):
    contract = deploy(direct_deploy)
    create_agent(contract)
    propose(contract)
    action = contract.get_action(1)
    assert action.agent_id == 1
    assert action.status == "PENDING_REVIEW"
    assert action.mandate_version == 1
    assert contract.get_action_count() == 1


def test_invalid_agent_rejected(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)
    with direct_vm.expect_revert("Agent does not exist"):
        contract.propose_action(99, "A", "B", "C", "D", "1", "USDC", "P", "", [])


@pytest.mark.parametrize("urls,message", [
    (["ftp://bad.example"], "http:// or https://"),
    (["https://bad host.example"], "malformed"),
    (["https://a", "https://b", "https://c", "https://d"], "at most 3"),
])
def test_evidence_url_validation(direct_vm, direct_deploy, urls, message):
    contract = deploy(direct_deploy)
    create_agent(contract)
    with direct_vm.expect_revert(message):
        propose(contract, urls=urls)


@pytest.mark.parametrize("decision,status,allowed", [
    ("PERMITTED", "APPROVED", True),
    ("DENIED", "DENIED", False),
    ("NEEDS_EVIDENCE", "NEEDS_EVIDENCE", False),
])
def test_decision_mapping_and_execution_gate(direct_vm, direct_deploy, decision, status, allowed):
    contract = deploy(direct_deploy)
    create_agent(contract)
    propose(contract)
    state = "SUFFICIENT" if decision != "NEEDS_EVIDENCE" else "INSUFFICIENT"
    mock_review(direct_vm, response(decision, state))
    contract.review_action(1)
    action = contract.get_action(1)
    assert action.decision == decision
    assert action.status == status
    assert contract.can_execute(1) is allowed


def test_denied_forbidden_recipient_and_purpose(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)
    create_agent(contract)
    propose(contract, recipient="Personal wallet", amount="800", purpose="Personal transfer")
    mock_review(direct_vm, response("DENIED", ids=["POLICY_SPEND_LIMIT", "POLICY_RECIPIENT", "POLICY_PURPOSE"]))
    contract.review_action(1)
    assert contract.get_action(1).status == "DENIED"


def test_required_unavailable_evidence_cannot_permit(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)
    create_agent(contract)
    url = "https://evidence.example/unavailable"
    propose(contract, urls=[url])
    direct_vm.mock_web(url, {"method": "GET", "status": 503, "body": "Unavailable"})
    mock_review(direct_vm, response("PERMITTED", "SUFFICIENT"))
    contract.review_action(1)
    action = contract.get_action(1)
    assert action.decision == "NEEDS_EVIDENCE"
    assert action.evidence_state == "UNAVAILABLE"
    assert contract.can_execute(1) is False


def test_consensus_ignores_reasoning_wording(direct_vm, direct_deploy):
    _, agrees = consensus_variant(direct_vm, direct_deploy, response(reasoning="Leader wording"), response(reasoning="Validator wording"))
    assert agrees is True


def test_consensus_normalizes_policy_order_and_duplicates(direct_vm, direct_deploy):
    leader = response(ids=["policy_spend_limit", "POLICY_SCOPE", "POLICY_SCOPE"])
    validator = response(ids=["POLICY_SCOPE", "POLICY_SPEND_LIMIT"])
    contract, agrees = consensus_variant(direct_vm, direct_deploy, leader, validator)
    assert agrees is True
    assert list(contract.get_action(1).relevant_policy_ids) == ["POLICY_SCOPE", "POLICY_SPEND_LIMIT"]


@pytest.mark.parametrize("value,expected", [
    (["POLICY_SCOPE", "POLICY_PURPOSE"], ["POLICY_SCOPE", "POLICY_PURPOSE"]),
    ("POLICY_SCOPE", ["POLICY_SCOPE"]),
    ("POLICY_SCOPE, POLICY_PURPOSE", ["POLICY_SCOPE", "POLICY_PURPOSE"]),
    ("POLICY_SCOPE | POLICY_PURPOSE", ["POLICY_SCOPE", "POLICY_PURPOSE"]),
    ("POLICY_SCOPE\nPOLICY_PURPOSE", ["POLICY_SCOPE", "POLICY_PURPOSE"]),
    (" policy_purpose | Policy_scope ", ["POLICY_SCOPE", "POLICY_PURPOSE"]),
    (["POLICY_PURPOSE", "POLICY_SCOPE", "POLICY_SCOPE"], ["POLICY_SCOPE", "POLICY_PURPOSE"]),
    ([], []),
    (None, []),
    (["POLICY_UNKNOWN"], []),
    (["policy_purpose", "POLICY_FAKE", "POLICY_SCOPE"], ["POLICY_SCOPE", "POLICY_PURPOSE"]),
    ({"id": "POLICY_SCOPE"}, []),
    (42, []),
    (True, []),
    (["POLICY_SCOPE", {"id": "POLICY_PURPOSE"}, 7, True, ["policy_evidence"], "POLICY_FAKE"], ["POLICY_SCOPE", "POLICY_EVIDENCE"]),
    ('["policy_purpose", "POLICY_SCOPE"]', ["POLICY_SCOPE", "POLICY_PURPOSE"]),
    ("[POLICY_PURPOSE, POLICY_SCOPE]", ["POLICY_SCOPE", "POLICY_PURPOSE"]),
    ("not-a-policy-list", []),
])
def test_policy_id_normalization_is_tolerant(direct_vm, direct_deploy, value, expected):
    contract = deploy(direct_deploy)
    create_agent(contract)
    propose(contract)
    mock_review(direct_vm, response_with_policy_ids(value))
    contract.review_action(1)
    assert list(contract.get_action(1).relevant_policy_ids) == expected


def test_missing_policy_id_field_normalizes_to_empty_list(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)
    create_agent(contract)
    propose(contract)
    mock_review(direct_vm, response_with_policy_ids())
    contract.review_action(1)
    assert list(contract.get_action(1).relevant_policy_ids) == []


@pytest.mark.parametrize("value", [
    ["POLICY_SCOPE", "POLICY_SCOPE", "POLICY_FAKE"],
    "policy_scope | unknown",
    {"policy": "POLICY_SCOPE"},
    None,
])
def test_policy_id_formatting_does_not_change_permitted_decision(direct_vm, direct_deploy, value):
    contract = deploy(direct_deploy)
    create_agent(contract)
    propose(contract)
    mock_review(direct_vm, response_with_policy_ids(value, decision="PERMITTED"))
    contract.review_action(1)
    action = contract.get_action(1)
    assert action.decision == "PERMITTED"
    assert contract.can_execute(1) is True


def test_validator_path_accepts_tolerant_policy_ids(direct_vm, direct_deploy):
    _prepare_validator_case(direct_vm, direct_deploy)
    direct_vm.clear_mocks()
    direct_vm.mock_web("https://evidence.example/validator", {"method": "GET", "status": 200, "body": "validator evidence"})
    mock_review(direct_vm, response_with_policy_ids("policy_scope | POLICY_FAKE"))
    _, _, validator_fn = direct_vm._captured_validators[-1]
    from genlayer import gl
    assert validator_fn(gl.vm.Return(calldata=response_data("PERMITTED", ids=["POLICY_SCOPE"]))) is True


def test_consensus_ignores_raw_evidence_text(direct_vm, direct_deploy):
    _, agrees = consensus_variant(direct_vm, direct_deploy, response(), response(), "Revision A", "Revision B")
    assert agrees is True


def test_consensus_rejects_decision_disagreement(direct_vm, direct_deploy):
    _, agrees = consensus_variant(direct_vm, direct_deploy, response("PERMITTED"), response("DENIED"))
    assert agrees is False


@pytest.mark.parametrize("bad_response,message", [
    (response("MAYBE"), "Invalid authorization decision"),
    (response(evidence_state="UNKNOWN"), "Invalid evidence state"),
])
def test_invalid_canonical_output_rejected(direct_vm, direct_deploy, bad_response, message):
    contract = deploy(direct_deploy)
    create_agent(contract)
    propose(contract)
    mock_review(direct_vm, bad_response)
    with direct_vm.expect_revert(message):
        contract.review_action(1)


def test_extra_llm_fields_ignored(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)
    create_agent(contract)
    propose(contract)
    mock_review(direct_vm, response(confidence="high", notes={"ignored": True}))
    contract.review_action(1)
    assert contract.get_action(1).status == "APPROVED"


def test_action_uses_mandate_version_at_submission(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)
    create_agent(contract)
    propose(contract)
    contract.update_mandate(1, "Updated mandate forbids all payments")
    mock_review(direct_vm, response())
    contract.review_action(1)
    assert contract.get_action(1).mandate_version == 1
    assert contract.get_mandate_version(1, 1) == MANDATE


def test_missing_action_cannot_execute(direct_deploy):
    assert deploy(direct_deploy).can_execute(999) is False


def test_pickling_and_validator_error_handling(direct_vm, direct_deploy):
    direct_vm.check_pickling = True
    contract = deploy(direct_deploy)
    create_agent(contract)
    propose(contract)
    mock_review(direct_vm, response())
    contract.review_action(1)
    leader, leader_fn, validator_fn = direct_vm._captured_validators[-1]
    from genlayer import gl
    assert callable(leader_fn)
    assert validator_fn(gl.vm.Return(calldata=leader)) is True
    assert validator_fn(gl.vm.UserError("failed")) is False
    assert validator_fn(gl.vm.VMError("failed")) is False
    assert validator_fn(gl.vm.Return(calldata="malformed")) is False


def test_same_needs_evidence_decision_agrees_across_evidence_states(direct_vm, direct_deploy):
    _, agrees = consensus_variant(
        direct_vm,
        direct_deploy,
        response("NEEDS_EVIDENCE", "INSUFFICIENT"),
        response("NEEDS_EVIDENCE", "UNAVAILABLE"),
    )
    assert agrees is True


def test_same_decision_agrees_across_different_policy_subsets(direct_vm, direct_deploy):
    _, agrees = consensus_variant(
        direct_vm,
        direct_deploy,
        response("NEEDS_EVIDENCE", "INSUFFICIENT", ids=["POLICY_SCOPE"]),
        response("NEEDS_EVIDENCE", "UNAVAILABLE", ids=["POLICY_EVIDENCE", "POLICY_PURPOSE"]),
    )
    assert agrees is True


@pytest.mark.parametrize("status", [403, 429, 500, 503])
def test_http_evidence_failure_is_conservative(direct_vm, direct_deploy, status):
    contract = deploy(direct_deploy)
    create_agent(contract)
    url = "https://evidence.example/status"
    propose(contract, urls=[url])
    direct_vm.mock_web(url, {"method": "GET", "status": status, "body": "blocked"})
    mock_review(direct_vm, response("PERMITTED", "SUFFICIENT"))
    contract.review_action(1)
    action = contract.get_action(1)
    assert action.decision == "NEEDS_EVIDENCE"
    assert action.evidence_state == "UNAVAILABLE"
    assert contract.can_execute(1) is False


def test_evidence_request_exception_is_conservative(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)
    create_agent(contract)
    propose(contract, urls=["https://evidence.example/timeout"])

    def failing_web(_request):
        raise TimeoutError("evidence request timed out")

    direct_vm._live_web_handler = failing_web
    mock_review(direct_vm, response("PERMITTED", "SUFFICIENT"))
    contract.review_action(1)
    action = contract.get_action(1)
    assert action.decision == "NEEDS_EVIDENCE"
    assert contract.can_execute(1) is False


def test_exactly_one_get_and_never_render_per_evidence_url(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)
    create_agent(contract)
    url = "https://evidence.example/one-fetch"
    propose(contract, urls=[url])
    calls = []

    def counting_web(request):
        calls.append(request)
        return {"ok": {"response": {"status": 200, "headers": {}, "body": b"verified source"}}}

    direct_vm._live_web_handler = counting_web
    mock_review(direct_vm, response("PERMITTED", "SUFFICIENT"))
    contract.review_action(1)
    assert len(calls) == 1
    assert all(request.get("method") == "GET" for request in calls)


def test_validator_evaluate_exception_returns_false(direct_vm, direct_deploy):
    contract, _ = _prepare_validator_case(direct_vm, direct_deploy)

    def failing_web(_request):
        raise RuntimeError("validator evidence failure")

    direct_vm.clear_mocks()
    direct_vm._live_web_handler = failing_web
    _, _, validator_fn = direct_vm._captured_validators[-1]
    from genlayer import gl
    assert validator_fn(gl.vm.Return(calldata=response_data("PERMITTED"))) is False


def test_validator_malformed_llm_result_returns_false(direct_vm, direct_deploy):
    contract, _ = _prepare_validator_case(direct_vm, direct_deploy)
    direct_vm.clear_mocks()
    direct_vm.mock_llm("You are reviewing an autonomous agent's proposed action", "not-json")
    _, _, validator_fn = direct_vm._captured_validators[-1]
    from genlayer import gl
    assert validator_fn(gl.vm.Return(calldata=response_data("PERMITTED"))) is False


def test_multiple_urls_with_one_failure_remain_fail_closed(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)
    create_agent(contract)
    first = "https://evidence.example/first"
    second = "https://evidence.example/second"
    propose(contract, urls=[first, second])
    direct_vm.mock_web(first, {"method": "GET", "status": 200, "body": "verified"})
    direct_vm.mock_web(second, {"method": "GET", "status": 503, "body": "unavailable"})
    mock_review(direct_vm, response("PERMITTED", "SUFFICIENT"))
    contract.review_action(1)
    assert contract.get_action(1).decision == "NEEDS_EVIDENCE"
    assert contract.can_execute(1) is False


def test_permitted_and_needs_evidence_disagree(direct_vm, direct_deploy):
    _, agrees = consensus_variant(
        direct_vm,
        direct_deploy,
        response("PERMITTED"),
        response("NEEDS_EVIDENCE", "UNAVAILABLE"),
    )
    assert agrees is False


def test_denied_and_permitted_disagree(direct_vm, direct_deploy):
    _, agrees = consensus_variant(
        direct_vm,
        direct_deploy,
        response("DENIED", ids=["POLICY_RECIPIENT"]),
        response("PERMITTED"),
    )
    assert agrees is False


def response_data(decision="PERMITTED", evidence_state="SUFFICIENT", ids=None):
    return {
        "decision": decision,
        "evidence_state": evidence_state,
        "relevant_policy_ids": ids or ["POLICY_SCOPE"],
        "relevant_policies": "Scope",
        "reasoning": "Reason",
        "evidence_summary": "Summary",
    }


def _prepare_validator_case(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)
    create_agent(contract)
    url = "https://evidence.example/validator"
    propose(contract, urls=[url])
    direct_vm.mock_web(url, {"method": "GET", "status": 200, "body": "leader evidence"})
    mock_review(direct_vm, response("PERMITTED", "SUFFICIENT"))
    contract.review_action(1)
    return contract, url
