"use client";

import { ContractRequestError, idempotencyHeaders } from "@/lib/api/client";
import { requireData } from "@/lib/api/errors";
import type { components } from "@/lib/api/generated";
import type { RealApiClient } from "@/lib/api/real-client";

export type BabyDeletionJob = components["schemas"]["DeletionJob"];

function checkJob(job: BabyDeletionJob, babyId: string, userId: string): BabyDeletionJob {
  if (job.baby_id !== babyId || job.requester_user_id !== userId || job.scope !== "ALL" || !job.access_blocked) {
    throw new ContractRequestError("Baby deletion response does not match this request.");
  }
  return job;
}

export async function deleteBabyData(client: RealApiClient, babyId: string, userId: string,
  version: number, proofToken: string, requestId: string): Promise<BabyDeletionJob> {
  if (!proofToken) throw new ContractRequestError("Fresh DELETE_BABY proof is required.");
  const result = requireData(await client.DELETE("/babies/{baby_id}/data", {
    params: { path: { baby_id: babyId }, query: { version, confirm: "DELETE_BABY" },
      header: { ...idempotencyHeaders(requestId), "X-Reauthentication-Proof": proofToken } },
  }));
  return checkJob(result, babyId, userId);
}

export async function getBabyDeletion(client: RealApiClient, deletionJobId: string, userId: string): Promise<BabyDeletionJob> {
  const result = requireData(await client.GET("/deletions/{deletion_job_id}", {
    params: { path: { deletion_job_id: deletionJobId } },
  }));
  if (result.deletion_job_id !== deletionJobId || result.requester_user_id !== userId || result.scope !== "ALL") {
    throw new ContractRequestError("Deletion job does not belong to this requester.");
  }
  return result;
}

export async function retryBabyDeletion(client: RealApiClient, deletionJobId: string, userId: string,
  expectedAttempt: number, requestId: string): Promise<BabyDeletionJob> {
  const result = requireData(await client.POST("/deletions/{deletion_job_id}/retry", {
    params: { path: { deletion_job_id: deletionJobId }, header: idempotencyHeaders(requestId) },
    body: { client_request_id: requestId, expected_attempt: expectedAttempt },
  }));
  if (result.deletion_job_id !== deletionJobId || result.requester_user_id !== userId || result.scope !== "ALL") {
    throw new ContractRequestError("Deletion retry does not match this requester.");
  }
  return result;
}
