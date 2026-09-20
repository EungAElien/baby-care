import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { createApiClient } from "../src/lib/api/client";
import { PrivateScope } from "../src/lib/private-scope";
import { consumeInviteFragment } from "../src/lib/invite-link";
import { createReauthenticationChallenge, createReauthenticationProof, revokeSessions } from "../src/lib/api/account-security";
import { acceptInvite, createInvite, setBabyConsent, setMyTrainingConsent } from "../src/lib/api/shared-care";
import { resolveApprovedPolicy } from "../src/lib/consent-policy";
import { revokeAndSignOut } from "../src/lib/auth/session-control";

const scenarios = JSON.parse(readFileSync(resolve(process.cwd(), "../../contracts/목 응답과 시험 사용자 배치.json"), "utf8")) as {
  scenarios: { name: string; response: { status: number; body: unknown } }[];
};
const scenario = (name: string) => {
  const result = scenarios.scenarios.find((entry) => entry.name === name);
  if (!result) throw new Error(`Missing fixture ${name}`);
  return result.response;
};
const requestId = "10000000-0000-4000-8000-000000000901";
const babyId = "10000000-0000-4000-8000-000000000101";

function clientFor(name: string, requests: Request[], suppliedScope?: PrivateScope) {
  const scope = suppliedScope ?? new PrivateScope(new QueryClient());
  if (!scope.snapshot().userId) scope.set("10000000-0000-4000-8000-000000000001", babyId);
  const fixture = scenario(name);
  const fetchImpl = vi.fn(async (request: Request) => {
    requests.push(request);
    return Response.json(fixture.body, { status: fixture.status });
  }) as unknown as typeof fetch;
  return createApiClient({ baseUrl: "https://api.example.invalid/v1", scope,
    auth: { getSession: async () => ({ userId: scope.snapshot().userId!, accessToken: "synthetic" }), refreshSession: async () => null },
    fetchImpl });
}

describe("A-03 invite, consent and account security boundaries", () => {
  it("removes an issued secret from browser history before returning it to the UI", () => {
    const replaceState = vi.fn();
    expect(consumeInviteFragment({ pathname: "/invite/issued-id", hash: "#private%2Dtoken" }, { replaceState }))
      .toBe("private-token");
    expect(replaceState).toHaveBeenCalledWith(null, "", "/invite/issued-id");
    expect(replaceState.mock.calls[0]!.join(" ")).not.toContain("token");
    expect(consumeInviteFragment({ pathname: "/invite/id", hash: "#%bad" }, { replaceState })).toBeNull();
  });

  it("fails closed without both approved policy fields", () => {
    expect(resolveApprovedPolicy(undefined, undefined)).toBeNull();
    expect(resolveApprovedPolicy("approved-v1", " ")).toBeNull();
    expect(resolveApprovedPolicy(" ", "approved scope")).toBeNull();
    expect(resolveApprovedPolicy(" approved-v1 ", " approved scope ")).toEqual({
      version: "approved-v1", text: "approved scope",
    });
  });

  it("sends the invite secret only in the accept body with matching idempotency values", async () => {
    const requests: Request[] = [];
    const client = clientFor("invite_accepted", requests);
    await acceptInvite(client, "synthetic-token", "approved-v1", "OTHER", requestId);
    expect(requests).toHaveLength(1);
    expect(new URL(requests[0]!.url).pathname).toBe("/v1/invites/accept");
    expect(requests[0]!.url).not.toContain("synthetic-token");
    expect(requests[0]!.headers.get("Idempotency-Key")).toBe(requestId);
    expect(await requests[0]!.json()).toMatchObject({ client_request_id: requestId, token: "synthetic-token",
      accept_shared_use: true, policy_version: "approved-v1", relationship: "OTHER" });
    await expect(acceptInvite(client, "synthetic-token", "", "OTHER", requestId)).rejects.toThrow();
    expect(requests).toHaveLength(1);
  });

  it("requires a bound proof for invite issuance and keeps the one-time link out of the request", async () => {
    const requests: Request[] = [];
    const issued = await createInvite(clientFor("invite_issued", requests), babyId,
      "synthetic@example.invalid", "synthetic-proof", requestId);
    expect(issued.invite.baby_id).toBe(babyId);
    expect(requests[0]!.headers.get("X-Reauthentication-Proof")).toBe("synthetic-proof");
    expect(requests[0]!.headers.get("Idempotency-Key")).toBe(requestId);
    expect(await requests[0]!.json()).toEqual({ client_request_id: requestId, email: "synthetic@example.invalid" });
    expect(requests[0]!.url).not.toContain("synthetic-proof");
  });

  it("binds a fresh challenge and proof to the agreed baby and action", async () => {
    const challengeRequests: Request[] = [];
    const challenge = await createReauthenticationChallenge(clientFor("reauthentication_challenge", challengeRequests),
      "CREATE_INVITE", babyId, requestId);
    expect(challengeRequests[0]!.headers.get("Idempotency-Key")).toBe(requestId);
    expect(await challengeRequests[0]!.json()).toMatchObject({ operation: "CREATE_INVITE", baby_id: babyId });
    const proofRequests: Request[] = [];
    const proof = await createReauthenticationProof(clientFor("reauthentication_proof", proofRequests), challenge, requestId);
    expect(proof).toBeTruthy();
    expect(await proofRequests[0]!.json()).toMatchObject({ challenge_id: challenge.challenge_id });
  });

  it("requires a proof for baby training grants and does not misreport failed provider revocation", async () => {
    const requests: Request[] = [];
    const client = clientFor("invite_accepted", requests);
    await expect(setBabyConsent(client, babyId, "BABY_TRAINING", true, "approved-v1", 0, requestId)).rejects.toThrow();
    expect(requests).toHaveLength(0);
    const revokeRequests: Request[] = [];
    const revokeClient = clientFor("session_revocation_provider_failed", revokeRequests);
    await expect(revokeSessions(revokeClient, "CURRENT", requestId)).rejects.toMatchObject({
      envelope: { code: "AUTH_PROVIDER_REVOCATION_FAILED" },
    });
    expect(await revokeRequests[0]!.json()).toMatchObject({ client_request_id: requestId, scope: "CURRENT" });
  });

  it("allows a former member to revoke only their own training participation with the current version", async () => {
    const requests: Request[] = [];
    const result = await setMyTrainingConsent(clientFor("consent_revoke_after_leave", requests), babyId,
      false, "fixture-policy-v1", 1, requestId);
    expect(result.status).toBe("REVOKED");
    expect(new URL(requests[0]!.url).pathname).toBe(`/v1/me/training-consents/${babyId}`);
    expect(await requests[0]!.json()).toEqual({
      client_request_id: requestId, granted: false, policy_version: "fixture-policy-v1", version: 1,
    });
  });

  it("clears private state after a provider revocation failure without claiming completion", async () => {
    const cache = new QueryClient();
    const scope = new PrivateScope(cache);
    scope.set("10000000-0000-4000-8000-000000000001", babyId);
    cache.setQueryData(["private", babyId], { private: true });
    const signOut = vi.fn(async () => ({ ok: true }));
    const outcome = await revokeAndSignOut(clientFor("session_revocation_provider_failed", [], scope),
      scope, signOut, "CURRENT");
    expect(outcome).toBe("provider-pending");
    expect(scope.snapshot()).toMatchObject({ userId: null, babyId: null });
    expect(cache.getQueryData(["private", babyId])).toBeUndefined();
    expect(signOut).toHaveBeenCalledOnce();
  });
});
