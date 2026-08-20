# AgentPermit

## Runtime authorization for autonomous AI agents.

AgentPermit turns owner-defined mandates into verifiable onchain permissions that autonomous agents can present before executing sensitive actions.

AgentPermit is runtime authorization infrastructure between an autonomous agent and the wallets, APIs, tools, infrastructure, and other systems it wants to use. It is not a DAO governance app, a Veto clone, an autonomous agent, a wallet executor, or a trading bot.

## Problem

Autonomous agents increasingly interact with wallets, APIs, tools, infrastructure, and financial systems. The problem is not only whether an agent can execute an action; it is whether that action is actually within the authority granted by its owner.

## Solution

An owner defines a mandate. The agent creates a permission request before acting. AgentPermit evaluates scope, spend limit, recipient, purpose, and evidence against the active mandate version. GenLayer validators independently evaluate the authorization decision.

If the request is permitted, a scoped permit becomes available. If it is denied or under-evidenced, no permit is issued.

## Core Flow

```text
Owner Mandate
↓
Agent Permission Request
↓
Independent Evidence
↓
GenLayer Authorization
↓
Scoped Permit
↓
Downstream Tool / Wallet / API
```

## Why GenLayer

Natural-language mandates and real-world evidence cannot be handled cleanly by ordinary deterministic smart contracts alone. GenLayer enables nondeterministic web access and LLM reasoning while validators independently reach consensus on the authorization decision. This is not one AI model deciding authority.

## Consensus Design

The contract uses `gl.vm.run_nondet_unsafe(evaluate, validate)`.

The consensus-critical field is `decision`. `evidence_state`, `relevant_policy_ids`, `reasoning`, and `evidence_summary` are audit and display metadata. Validators may observe different web availability or identify different policy subsets while still reaching the same safe authorization decision, so consensus compares the decision only.

## Authorization Decisions

| Decision | Permit | `can_execute` |
| --- | --- | ---: |
| `PERMITTED` | Issued | `true` |
| `DENIED` | None | `false` |
| `NEEDS_EVIDENCE` | None | `false` |

The frontend treats the stored contract decision and exact-scope can_execute(action_id, target, payload_hash) result as the source of truth. An ACCEPTED transaction alone does not imply authorization.

## Evidence Safety

- Proposer-written evidence is not automatically independent proof.
- Evidence URLs are fetched independently, one `web.get` per URL.
- Evidence failures fail closed.
- HTTP 403, 429, 500, 503, and request timeouts cannot produce `PERMITTED` when material evidence is required.
- Evidence URLs are limited to three HTTP(S) sources.

## Scoped Permits

The frontend lists every stored PERMITTED decision as a historical permit and shows exact-scope execution authorization separately. An active execution permit is bound to one target, one payload commitment, one expiry, and one lifecycle state; a decision alone is never enough. Permit IDs are deterministic display identifiers such as AP-0001 and AP-0002. Permit JSON is a machine-readable representation of current onchain authorization state; it is not cryptographically signed.

Downstream tools, wallets, and APIs must enforce the exact permit scope before execution. They should pass the same target identity and payload commitment to can_execute(action_id, target, payload_hash), then call consume_permit(action_id, target, payload_hash) immediately before the external call. Copied permit JSON, a display ID, or a decision string is not an authorization token.

## Permit Scope & Lifecycle

A permit is executable only when every gate below succeeds:

~~~text
APPROVED ACTION
↓
EXACT TARGET
↓
EXACT PAYLOAD COMMITMENT
↓
NOT EXPIRED
↓
NOT REVOKED
↓
NOT CONSUMED
↓
can_execute(action_id, target, payload_hash)
↓
consume_permit(action_id, target, payload_hash)
↓
Execution Boundary
↓
Target
~~~

- target is the exact downstream identity. The contract stores it with the action and rejects any mismatch.
- payload_hash is the SHA-256 digest of the exact UTF-8 canonical payload string. Plain text is hashed byte-for-byte; top-level JSON objects/lists are compacted with sorted keys, compact separators, UTF-8 characters, and strict JSON values before submission. Top-level numbers, booleans, null, and bytes are rejected to avoid type collisions.
- Expiry uses the GenLayer transaction datetime at one-second precision from gl.message_raw[datetime]. Fractional seconds are truncated; a missing or malformed runtime value fails closed. The boundary is inclusive: current_time is less than or equal to expires_at.
- The contract owner has emergency/admin authority to revoke any permit; the registered agent owner can revoke permits for that agent. Revocation is irreversible.
- consume_permit performs the same checks as can_execute and marks the permit consumed exactly once. A replay, scope mismatch, expiry, revocation, or failed authorization lookup is blocked.
- Consumption authorizes one execution attempt, not a guaranteed successful external action. It happens before the non-atomic target call; if that call fails, the permit remains consumed and replay is blocked.

## Machine-Readable Permit

~~~json
{
  "permit_id": "AP-0002",
  "agent_id": 1,
  "request_id": 2,
  "capability": "PAYMENT",
  "target": "Vercel",
  "payload_hash": "sha256-of-exact-payload",
  "amount": "120",
  "asset": "USDC",
  "mandate_version": 1,
  "authorization": "PERMITTED",
  "expires_at": "2099-01-01T00:00:00Z",
  "revoked": false,
  "consumed": false,
  "can_execute": true
}
~~~

Downstream tools, wallets, and APIs must enforce the exact scope and lifecycle through the contract immediately before execution; a copied JSON object, display ID, or decision string is not an authorization token.

## Demo Example

Successful live flow:

