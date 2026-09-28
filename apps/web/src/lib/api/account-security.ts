"use client";

import { idempotencyHeaders } from "@/lib/api/client";
import { ContractRequestError } from "@/lib/api/client";
import { requireData } from "@/lib/api/errors";
import type { components } from "@/lib/api/generated";
import type { RealApiClient } from "@/lib/api/real-client";

export type ReauthenticationOperation = components["schemas"]["CreateReauthenticationChallenge"]["operation"];
export type ReauthenticationChallenge = components["schemas"]["ReauthenticationChallenge"];
export type SessionRevocation = components["schemas"]["SessionRevocation"];
export type SessionRevocationScope = components["schemas"]["RevokeSessions"]["scope"];

export async function createReauthenticationChallenge(
  client: RealApiClient, operation: ReauthenticationOperation, babyId: string, requestId: string,
): Promise<ReauthenticationChallenge> {
  const result = requireData(await client.POST("/auth/reauthentication/challenges", {
    params: { header: idempotencyHeaders(requestId) },
    body: { client_request_id: requestId, operation, baby_id: babyId },
  }));
  if (result.baby_id !== babyId || result.operation !== operation) {
    throw new ContractRequestError("Reauthentication challenge scope does not match the request.");
  }
  return result;
}

export async function createReauthenticationProof(
  client: RealApiClient, challenge: ReauthenticationChallenge, requestId: string,
): Promise<string> {
  const result = requireData(await client.POST("/auth/reauthentication/proofs", {
    params: { header: idempotencyHeaders(requestId) },
    body: { client_request_id: requestId, challenge_id: challenge.challenge_id },
  }));
  if (result.challenge_id !== challenge.challenge_id || result.baby_id !== challenge.baby_id ||
      result.operation !== challenge.operation || !result.proof_token) {
    throw new ContractRequestError("Fresh reauthentication proof was not returned for this action.");
  }
  return result.proof_token;
}

export async function revokeSessions(
  client: RealApiClient, scope: SessionRevocationScope, requestId: string,
): Promise<SessionRevocation> {
  const result = requireData(await client.POST("/auth/session-revocations", {
    params: { header: idempotencyHeaders(requestId) },
    body: { client_request_id: requestId, scope },
  }));
  if (result.scope !== scope) throw new ContractRequestError("Session revocation scope does not match the request.");
  return result;
}

export async function getSessionRevocation(
  client: RealApiClient, revocationId: string,
): Promise<SessionRevocation> {
  const result = requireData(await client.GET("/auth/session-revocations/{revocation_id}", {
    params: { path: { revocation_id: revocationId } },
  }));
  if (result.revocation_id !== revocationId) {
    throw new ContractRequestError("Session revocation id does not match the request.");
  }
  return result;
}
