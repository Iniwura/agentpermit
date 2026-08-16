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

The frontend treats the stored contract decision and `can_execute()` as the source of truth. An `ACCEPTED` transaction alone does not imply authorization.

## Evidence Safety

- Proposer-written evidence is not automatically independent proof.
- Evidence URLs are fetched independently, one `web.get` per URL.
- Evidence failures fail closed.
- HTTP 403, 429, 500, 503, and request timeouts cannot produce `PERMITTED` when material evidence is required.
- Evidence URLs are limited to three HTTP(S) sources.

## Scoped Permits

The frontend derives a permit only when:

```text
decision == "PERMITTED"
and
can_execute == true
```

Permit IDs are deterministic display identifiers such as `AP-0001` and `AP-0002`. Permit JSON is a machine-readable representation of current onchain authorization state; it is not cryptographically signed.

## Machine-Readable Permit

```json
{
  "permit_id": "AP-0002",
  "agent_id": 1,
  "request_id": 2,
  "capability": "PAYMENT",
  "target": "Vercel",
  "amount": "120",
  "asset": "USDC",
  "mandate_version": 1,
  "authorization": "PERMITTED",
  "can_execute": true
}
```

Downstream tools, wallets, and APIs must explicitly choose to check AgentPermit before execution and should apply their own replay and idempotency controls.

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

```text
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
Permit derivation / can_execute
```

## Frontend

The React control plane provides wallet connect/disconnect and account switching, versioned mandates, permission request submission, persisted transaction state, four-second authorization polling with backoff, timeout and undetermined handling, issued-permit lists, permit detail views, and copyable permit JSON.

Routes include `/`, `/app`, `/app/agents`, `/app/requests`, `/app/requests/new`, `/app/requests/:id`, `/app/permits`, `/app/permits/:id`, and `/app/policy`.

## Contract

- Network: GenLayer Bradbury testnet
- Contract: `0xb1DbF4AA85B585652bD2d453CAb2c1426fFB70E8`
- Contract logic and public ABI are deployed and must not be changed for this submission pass.

## Testing

Current result: **64 tests passing**.

Coverage includes ownership, mandate versioning, request creation, evidence failures, `PERMITTED`, `DENIED`, `NEEDS_EVIDENCE`, decision consensus, tolerant policy-ID normalization, validator error handling, exactly one web GET per source, no `web.render`, fail-closed behavior, and `can_execute` gating.

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
```

## Deployment

The deployment script targets `contracts/agent_permit.py` and validates the current GenLayer receipt shape without serializing BigInt-containing receipts. Do not redeploy the already-live Bradbury contract as part of normal local setup.

## Security / Limitations

- Bradbury is a testnet.
- Web availability can vary; required evidence failures fail closed.
- Permit JSON is not independently signed.
- AgentPermit authorizes; it does not execute actions or hold funds.
- Downstream systems must explicitly enforce the permit.
- Production use requires deeper security review, identity binding, replay protection, and idempotent execution controls.
