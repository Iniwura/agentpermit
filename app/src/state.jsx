import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import * as api from "./api";

const Context = createContext(null);
const scope = (suffix) => `agentpermit.${suffix}.v4.${api.CONTRACT_ADDRESS.toLowerCase()}`;
const REVIEW_KEY = scope("reviewState");
const TX_KEYS = {
  agentCreation: scope("activeAgentCreation"),
  actionSubmission: scope("activeActionSubmission"),
  mandateUpdate: scope("mandateUpdate"),
};
const activeStages = new Set(["AWAITING_SIGNATURE","REVIEW_SUBMITTED","PENDING_CONSENSUS","CONSENSUS_PROCESSING","FINALIZING","STATUS_UNAVAILABLE"]);
const terminal = new Set(["FINALIZED","LEADER_TIMEOUT","VALIDATORS_TIMEOUT","UNDETERMINED","FAILED","EXPIRED"]);
const load = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) || fallback; } catch { return fallback; } };
const statusOf = (tx) => String(tx?.statusName ?? tx?.status_name ?? tx?.status ?? "UNKNOWN").replaceAll("_"," ").toUpperCase();
function stageOf(status, tx) {
  const execution = String(tx?.txExecutionResultName ?? tx?.tx_execution_result_name ?? "").toUpperCase();
  if (/LEADER.?TIMEOUT/.test(status)) return "LEADER_TIMEOUT";
  if (/VALIDATOR.?TIMEOUT/.test(status)) return "VALIDATORS_TIMEOUT";
  if (/UNDETERMINED|NO.?MAJORITY/.test(status)) return "UNDETERMINED";
  if (/EXPIRED/.test(status)) return "EXPIRED";
  if (/FAILED|REVERTED|REJECTED|CANCELLED/.test(status+execution)) return "FAILED";
  if (/ACCEPTED|FINALIZED|COMPLETED|SUCCESS/.test(status+execution)) return "FINALIZING";
  if (status === "UNKNOWN") return "STATUS_UNAVAILABLE";
  if (/QUEUE|PENDING/.test(status)) return "PENDING_CONSENSUS";
  return "CONSENSUS_PROCESSING";
}