- Agent: `ResearchOps-01`
- Mandate: May spend up to 500 USDC per transaction on developer infrastructure, API access, cloud compute, and research tools.
- Request: Pay 120 USDC to Vercel for cloud deployment and developer infrastructure.
- Evidence: `https://vercel.com/docs`
- Result: `PERMITTED`
- Evidence state: `SUFFICIENT`
- Permit: `AP-0002`
- Execution: `AUTHORIZED`

Fail-closed flow:

- The same agent requests API-related spending with no independent evidence.
- Result: `NEEDS_EVIDENCE`
- No permit is issued.

## Architecture

~~~text
React/Vite frontend
↓
GenLayer JS
↓
AgentPermit Intelligent Contract
↓
Web evidence + LLM evaluation
↓
Validator consensus
↓
Onchain authorization decision
↓
can_execute / consume_permit
↓
Execution enforcement boundary
~~~

## Frontend

The React control plane provides wallet connect/disconnect and account switching, versioned mandates, permission request submission, persisted transaction state, four-second authorization polling with backoff, timeout and undetermined handling, issued-permit lists, permit detail views, and copyable permit JSON.

Routes include `/`, `/app`, `/app/agents`, `/app/requests`, `/app/requests/new`, `/app/requests/:id`, `/app/permits`, `/app/permits/:id`, and `/app/policy`.

The frontend is migrated to the hardened ABI. Permission requests collect an exact target, raw or structured JSON payload, and expiry; structured payloads are recursively key-sorted and compacted before a Web Crypto SHA-256 preview. The API submits the 12-argument propose_action call and reads can_execute(action_id, target, payload_hash) with the same scope. Policy decision and execution authorization are displayed separately, historical permits remain visible, and revocation rereads the action after its persisted transaction reaches finality. All deployment-scoped frontend state uses v5 keys scoped to the lowercase deployed contract address, including revocations.

## Contract

- Network: GenLayer Bradbury testnet
- Contract: `0xac363BA858c1FC97fA1477aF3DE52C238a491978`
- The deployed Bradbury address exposes the hardened scope/lifecycle ABI used by the frontend; this migration does not redeploy the contract.

## Testing

Contract tests: **83 passing**.
Execution-gate tests: **25 passing**.
Full pytest result: **108 passing**.

Coverage includes exact payload canonicalization, object key ordering, array ordering, Unicode/nested JSON, scalar rejection, exact target matching, ownership, mandate versioning, request creation, evidence failures, PERMITTED, DENIED, NEEDS_EVIDENCE, decision consensus, tolerant policy-ID normalization, validator error handling, exactly one web GET per source, no web.render, fail-closed behavior, expiry boundaries, revocation, consumption, replay prevention, malformed runtime-clock fail-closed behavior, and exact-scope can_execute gating.
Execution-gate coverage includes exact scope forwarding, blocked execution, deterministic payload hashing, duplicate protection, target exceptions and explicit failure values, fail-closed authorization/consumption errors, and execution-time authorization refresh.

## Local Setup

```bash
cd /home/ini/agentpermit/app
npm install
cp .env.example .env
npm run dev
```

Set `VITE_CONTRACT_ADDRESS` to the deployed Bradbury address in `.env` when running the frontend locally. The checked-in `.env.example` contains the final public contract address and no secrets.

## Verification

```bash
cd /home/ini/agentpermit/app
npm run build

cd /home/ini/agentpermit
gltest test/test_agent_permit.py -v
genvm-lint check contracts/agent_permit.py
pytest -q
```

## Deployment

The deployment script targets `contracts/agent_permit.py` and validates the current GenLayer receipt shape without serializing BigInt-containing receipts. Do not redeploy the already-live Bradbury contract as part of normal local setup.

## Security / Limitations

- Bradbury is a testnet.
- Permit expiry reads the transaction datetime supplied in gl.message_raw. If the target runtime omits or misreports it, the contract fails closed; verify this field on Bradbury before redeployment.
- Web availability can vary; required evidence failures fail closed.
- Permit JSON is not independently signed.
- The repository includes a reference enforcement adapter, AgentPermitExecutionGate, that derives the exact target and payload hash, calls can_execute(request_id, target, payload_hash), then consumes the permit before forwarding a target call.
- Real production integrations must place the adapter, or equivalent logic, directly in their actual tool, wallet, API, or infrastructure execution path.
- The Python adapter does not automatically secure arbitrary external systems.
- Production identity binding, replay/idempotency controls, and downstream failure semantics still require deeper integration and security review.

## Execution Enforcement

AgentPermit does not stop at producing a permit. The repository includes AgentPermitExecutionGate, a reference enforcement consumer that derives the exact target identity and payload commitment, calls can_execute(request_id, target, payload_hash), consumes the permit, and only then forwards a downstream tool, wallet, API, or infrastructure action.

~~~text
Agent Permission Request
↓
GenLayer Authorization
↓
Exact target + payload hash
↓
can_execute(action_id, target, payload_hash)
↓
consume_permit(action_id, target, payload_hash)
↓
AgentPermitExecutionGate
↓ false                         ↓ true
execution blocked               downstream action executed
                                 (permit is now consumed)
~~~

- AgentPermit Intelligent Contract = authorization source.
- AgentPermitExecutionGate = enforcement consumer.
- Copied permit JSON, a permit ID, or a request decision is not trusted as authorization.
- Authorization and consumption are reread at execution time; the gate does not cache contract state.
- Duplicate execution protection is included per gate instance and by contract-level consumed state.
- Consumption precedes the non-atomic external call. If the target fails, the permit remains consumed and replay is blocked.

The gate uses a small target.call(payload) protocol. It is a reference adapter, not a wallet, API gateway, or claim that arbitrary external systems are secured automatically.
