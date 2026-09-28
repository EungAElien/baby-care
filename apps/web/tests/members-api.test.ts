import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { createApiClient, newClientRequestId, idempotencyHeaders } from "../src/lib/api/client";
import { requireData } from "../src/lib/api/errors";
import { PrivateScope } from "../src/lib/private-scope";
import type { components } from "../src/lib/api/generated";

// A-03 실제 연동: 구성원 제거/초대 취소 요청 형태를 계약 fixture로 확인한다.
// listMembers/listInvites의 성공 목록 응답은 제공된 fixture 중 정확히
// 일치하는 예시가 없어(개별 항목 스키마만 있음) 여기서는 다루지 않는다 —
// 지어낸 목록으로 형태를 검증하지 않는다.

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
  scope.set("user-a", "baby-a");
  const auth = {
    getSession: vi.fn(async () => ({ userId: "user-a", accessToken: "token" })),
    refreshSession: vi.fn(async () => null),
  };
  return createApiClient({ baseUrl: "https://api.example.invalid/v1", auth, scope, fetchImpl });
}

describe("A-03 real member/invite requests", () => {
  it("removes a membership with the version query and matching Idempotency-Key", async () => {
    const requests: Request[] = [];
    // member_left is genuinely Membership-shaped (contract's own removeMembership success fixture).
    const fetchImpl = vi.fn(async (request: Request) => {
      requests.push(request);
      return fixtureResponse("member_left");
    }) as unknown as typeof fetch;
    const client = setup(fetchImpl);
    const clientRequestId = newClientRequestId();

    const result = requireData(
      await client.DELETE("/babies/{baby_id}/members/{user_id}", {
        params: {
          path: { baby_id: "10000000-0000-4000-8000-000000000101", user_id: "10000000-0000-4000-8000-000000000002" },
          query: { version: 1 },
          header: idempotencyHeaders(clientRequestId),
        },
      }),
    );

    expect(requests).toHaveLength(1);
    expect(requests[0]!.method).toBe("DELETE");
    const url = new URL(requests[0]!.url);
    expect(url.pathname).toBe(
      "/v1/babies/10000000-0000-4000-8000-000000000101/members/10000000-0000-4000-8000-000000000002",
    );
    expect(url.searchParams.get("version")).toBe("1");
    expect(requests[0]!.headers.get("Idempotency-Key")).toBe(clientRequestId);
    expect(requests[0]!.body).toBeNull();
    expect(result).toEqual(fixture("member_left").response.body);
  });

  it("revokes a pending invite with the version query, reusing the invite_issued fixture's own Invite", async () => {
    const requests: Request[] = [];
    const issuedInvite = fixture("invite_issued").response.body as { invite: components["schemas"]["Invite"] };
    const fetchImpl = vi.fn(async (request: Request) => {
      requests.push(request);
      return Response.json(issuedInvite.invite, { status: 200 });
    }) as unknown as typeof fetch;
    const client = setup(fetchImpl);
    const clientRequestId = newClientRequestId();

    const result = requireData(
      await client.DELETE("/invites/{invite_id}", {
        params: {
          path: { invite_id: issuedInvite.invite.invite_id },
          query: { version: issuedInvite.invite.version },
          header: idempotencyHeaders(clientRequestId),
        },
      }),
    );

    expect(requests[0]!.method).toBe("DELETE");
    expect(new URL(requests[0]!.url).pathname).toBe(`/v1/invites/${issuedInvite.invite.invite_id}`);
    expect(result).toEqual(issuedInvite.invite);
  });
});
