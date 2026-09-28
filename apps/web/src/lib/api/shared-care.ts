"use client";

import { ContractRequestError, idempotencyHeaders } from "@/lib/api/client";
import { requireData } from "@/lib/api/errors";
import type { components } from "@/lib/api/generated";
import type { RealApiClient } from "@/lib/api/real-client";

export type IssuedInvite = components["schemas"]["IssuedInvite"];
export type Consent = components["schemas"]["Consent"];
export type ConsentScope = Consent["scope"];

function checkIssuedInvite(result: IssuedInvite, babyId: string): IssuedInvite {
  if (result.invite.baby_id !== babyId) {
    throw new ContractRequestError("Issued invite does not match the current baby.");
  }
  return result;
}

export async function createInvite(
  client: RealApiClient, babyId: string, email: string, proofToken: string, requestId: string,
): Promise<IssuedInvite> {
  return checkIssuedInvite(requireData(await client.POST("/babies/{baby_id}/invites", {
    params: {
      path: { baby_id: babyId },
      header: { ...idempotencyHeaders(requestId), "X-Reauthentication-Proof": proofToken },
    },
    body: { client_request_id: requestId, email },
  })), babyId);
}

export async function reissueInvite(
  client: RealApiClient, babyId: string, inviteId: string, proofToken: string, requestId: string,
): Promise<IssuedInvite> {
  return checkIssuedInvite(requireData(await client.POST("/invites/{invite_id}/reissue", {
    params: {
      path: { invite_id: inviteId },
      header: { ...idempotencyHeaders(requestId), "X-Reauthentication-Proof": proofToken },
    },
    body: { client_request_id: requestId },
  })), babyId);
}

export async function acceptInvite(
  client: RealApiClient, token: string, policyVersion: string,
  relationship: components["schemas"]["AcceptInvite"]["relationship"], requestId: string,
): Promise<components["schemas"]["BabyAccess"]> {
  if (!policyVersion.trim()) throw new ContractRequestError("An approved shared-use policy version is required.");
  return requireData(await client.POST("/invites/accept", {
    params: { header: idempotencyHeaders(requestId) },
    body: {
      client_request_id: requestId,
      token,
      accept_shared_use: true,
      policy_version: policyVersion,
      relationship,
    },
  }));
}

export async function listConsents(client: RealApiClient, babyId: string) {
  const result = requireData(await client.GET("/consents", {
    params: { query: { baby_id: babyId } },
  }));
  if (result.items.some((item) => item.baby_id !== babyId)) {
    throw new ContractRequestError("Consent list contains another baby's data.");
  }
  return result;
}

export async function getChildDataVerification(client: RealApiClient, babyId: string) {
  const result = requireData(await client.GET("/babies/{baby_id}/child-data-verification", {
    params: { path: { baby_id: babyId } },
  }));
  if (result.baby_id !== babyId) throw new ContractRequestError("Child verification does not match the current baby.");
  return result;
}

export function latestConsent(
  consents: readonly Consent[], scope: ConsentScope, actorUserId?: string,
): Consent | null {
  return consents
    .filter((item) => item.scope === scope && (!actorUserId || item.actor_user_id === actorUserId))
    .reduce<Consent | null>((latest, item) => latest === null || item.version > latest.version ? item : latest, null);
}

export async function setBabyConsent(
  client: RealApiClient, babyId: string, scope: components["schemas"]["SetBabyConsent"]["scope"],
  granted: boolean, policyVersion: string, version: number, requestId: string, proofToken?: string,
): Promise<Consent> {
  if (!policyVersion.trim()) throw new ContractRequestError("An approved policy version is required.");
  if (scope === "BABY_TRAINING" && granted && !proofToken) {
    throw new ContractRequestError("Fresh reauthentication is required for baby training.");
  }
  const result = requireData(await client.PUT("/consents", {
    params: { header: { ...idempotencyHeaders(requestId), ...(proofToken ? { "X-Reauthentication-Proof": proofToken } : {}) } },
    body: { client_request_id: requestId, baby_id: babyId, scope, granted, policy_version: policyVersion, version },
  }));
  if (result.baby_id !== babyId || result.scope !== scope) {
    throw new ContractRequestError("Updated consent does not match the requested baby and scope.");
  }
  return result;
}

export async function setMyTrainingConsent(
  client: RealApiClient, babyId: string, granted: boolean,
  policyVersion: string, version: number, requestId: string,
): Promise<Consent> {
  if (!policyVersion.trim()) throw new ContractRequestError("An approved policy version is required.");
  const result = requireData(await client.PUT("/me/training-consents/{baby_id}", {
    params: { path: { baby_id: babyId }, header: idempotencyHeaders(requestId) },
    body: { client_request_id: requestId, granted, policy_version: policyVersion, version },
  }));
  if (result.baby_id !== babyId || result.scope !== "CONTRIBUTOR_TRAINING") {
    throw new ContractRequestError("Updated training consent does not match the requested baby.");
  }
  return result;
}
