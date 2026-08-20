import { createClient } from "genlayer-js";
import { testnetBradbury } from "genlayer-js/chains";

export const CONTRACT_ADDRESS = (
  import.meta.env.VITE_CONTRACT_ADDRESS
  || "0xac363BA858c1FC97fA1477aF3DE52C238a491978"
).trim();
export const BRADBURY_RPC = import.meta.env.VITE_GENLAYER_RPC_URL || "https://rpc-bradbury.genlayer.com";
export const EXPLORER_URL = testnetBradbury.blockExplorers?.default?.url || "https://explorer-bradbury.genlayer.com";
export const configured = /^0x[0-9a-fA-F]{40}$/.test(CONTRACT_ADDRESS);
const latest = { transactionHashVariant: "latest-nonfinal" };
let client = createClient({ chain: testnetBradbury, endpoint: BRADBURY_RPC });

export function configureClient(account) {
  client = createClient({ chain: testnetBradbury, endpoint: BRADBURY_RPC, ...(account ? { account, provider: window.ethereum } : {}) });
}
export function getClient() { return client; }
function requireContract() { if (!configured) throw new Error("Set VITE_CONTRACT_ADDRESS to the deployed AgentPermit contract."); }
const number = (value) => Number(typeof value === "bigint" ? value : value || 0);
const record = (value) => value instanceof Map ? Object.fromEntries(value) : (value || {});
const array = (value) => value instanceof Map ? [...value.values()] : Array.isArray(value) ? value : [];
const booleanOrNull = (value) => typeof value === "boolean" ? value : null;

export async function ensureBradbury(provider = window.ethereum) {
  if (!provider?.request) throw new Error("No EIP-1193 wallet found.");
  const chainId = "0x107d";
  if (String(await provider.request({ method: "eth_chainId" })).toLowerCase() === chainId) return;
  try { await provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId }] }); }
  catch (error) {
    if (error?.code !== 4902 && !/unknown|unrecognized|not added/i.test(error?.message || "")) throw error;
    await provider.request({ method: "wallet_addEthereumChain", params: [{ chainId, chainName: testnetBradbury.name, rpcUrls: [BRADBURY_RPC], nativeCurrency: testnetBradbury.nativeCurrency, blockExplorerUrls: [EXPLORER_URL] }] });
    await provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId }] });
  }
}

export function normalizeAgent(raw, id) { const a = record(raw); return { id: number(id), name: String(a.name || ""), owner: String(a.owner || ""), mandate: String(a.mandate || ""), mandate_version: number(a.mandate_version), active: Boolean(a.active) }; }
export function normalizeAction(raw, id) {
  const a = record(raw);
  const target = String(a.target ?? a.recipient ?? "");
  return {
    id: number(id), agent_id: number(a.agent_id), title: String(a.title || ""), description: String(a.description || ""), action_type: String(a.action_type || ""),
    recipient: String(a.recipient || target), target, amount: String(a.amount || ""), asset: String(a.asset || ""), purpose: String(a.purpose || ""), evidence: String(a.evidence || ""),
    evidence_urls: array(a.evidence_urls).map(String), mandate_version: number(a.mandate_version), proposer: String(a.proposer || ""), status: String(a.status || "PENDING_REVIEW"),
    decision: String(a.decision || ""), evidence_state: String(a.evidence_state || ""), reasoning: String(a.reasoning || ""), relevant_policy_ids: array(a.relevant_policy_ids).map(String),
    relevant_policies: String(a.relevant_policies || ""), evidence_summary: String(a.evidence_summary || ""), payload_hash: String(a.payload_hash || ""), expires_at: String(a.expires_at || ""),
    revoked: booleanOrNull(a.revoked), consumed: booleanOrNull(a.consumed), can_execute: false,
  };
}
async function read(name, args=[]) { requireContract(); return client.readContract({ address: CONTRACT_ADDRESS, functionName: name, args, ...latest }); }
async function write(name, args=[]) { requireContract(); return client.writeContract({ address: CONTRACT_ADDRESS, functionName: name, args, value: 0n }); }
export async function readAgent(id) { return normalizeAgent(await read("get_agent", [Number(id)]), id); }
export async function readAction(id) { return normalizeAction(await read("get_action", [Number(id)]), id); }
export async function readAll() {
  if (!configured) return { agents: [], actions: [] };
  const [ac, xc] = await Promise.all([read("get_agent_count"), read("get_action_count")]);
  const agents = await Promise.all(Array.from({length:number(ac)},(_,i)=>readAgent(i+1)));
  const actions = await Promise.all(Array.from({length:number(xc)},async(_,i)=>{
    const action = await readAction(i+1);
    if (action.decision !== "PERMITTED" || !action.target || !action.payload_hash) return action;
    try { return {...action, can_execute: await canExecute(i+1, action.target, action.payload_hash)}; } catch { return action; }
  }));
  return { agents, actions: actions.reverse() };
}
export const createAgent = ({name,mandate}) => write("create_agent", [name,mandate]);
export const updateMandate = (id, mandate) => write("update_mandate", [Number(id),mandate]);
export const proposeAction = (p) => write("propose_action", [Number(p.agent_id),p.title,p.description,p.action_type,p.target ?? p.recipient,p.amount,p.asset,p.purpose,p.evidence,p.evidence_urls,p.payload,p.expires_at]);
export const reviewAction = (id) => write("review_action", [Number(id)]);
export const canExecute = (id, target, payloadHash) => read("can_execute", [Number(id),String(target),String(payloadHash)]).then(Boolean);
export const consumePermit = (id, target, payloadHash) => write("consume_permit", [Number(id),String(target),String(payloadHash)]);
export const revokePermit = (id) => write("revoke_permit", [Number(id)]);
export const getTransaction = (hash) => client.getTransaction({ hash });
export const txUrl = (hash) => `${EXPLORER_URL.replace(/\/$/,"")}/tx/${hash}`;
export const displayLifecycle = (action) => {
  if (action?.consumed === true) return "CONSUMED";
  if (action?.revoked === true) return "REVOKED";
  if (action?.revoked !== false || action?.consumed !== false) return "UNKNOWN";
  const expiry = Date.parse(action.expires_at || "");
  if (!Number.isFinite(expiry)) return "UNKNOWN";
  return Date.now() > expiry ? "EXPIRED" : "ACTIVE";
};
export const executionAuthorization = (action) => action?.decision === "PERMITTED" && action?.can_execute === true ? "ACTIVE" : "BLOCKED";
export const errorText = (e) => e?.code === 4001 ? "Wallet signature rejected." : (e?.shortMessage || e?.details || e?.message || "RPC temporarily unavailable.");
