import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { createApiClient, NetworkRequestError, newClientRequestId } from "../src/lib/api/client";
import {
  deleteCareEvent, getCareEventAfterConflict, getCareEventDeletion, linkExistingCareEvent,
  patchCareEvent, retryCareEventDeletion,
} from "../src/lib/api/care-events";
import type { ActionAttempt, DeletionJob } from "../src/lib/api/care-events";
import type { components } from "../src/lib/api/generated";
import { PrivateScope } from "../src/lib/private-scope";

type Scenario = Readonly<{
  name: string;
  response: Readonly<{ status: number; headers: Record<string, string>; body: unknown }>;
}>;
const fixtureFile = resolve(process.cwd(), "../../contracts/목 응답과 시험 사용자 배치.json");
const fixtures = JSON.parse(readFileSync(fixtureFile, "utf8")) as { scenarios: Scenario[] };
function fixtureResponse(name: string): Response {
  const found = fixtures.scenarios.find((scenario) => scenario.name === name);
  if (!found) throw new Error(`Missing fixture: ${name}`);
  return Response.json(found.response.body, { status: found.response.status, headers: found.response.headers });
}

const saved = fixtures.scenarios.find((scenario) => scenario.name === "care_event_saved")!
  .response.body as components["schemas"]["CareEvent"];
const babyId = saved.baby_id;
const userId = saved.created_by_user_id;
const episodeId = "10000000-0000-4000-8000-000000000301";
const deletionJobId = "10000000-0000-4000-8000-000000000841";
const deletionJob: DeletionJob = {
  deletion_job_id: deletionJobId,
  requester_user_id: userId,
  baby_id: babyId,
  scope: "CARE_EVENT",
  resource_id: saved.care_event_id,
  status: "PENDING",
  access_blocked: true,
  requested_at: "2026-09-19T09:00:00Z",
  completed_at: null,
  failure: null,
  pending_categories: ["RECORDS"],
  attempt_no: 1,
};
const action: ActionAttempt = {
  action_id: "10000000-0000-4000-8000-000000000602",
  baby_id: babyId,
  episode_id: episodeId,
  care_event_id: saved.care_event_id,
  recommendation_id: null,
  created_by_user_id: userId,
  performed_by_user_id: null,
  performed_at: saved.event.occurred_at,
  sequence: 1,
  status: "ACTIVE",
  followup_status: "PENDING",
  data_origin: "DEMO",
  version: 1,
  recorded_at: "2026-09-19T09:00:00Z",
  updated_at: "2026-09-19T09:00:00Z",
};

function setup(fetchImpl: typeof fetch) {
  const scope = new PrivateScope(new QueryClient());
  scope.set(userId, babyId);
  const client = createApiClient({
    baseUrl: "https://api.example.invalid/v1",
    auth: {
      getSession: async () => ({ userId, accessToken: "synthetic-token" }),
      refreshSession: async () => null,
    },
    scope,
    fetchImpl,
  });
  return { client, scope };
}

