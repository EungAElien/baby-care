import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { createApiClient } from "../src/lib/api/client";
import { getChanges } from "../src/lib/api/changes";
import { PrivateScope } from "../src/lib/private-scope";

// A-08 ①: getChanges request shape and the contract self-checks (baby scope, revision
// direction) against the provided changes_* fixtures. B-09 handoff §요청·응답 예시.

type FixtureScenario = {
  name: string;
  response: { status: number; headers: Record<string, string>; body: unknown };
};

const fixtureFile = resolve(process.cwd(), "../../contracts/목 응답과 시험 사용자 배치.json");
const fixtures = JSON.parse(readFileSync(fixtureFile, "utf8")) as { scenarios: FixtureScenario[] };

function fixture(name: string): FixtureScenario {
  const found = fixtures.scenarios.find((scenario) => scenario.name === name);
  if (!found) throw new Error(`Missing contract fixture: ${name}`);
  return found;
}

function fixtureResponse(name: string): Response {
  const { response } = fixture(name);
  return Response.json(response.body, { status: response.status, headers: response.headers });
}

const babyId = (fixture("changes_initial_resync").response.body as { baby_id: string }).baby_id;

function setup(fetchImpl: typeof fetch) {
  const queryClient = new QueryClient();
  const scope = new PrivateScope(queryClient);
  scope.set("10000000-0000-4000-8000-000000000001", babyId);
  const client = createApiClient({
    baseUrl: "https://api.example.invalid/v1",
    auth: {
      getSession: vi.fn(async () => ({ userId: "10000000-0000-4000-8000-000000000001", accessToken: "token" })),
      refreshSession: vi.fn(async () => null),
    },
    scope,
    fetchImpl,
  });
  return { client };
}

describe("A-08 ① getChanges", () => {
  it("bootstraps with since_revision=0 and reports resync_required", async () => {
    const requests: Request[] = [];
    const { client } = setup(vi.fn(async (request: Request) => {
      requests.push(request);
      return fixtureResponse("changes_initial_resync");
    }) as unknown as typeof fetch);
    const result = await getChanges(client, babyId, 0);
    expect(result.resync_required).toBe(true);
    expect(result.changes).toEqual([]);
    expect(result.current_revision).toBe(8);
    const url = new URL(requests[0]!.url);
    expect(url.pathname).toBe(`/v1/babies/${babyId}/changes`);
    expect(url.searchParams.get("since_revision")).toBe("0");
  });

  it("returns a coalesced incremental range", async () => {
    const { client } = setup(vi.fn(async () => fixtureResponse("changes_incremental")) as unknown as typeof fetch);
    const result = await getChanges(client, babyId, 8);
    expect(result.resync_required).toBe(false);
    expect(result.current_revision).toBe(10);
    expect(result.changes).toEqual([
      { resource_type: "CARE_EVENT", resource_id: "10000000-0000-4000-8000-000000000601", version: 3, deleted: false },
      { resource_type: "BABY", resource_id: babyId, version: 8, deleted: false },
      { resource_type: "MEMBERSHIP", resource_id: "10000000-0000-4000-8000-000000000204", version: 1, deleted: false },
    ]);
  });

  it("returns an empty range when nothing changed", async () => {
    const { client } = setup(vi.fn(async () => fixtureResponse("changes_empty")) as unknown as typeof fetch);
    const result = await getChanges(client, babyId, 10);
    expect(result.changes).toEqual([]);
    expect(result.resync_required).toBe(false);
    expect(result.current_revision).toBe(10);
  });

  it("reports a tombstone with a higher version than the prior state", async () => {
    const { client } = setup(vi.fn(async () => fixtureResponse("changes_deleted")) as unknown as typeof fetch);
    const result = await getChanges(client, babyId, 10);
    expect(result.changes).toEqual([
      { resource_type: "CARE_EVENT", resource_id: "10000000-0000-4000-8000-000000000601", version: 4, deleted: true },
      { resource_type: "BABY", resource_id: babyId, version: 9, deleted: false },
    ]);
  });

  it("signals a full resync on a retention gap or the 500-resource cap without partial data", async () => {
    const { client } = setup(vi.fn(async () => fixtureResponse("changes_history_or_limit_resync")) as unknown as typeof fetch);
    const result = await getChanges(client, babyId, 1);
    expect(result.resync_required).toBe(true);
    expect(result.changes).toEqual([]);
  });

  it("rejects a response for a different baby", async () => {
    const { client } = setup(vi.fn(async () =>
      Response.json({ ...(fixture("changes_empty").response.body as object), baby_id: "10000000-0000-4000-8000-000000000102" })
    ) as unknown as typeof fetch);
    await expect(getChanges(client, babyId, 10)).rejects.toThrow("does not match the requested baby");
  });

  it("rejects a revision that moved backward without signaling resync", async () => {
    const { client } = setup(vi.fn(async () =>
      Response.json({ ...(fixture("changes_empty").response.body as object), current_revision: 3 })
    ) as unknown as typeof fetch);
    await expect(getChanges(client, babyId, 10)).rejects.toThrow("moved backward");
  });

  it("rejects a negative or non-integer since_revision before sending a request", async () => {
    const requests: Request[] = [];
    const { client } = setup(vi.fn(async (request: Request) => {
      requests.push(request);
      return fixtureResponse("changes_empty");
    }) as unknown as typeof fetch);
    await expect(getChanges(client, babyId, -1)).rejects.toThrow("non-negative integer");
    await expect(getChanges(client, babyId, 1.5)).rejects.toThrow("non-negative integer");
    expect(requests).toHaveLength(0);
  });
});
