import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { createApiClient, newClientRequestId, idempotencyHeaders } from "../src/lib/api/client";
import { requireData } from "../src/lib/api/errors";
import { PrivateScope } from "../src/lib/private-scope";
import type { components } from "../src/lib/api/generated";

// A-03 실제 인증+아기 기반: listBabies/createBaby/getActiveBaby/setActiveBaby
// 요청이 계약과 제공된 fixture에 맞는지 확인한다. fixture에 없는 조합은
// 지어내지 않고 요청 형태(메서드·헤더·경로)만 검증한다.

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

function setup(fetchImpl: typeof fetch) {
  const queryClient = new QueryClient();
  const scope = new PrivateScope(queryClient);
  scope.set("user-a", null);
  const auth = {
    getSession: vi.fn(async () => ({ userId: "user-a", accessToken: "token" })),
    refreshSession: vi.fn(async () => null),
  };
  return createApiClient({ baseUrl: "https://api.example.invalid/v1", auth, scope, fetchImpl });
}

describe("A-03 real baby foundation requests", () => {
  it("creates a baby with a matching Idempotency-Key/client_request_id and returns the fixture's BabyAccess", async () => {
    const requests: Request[] = [];
    const fetchImpl = vi.fn(async (request: Request) => {
      requests.push(request);
      return fixtureResponse("baby_created");
    }) as unknown as typeof fetch;
    const client = setup(fetchImpl);
    const clientRequestId = newClientRequestId();

    const body: components["schemas"]["CreateBaby"] = {
      client_request_id: clientRequestId,
      alias: "예시 아기 A",
      birth_date: "2026-07-01",
      feeding_mode: "MIXED",
      timezone: "Asia/Seoul",
    };
    const result = requireData(
      await client.POST("/babies", { params: { header: idempotencyHeaders(clientRequestId) }, body }),
    );

    expect(requests).toHaveLength(1);
    expect(requests[0]!.method).toBe("POST");
    expect(new URL(requests[0]!.url).pathname).toBe("/v1/babies");
    expect(requests[0]!.headers.get("Idempotency-Key")).toBe(clientRequestId);
    expect(result).toEqual(fixture("baby_created").response.body);
  });

  it("reads the active-baby preference with a plain GET and no body", async () => {
    const requests: Request[] = [];
    const fetchImpl = vi.fn(async (request: Request) => {
      requests.push(request);
      return fixtureResponse("active_baby_lost_membership");
    }) as unknown as typeof fetch;
    const client = setup(fetchImpl);

    const result = requireData(await client.GET("/babies/current"));

    expect(requests).toHaveLength(1);
    expect(requests[0]!.method).toBe("GET");
    expect(new URL(requests[0]!.url).pathname).toBe("/v1/babies/current");
    expect(requests[0]!.body).toBeNull();
    expect(result).toEqual({ baby_id: null });
  });
});