describe("A-04 ② CareEvent mutations", () => {
  it("PATCH sends the read version and matching idempotency key without changing the author", async () => {
    const requests: Request[] = [];
    const updated = { ...saved, version: 2, updated_by_user_id: "10000000-0000-4000-8000-000000000001" };
    const { client } = setup(vi.fn(async (request: Request) => {
      requests.push(request);
      return Response.json(updated);
    }) as unknown as typeof fetch);
    const clientRequestId = newClientRequestId();
    await expect(patchCareEvent(client, {
      babyId, careEventId: saved.care_event_id, version: saved.version, clientRequestId, event: saved.event,
    })).resolves.toEqual(updated);
    expect(requests).toHaveLength(1);
    expect(requests[0]!.method).toBe("PATCH");
    expect(new URL(requests[0]!.url).pathname).toBe(`/v1/care-events/${saved.care_event_id}`);
    expect(requests[0]!.headers.get("Idempotency-Key")).toBe(clientRequestId);
    expect(await requests[0]!.json()).toEqual({
      client_request_id: clientRequestId, version: saved.version, event: saved.event,
    });
  });

  it("never displays the 409 embedded resource without an authorized GET", async () => {
    const requests: Request[] = [];
    const { client } = setup(vi.fn(async (request: Request) => {
      requests.push(request);
      return request.method === "PATCH" ? fixtureResponse("edit_conflict") : fixtureResponse("not_member");
    }) as unknown as typeof fetch);
    let conflict: unknown;
    try {
      await patchCareEvent(client, {
        babyId, careEventId: saved.care_event_id, version: saved.version,
        clientRequestId: newClientRequestId(), event: saved.event,
      });
    } catch (error) {
      conflict = error;
    }
    expect(conflict).toMatchObject({ status: 409, envelope: { code: "VERSION_CONFLICT" } });
    await expect(getCareEventAfterConflict(client, babyId, saved.care_event_id, conflict)).rejects.toMatchObject({
      status: 404, envelope: { code: "RESOURCE_NOT_FOUND" },
    });
    expect(requests.map((request) => request.method)).toEqual(["PATCH", "GET"]);
  });

  it("DELETE uses a version query, has no body, and treats 202 as a job", async () => {
    const requests: Request[] = [];
    const { client } = setup(vi.fn(async (request: Request) => {
      requests.push(request);
      return Response.json(deletionJob, { status: 202 });
    }) as unknown as typeof fetch);
    const clientRequestId = newClientRequestId();
    await expect(deleteCareEvent(client, {
      babyId, careEventId: saved.care_event_id, version: saved.version, clientRequestId,
    })).resolves.toEqual(deletionJob);
    expect(requests[0]!.method).toBe("DELETE");
    expect(new URL(requests[0]!.url).searchParams.get("version")).toBe(String(saved.version));
    expect(requests[0]!.headers.get("Idempotency-Key")).toBe(clientRequestId);
    expect(requests[0]!.body).toBeNull();
  });

  it("recovers an uncertain DELETE with exactly the same key and version", async () => {
    const requests: Request[] = [];
    const { client } = setup(vi.fn(async (request: Request) => {
      requests.push(request);
      if (requests.length === 1) throw new TypeError("synthetic disconnect");
      return Response.json(deletionJob, { status: 202 });
    }) as unknown as typeof fetch);
    const logicalRequest = {
      babyId, careEventId: saved.care_event_id, version: saved.version, clientRequestId: newClientRequestId(),
    };
    await expect(deleteCareEvent(client, logicalRequest)).rejects.toBeInstanceOf(NetworkRequestError);
    await expect(deleteCareEvent(client, logicalRequest)).resolves.toEqual(deletionJob);
    expect(requests[0]!.url).toBe(requests[1]!.url);
    expect(requests[0]!.headers.get("Idempotency-Key")).toBe(requests[1]!.headers.get("Idempotency-Key"));
  });

  it("reads only the requester's CareEvent job and retries its failed attempt", async () => {
    const requests: Request[] = [];
    const failed = { ...deletionJob, status: "FAILED" as const };
    const { client } = setup(vi.fn(async (request: Request) => {
      requests.push(request);
      return Response.json(request.method === "GET" ? failed : { ...failed, status: "RUNNING" });
    }) as unknown as typeof fetch);
    await expect(getCareEventDeletion(client, deletionJobId, userId)).resolves.toEqual(failed);
    const clientRequestId = newClientRequestId();
    await expect(retryCareEventDeletion(client, deletionJobId, userId, 1, clientRequestId)).resolves.toMatchObject({ status: "RUNNING" });
    expect(new URL(requests[0]!.url).pathname).toBe(`/v1/deletions/${deletionJobId}`);
    expect(new URL(requests[1]!.url).pathname).toBe(`/v1/deletions/${deletionJobId}/retry`);
    expect(await requests[1]!.json()).toEqual({ client_request_id: clientRequestId, expected_attempt: 1 });
  });

  it("links an existing event without creating another care event", async () => {
    const requests: Request[] = [];
    const { client } = setup(vi.fn(async (request: Request) => {
      requests.push(request);
      return Response.json(action, { status: 201 });
    }) as unknown as typeof fetch);
    const clientRequestId = newClientRequestId();
    await expect(linkExistingCareEvent(client, {
      babyId, careEventId: saved.care_event_id, episodeId, clientRequestId, sequence: 1,
    })).resolves.toEqual(action);
    expect(new URL(requests[0]!.url).pathname).toBe(`/v1/episodes/${episodeId}/actions`);
    expect(await requests[0]!.json()).toEqual({
      client_request_id: clientRequestId, care_event_id: saved.care_event_id,
      new_care_event: null, recommendation_id: null, performed_by_user_id: null, sequence: 1,
    });
  });

  it("rejects a cross-baby action or deletion response", async () => {
    const otherBabyId = "10000000-0000-4000-8000-000000000102";
    const wrongAction = setup(vi.fn(async () => Response.json({ ...action, baby_id: otherBabyId }, { status: 201 })) as unknown as typeof fetch);
    await expect(linkExistingCareEvent(wrongAction.client, {
      babyId, careEventId: saved.care_event_id, episodeId,
      clientRequestId: newClientRequestId(), sequence: 1,
    })).rejects.toThrow("does not match");
    const wrongJob = setup(vi.fn(async () => Response.json({ ...deletionJob, baby_id: otherBabyId }, { status: 202 })) as unknown as typeof fetch);
    await expect(deleteCareEvent(wrongJob.client, {
      babyId, careEventId: saved.care_event_id, version: saved.version, clientRequestId: newClientRequestId(),
    })).rejects.toThrow("does not belong");
  });
});
