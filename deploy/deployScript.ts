import { readFileSync } from "node:fs";
import path from "node:path";
import type { GenLayerClient, TransactionHash } from "genlayer-js/types";
import { ExecutionResult, TransactionStatus } from "genlayer-js/types";

export default async function main(client: GenLayerClient<any>) {
  const code = new Uint8Array(
    readFileSync(path.resolve(process.cwd(), "contracts/agent_permit.py"))
  );

  const hash = await client.deployContract({
    code,
    args: [],
  });

  const receipt = await client.waitForTransactionReceipt({
    hash: hash as TransactionHash,
    status: TransactionStatus.ACCEPTED,
    retries: 200,
  });

  const receiptWithSdkShape = receipt as typeof receipt & { status_name?: TransactionStatus };
  const status = receiptWithSdkShape.status_name ?? receipt.statusName;
  const contractAddress = receipt.txDataDecoded && "contractAddress" in receipt.txDataDecoded
    ? receipt.txDataDecoded.contractAddress
    : undefined;
  const hasValidContractAddress = typeof contractAddress === "string"
    && /^0x[0-9a-fA-F]{40}$/.test(contractAddress);
  const hasSuccessfulTransactionState = (
    status === TransactionStatus.ACCEPTED || status === TransactionStatus.FINALIZED
  ) && receipt.resultName === "AGREE"
    && receipt.txExecutionResultName === ExecutionResult.FINISHED_WITH_RETURN;

  if (!hasValidContractAddress || !hasSuccessfulTransactionState) {
    console.error("AgentPermit deployment failed.", {
      transactionHash: hash,
      status,
      result: receipt.resultName,
      txExecutionResult: receipt.txExecutionResultName,
      contractAddress,
    });
    console.dir(receipt, { depth: null });
    throw new Error("AgentPermit deployment execution was not successful");
  }

  console.log("AgentPermit deployed successfully.");
  console.log(`\nTransaction Hash: ${hash}`);
  console.log(`Contract Address: ${contractAddress}`);
}
