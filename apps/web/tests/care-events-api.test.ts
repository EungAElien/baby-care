import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { createApiClient, NetworkRequestError, newClientRequestId } from "../src/lib/api/client";
import { createCareEvent, getCareEvent, getTimelinePage } from "../src/lib/api/care-events";
import type { components } from "../src/lib/api/generated";
import { PrivateScope } from "../src/lib/private-scope";

type Scenario = Readonly<{
  name: string;
  response: Readonly<{ status: number; headers: Record<string, string>; body: unknown }>;
}>;
const fixtureFile = resolve(process.cwd(), "../../contracts/목 응답과 시험 사용자 배치.json");
const fixtures = JSON.parse(readFileSync(fixtureFile, "utf8")) as { scenarios: Scenario[] };
function fixture(name: string): Scenario {
  const found = fixtures.scenarios.find((scenario) => scenario.name === name);
  if (!found) throw new Error(`Missing fixture: ${name}`);
  return found;
}
function fixtureResponse(name: string): Response {
  const response = fixture(name).response;
  return Response.json(response.body, { status: response.status, headers: response.headers });
}

const saved = fixture("care_event_saved").response.body as components["schemas"]["CareEvent"];
const babyId = saved.baby_id;
const otherBabyId = "10000000-0000-4000-8000-000000000102";

function setup(fetchImpl: typeof fetch) {
  const scope = new PrivateScope(new QueryClient());
  scope.set("10000000-0000-4000-8000-000000000002", babyId);
  const client = createApiClient({
    baseUrl: "https://api.example.invalid/v1",
    auth: {
      getSession: async () => ({ userId: scope.snapshot().userId!, accessToken: "synthetic-token" }),
      refreshSession: async () => null,
    },
    scope,
    fetchImpl,
  });
  return { client, scope };
}

describe("A-04 ① CareEvent API contract", () => {
  it("creates a record under the explicit baby with a matching UUID key and body", async () => {
    const requests: Request[] = [];
    const { client } = setup(vi.fn(async (request: Request) => {
      requests.push(request);
      return fixtureResponse("care_event_saved");
    }) as unknown as typeof fetch);
    const clientRequestId = newClientRequestId();
    const event = await createCareEvent(client, { babyId, clientRequestId, event: saved.event });

    expect(event).toEqual(saved);
    expect(requests).toHaveLength(1);
    expect(requests[0]!.method).toBe("POST");
    expect(new URL(requests[0]!.url).pathname).toBe(`/v1/babies/${babyId}/care-events`);
    expect(requests[0]!.headers.get("Idempotency-Key")).toBe(clientRequestId);
    expect(await requests[0]!.json()).toEqual({ client_request_id: clientRequestId, event: saved.event });
  });

  it("reuses exactly the same logical request after an unknown network outcome", async () => {
    const requests: Request[] = [];
    let attempts = 0;
    const { client } = setup(vi.fn(async (request: Request) => {
      requests.push(request);
      attempts += 1;
      if (attempts === 1) throw new TypeError("synthetic network disconnect");
      return fixtureResponse("care_event_saved");
    }) as unknown as typeof fetch);
    const logicalRequest = { babyId, clientRequestId: newClientRequestId(), event: saved.event };

    await expect(createCareEvent(client, logicalRequest)).rejects.toBeInstanceOf(NetworkRequestError);
    await expect(createCareEvent(client, logicalRequest)).resolves.toEqual(saved);
    expect(requests).toHaveLength(2);
    expect(requests[0]!.headers.get("Idempotency-Key")).toBe(requests[1]!.headers.get("Idempotency-Key"));
    expect(await requests[0]!.json()).toEqual(await requests[1]!.json());
  });

  it("reads a record by id and rejects a response from another baby", async () => {
    const { client } = setup(vi.fn(async () => fixtureResponse("care_event_saved")) as unknown as typeof fetch);
    await expect(getCareEvent(client, babyId, saved.care_event_id)).resolves.toEqual(saved);
    await expect(getCareEvent(client, otherBabyId, saved.care_event_id)).rejects.toThrow("does not belong");
  });

  it("passes the opaque cursor unchanged and keeps the server's item order", async () => {
    const requests: Request[] = [];
    const page: components["schemas"]["TimelineItemPage"] = {
      items: [{
        kind: "CARE_EVENT",
        resource_id: saved.care_event_id,
        baby_id: babyId,
        occurred_at: saved.event.occurred_at,
        version: saved.version,
        created_by_user_id: saved.created_by_user_id,
        data_origin: saved.data_origin,
        resource: saved,
      }],
      next_cursor: "opaque-next-token",
    };
    const { client } = setup(vi.fn(async (request: Request) => {
      requests.push(request);
      return Response.json(page);
    }) as unknown as typeof fetch);

    await expect(getTimelinePage(client, babyId, "opaque-current-token")).resolves.toEqual(page);
    expect(new URL(requests[0]!.url).searchParams.get("cursor")).toBe("opaque-current-token");
  });

  it("does not render a timeline response containing another baby's record", async () => {
    const page: components["schemas"]["TimelineItemPage"] = {
      items: [{
        kind: "CARE_EVENT",
        resource_id: saved.care_event_id,
        baby_id: otherBabyId,
        occurred_at: saved.event.occurred_at,
        version: saved.version,
        created_by_user_id: saved.created_by_user_id,
        data_origin: saved.data_origin,
        resource: saved,
      }],
      next_cursor: null,
    };
    const { client } = setup(vi.fn(async () => Response.json(page)) as unknown as typeof fetch);
    await expect(getTimelinePage(client, babyId)).rejects.toThrow("does not belong");
  });

  it("keeps the contract's 404 and active-sleep 409 distinct", async () => {
    const notMember = setup(vi.fn(async () => fixtureResponse("not_member")) as unknown as typeof fetch);
    await expect(getCareEvent(notMember.client, babyId, saved.care_event_id)).rejects.toMatchObject({
      status: 404, envelope: { code: "RESOURCE_NOT_FOUND" },
    });

    const sleep = setup(vi.fn(async () => fixtureResponse("sleep_conflict")) as unknown as typeof fetch);
    await expect(createCareEvent(sleep.client, {
      babyId, clientRequestId: newClientRequestId(), event: {
        type: "SLEEP", occurred_at: "2026-09-19T09:00:00Z", ended_at: null, time_precision: "EXACT", payload: {},
      },
    })).rejects.toMatchObject({ status: 409, envelope: { code: "SLEEP_ALREADY_ACTIVE" } });
  });
});