export function PermitProvider({children}) {
  const [account,setAccount] = useState(null), [agents,setAgents] = useState([]), [actions,setActions] = useState([]);
  const [error,setError] = useState(""), [loading,setLoading] = useState(false);
  const [reviews,setReviews] = useState(()=>load(REVIEW_KEY,{})), [transactions,setTransactions] = useState(()=>Object.fromEntries(Object.entries(TX_KEYS).map(([kind,key])=>[kind,load(key,null)]).filter(([,value])=>value)));
  const reviewsRef = useRef(reviews), disconnected = useRef(false), poll = useRef({timer:0,busy:false,backoff:8000});
  useEffect(()=>{ reviewsRef.current=reviews; localStorage.setItem(REVIEW_KEY,JSON.stringify(reviews)); },[reviews]);
  useEffect(()=>Object.entries(TX_KEYS).forEach(([kind,key])=>transactions[kind]&&localStorage.setItem(key,JSON.stringify(transactions[kind]))),[transactions]);
  const refresh = useCallback(async()=>{ setLoading(true); try { const data=await api.readAll(); setAgents(data.agents); setActions(data.actions); setError(""); return data; } catch(e){setError(api.errorText(e)); throw e;} finally{setLoading(false);} },[]);
  const connect = useCallback(async()=>{ if(!window.ethereum) throw new Error("Install an EIP-1193 wallet."); const accounts=await window.ethereum.request({method:"eth_requestAccounts"}); if(!accounts?.[0]) throw new Error("Wallet returned no account."); await api.ensureBradbury(); disconnected.current=false; api.configureClient(accounts[0]); setAccount(accounts[0]); return accounts[0]; },[]);
  const disconnect = useCallback(async()=>{ disconnected.current=true; setAccount(null); api.configureClient(null); try { await window.ethereum?.request?.({method:"wallet_revokePermissions",params:[{eth_accounts:{}}]}); } catch {} },[]);
  const updateReview = useCallback((id,patch)=>setReviews(current=>({...current,[id]:{actionId:Number(id),lifecycleStage:"REVIEW_DRAFT",...current[id],...patch}})),[]);
  const syncReview = useCallback(async(id)=>{ const saved=reviewsRef.current[id]; if(!saved?.transactionHash) return saved; try { const tx=await api.getTransaction(saved.transactionHash); let lifecycleStage=stageOf(statusOf(tx),tx), snapshot=saved.snapshot, allowed=saved.canExecute||false; if(lifecycleStage==="FINALIZING"){ try { [snapshot,allowed]=await Promise.all([api.readAction(id),api.canExecute(id)]); if(snapshot.status!=="PENDING_REVIEW"){lifecycleStage="FINALIZED"; refresh().catch(()=>{});} } catch {lifecycleStage="STATUS_UNAVAILABLE";} } const next={...saved,lifecycleStage,lastKnownStatus:statusOf(tx),snapshot,canExecute:allowed,error:""}; updateReview(id,next); return next; } catch(e){ const next={...saved,lifecycleStage:terminal.has(saved.lifecycleStage)?saved.lifecycleStage:"STATUS_UNAVAILABLE",error:api.errorText(e)}; updateReview(id,next); return next;} },[refresh,updateReview]);
  const sign = useCallback(async(kind,key,fn,payload)=>{ if(!account) throw new Error("Connect a Bradbury wallet first."); setTransactions(c=>({...c,[kind]:{key,payload,lifecycleStage:"AWAITING_SIGNATURE"}})); try { const hash=await fn(); const tx={key,payload,transactionHash:String(hash),submittedAt:new Date().toISOString(),lifecycleStage:"REVIEW_SUBMITTED"}; setTransactions(c=>({...c,[kind]:tx})); return hash; } catch(e){ setTransactions(c=>({...c,[kind]:{key,payload,lifecycleStage:e?.code===4001?"SIGNATURE_REJECTED":"FAILED",error:api.errorText(e)}})); throw e;} },[account]);
  const beginReview=useCallback(async(id)=>{ updateReview(id,{lifecycleStage:"AWAITING_SIGNATURE",error:""}); try {const hash=await api.reviewAction(id); updateReview(id,{transactionHash:String(hash),submittedAt:new Date().toISOString(),lifecycleStage:"REVIEW_SUBMITTED"}); return hash;} catch(e){updateReview(id,{lifecycleStage:e?.code===4001?"REVIEW_DRAFT":"FAILED",error:api.errorText(e)});throw e;}},[updateReview]);
  useEffect(()=>{ const p=window.ethereum;if(!p)return; p.request({method:"eth_accounts"}).then(a=>{if(a?.[0]&&!disconnected.current){setAccount(a[0]);api.configureClient(a[0]);}}).catch(()=>{}); const accounts=a=>{const next=a?.[0]||null;if(!next){setAccount(null);api.configureClient(null);}else if(!disconnected.current){setAccount(next);api.configureClient(next);}}; const chain=()=>refresh().catch(()=>{});p.on?.("accountsChanged",accounts);p.on?.("chainChanged",chain);return()=>{p.removeListener?.("accountsChanged",accounts);p.removeListener?.("chainChanged",chain);};},[refresh]);
  useEffect(()=>{refresh().catch(()=>{});},[refresh]);
  useEffect(()=>{const manager=poll.current; const sync=async()=>{if(manager.busy)return;const active=Object.values(reviewsRef.current).filter(r=>r.transactionHash&&activeStages.has(r.lifecycleStage));if(!active.length)return;manager.busy=true;await Promise.all(active.map(r=>syncReview(r.actionId)));manager.busy=false;const unavailable=active.some(r=>r.lifecycleStage==="STATUS_UNAVAILABLE");manager.backoff=unavailable?Math.min(manager.backoff*1.5,30000):8000;manager.timer=setTimeout(sync,unavailable?manager.backoff:4000);};sync();const wake=()=>{clearTimeout(manager.timer);sync();};const visible=()=>document.visibilityState==="visible"&&wake();window.addEventListener("focus",wake);document.addEventListener("visibilitychange",visible);return()=>{clearTimeout(manager.timer);window.removeEventListener("focus",wake);document.removeEventListener("visibilitychange",visible);};},[reviews,syncReview]);
  const value=useMemo(()=>({account,agents,actions,error,loading,reviews,transactions,configured:api.configured,connect,disconnect,refresh,setError,updateReview,syncReview,beginReview,createAgent:(p)=>sign("agentCreation","create",()=>api.createAgent(p),p),proposeAction:(p)=>sign("actionSubmission","propose",()=>api.proposeAction(p),p),updateMandate:(id,text)=>sign("mandateUpdate",String(id),()=>api.updateMandate(id,text),{id,text})}),[account,agents,actions,error,loading,reviews,transactions,connect,disconnect,refresh,updateReview,syncReview,beginReview,sign]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
export const usePermit=()=>useContext(Context);
