import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { createApiClient, ContractRequestError, AuthenticationBoundaryError, NetworkRequestError, newClientRequestId } from "../src/lib/api/client";
import { classifyErrorCode, ContractApiError, requireData } from "../src/lib/api/errors";
import type { components } from "../src/lib/api/generated";
import { PrivateScope, ScopeChangedError } from "../src/lib/private-scope";

type FixtureScenario = {
  name: string;
  request?: { headers: { "Idempotency-Key": string }; body: Record<string, unknown> };
  response: { status: number; headers: Record<string, string>; body: unknown };
};

const fixtureFile = resolve(process.cwd(), "../../contracts/목 응답과 시험 사용자 배치.json");
const fixtures = JSON.parse(readFileSync(fixtureFile, "utf8")) as { scenarios: FixtureScenario[] };
const openApiFile = resolve(process.cwd(), "../../contracts/openapi계약.json");
const contract = JSON.parse(readFileSync(openApiFile, "utf8")) as {
  components: { schemas: { ErrorCode: { enum: string[] } } };
};

function fixture(name: string): FixtureScenario {
  const found = fixtures.scenarios.find((scenario) => scenario.name === name);
  if (!found) throw new Error(`Missing contract fixture: ${name}`);
  return found;
}

function fixtureResponse(name: string): Response {
  const { response } = fixture(name);
  return Response.json(response.body, { status: response.status, headers: response.headers });
}

function setup(fetchImpl: typeof fetch, refreshSession = vi.fn(async () => ({ userId: "user-a", accessToken: "new-token" }))) {
  const queryClient = new QueryClient();
  const scope = new PrivateScope(queryClient);
  scope.set("user-a", "baby-a");
  const auth = {
    getSession: vi.fn(async () => ({ userId: "user-a", accessToken: "old-token" })),
    refreshSession,
  };
  const client = createApiClient({ baseUrl: "https://api.example.invalid/v1", auth, scope, fetchImpl });
  return { client, auth, scope, queryClient };
}

describe("OpenAPI client with provided synthetic fixtures", () => {
  it("creates a UUID for each new logical mutation", () => {
    expect(newClientRequestId()).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
    expect(newClientRequestId()).not.toBe(newClientRequestId());
  });

  it("cannot be instantiated for server-side private fetching without an explicit test transport", () => {
    const scope = new PrivateScope(new QueryClient());
    expect(() => createApiClient({
      baseUrl: "https://api.example.invalid/v1",
      scope,
      auth: { getSession: async () => null, refreshSession: async () => null },
    })).toThrow(ContractRequestError);
  });

  it("sends user Bearer, exact idempotency key, and no-store through the typed client", async () => {
    const recorded: Request[] = [];
    const fetchImpl = vi.fn(async (request: Request) => {
      recorded.push(request);
      return fixtureResponse("baby_created");
    }) as unknown as typeof fetch;
    const { client } = setup(fetchImpl);
    const example = fixture("baby_created").request!;
    const result = await client.POST("/babies", {
      params: { header: example.headers },
      body: example.body as components["schemas"]["CreateBaby"],
    });

    expect(requireData(result)).toEqual(fixture("baby_created").response.body);
    expect(recorded).toHaveLength(1);
    expect(recorded[0]!.headers.get("Authorization")).toBe("Bearer old-token");
    expect(recorded[0]!.headers.get("Idempotency-Key")).toBe(example.body.client_request_id);
    expect(recorded[0]!.cache).toBe("no-store");
  });

  it("rejects missing or mismatched keys before any network call", async () => {
    const fetchImpl = vi.fn(async () => fixtureResponse("baby_created")) as unknown as typeof fetch;
    const { client } = setup(fetchImpl);
    const example = fixture("baby_created").request!;
    await expect(client.POST("/babies", {
      params: { header: { "Idempotency-Key": "" } },
      body: example.body as components["schemas"]["CreateBaby"],
    })).rejects.toBeInstanceOf(ContractRequestError);
    await expect(client.POST("/babies", {
      params: { header: { "Idempotency-Key": "10000000-0000-4000-8000-000000000999" } },
      body: example.body as components["schemas"]["CreateBaby"],
    })).rejects.toBeInstanceOf(ContractRequestError);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("uses the contract's header and version query for DELETE, with no request body", async () => {
    const requests: Request[] = [];
    const fetchImpl = vi.fn(async (request: Request) => {
      requests.push(request);
      return fixtureResponse("deletion_accepted");
    }) as unknown as typeof fetch;
    const { client } = setup(fetchImpl);
    const key = fixture("baby_created").request!.headers["Idempotency-Key"];
    const result = await client.DELETE("/babies/{baby_id}/data", {
      params: {
        path: { baby_id: "10000000-0000-4000-8000-000000000101" },
        query: { version: 1, confirm: "DELETE_BABY" },
        header: { "Idempotency-Key": key, "X-Reauthentication-Proof": "test-reauth-proof" },
      },
    });
    expect(requireData(result)).toEqual(fixture("deletion_accepted").response.body);
    expect(requests[0]!.headers.get("Idempotency-Key")).toBe(key);
    expect(new URL(requests[0]!.url).searchParams.get("version")).toBe("1");
    expect(requests[0]!.body).toBeNull();
  });

  it("refreshes once after 401 and replays the identical mutation/key for the same user", async () => {
    const requests: Request[] = [];
    const fetchImpl = vi.fn(async (request: Request) => {
      requests.push(request);
      return requests.length === 1 ? fixtureResponse("auth_expired") : fixtureResponse("baby_created");
    }) as unknown as typeof fetch;
    const { client, auth } = setup(fetchImpl);
    const example = fixture("baby_created").request!;
    const result = await client.POST("/babies", {
      params: { header: example.headers },
      body: example.body as components["schemas"]["CreateBaby"],
    });
    expect(requireData(result)).toEqual(fixture("baby_created").response.body);
    expect(auth.refreshSession).toHaveBeenCalledTimes(1);
    expect(requests).toHaveLength(2);
    expect(requests[0]!.headers.get("Idempotency-Key")).toBe(requests[1]!.headers.get("Idempotency-Key"));
    expect(await requests[0]!.text()).toBe(await requests[1]!.text());
    expect(requests[1]!.headers.get("Authorization")).toBe("Bearer new-token");
  });

  it("stops after one unsuccessful refresh and clears private state", async () => {
    const fetchImpl = vi.fn(async () => fixtureResponse("auth_expired")) as unknown as typeof fetch;
    const { client, scope, auth } = setup(fetchImpl);
    const result = await client.GET("/analyses/{analysis_id}", {
      params: { path: { analysis_id: "10000000-0000-4000-8000-000000000501" } },
    });
    expect(() => requireData(result)).toThrow(ContractApiError);
    expect(auth.refreshSession).toHaveBeenCalledTimes(1);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(scope.snapshot().userId).toBeNull();
  });

  it("never replays an old user's request after account change", async () => {
    const fetchImpl = vi.fn(async () => fixtureResponse("auth_expired")) as unknown as typeof fetch;
    const { client, scope } = setup(fetchImpl, vi.fn(async () => ({ userId: "user-b", accessToken: "other-token" })));
    await expect(client.GET("/analyses/{analysis_id}", {
      params: { path: { analysis_id: "10000000-0000-4000-8000-000000000501" } },
    })).rejects.toBeInstanceOf(AuthenticationBoundaryError);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(scope.snapshot().userId).toBeNull();
  });

  it("keeps a network-uncertain mutation as unknown; caller can explicitly retry the same key", async () => {
    const requests: Request[] = [];
    const fetchImpl = vi.fn(async (request: Request) => {
      requests.push(request);
      if (requests.length === 1) throw new TypeError("offline");
      return fixtureResponse("baby_created");
    }) as unknown as typeof fetch;
    const { client } = setup(fetchImpl);
    const example = fixture("baby_created").request!;
    const options = { params: { header: example.headers }, body: example.body as components["schemas"]["CreateBaby"] };
    await expect(client.POST("/babies", options)).rejects.toBeInstanceOf(NetworkRequestError);
    expect(requests).toHaveLength(1);
    expect(requireData(await client.POST("/babies", options))).toEqual(fixture("baby_created").response.body);
    expect(requests[1]!.headers.get("Idempotency-Key")).toBe(requests[0]!.headers.get("Idempotency-Key"));
  });

  it("discards a late response after baby switch", async () => {
    let finish!: (response: Response) => void;
    const fetchImpl = vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })) as unknown as typeof fetch;
    const { client, scope } = setup(fetchImpl);
    const pending = client.GET("/babies/current");
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1));
    scope.set("user-a", "baby-b");
    finish(fixtureResponse("active_baby_lost_membership"));
    await expect(pending).rejects.toBeInstanceOf(ScopeChangedError);
  });

  it("classifies contract error codes and never treats fixture failures as mock success", async () => {
    const fetchImpl = vi.fn(async () => fixtureResponse("model_unavailable")) as unknown as typeof fetch;
    const { client } = setup(fetchImpl);
    const example = fixture("baby_created").request!;
    // Use the response fixture through the same transport; no fabricated Analysis value is returned.
    const result = await client.POST("/babies", {
      params: { header: example.headers },
      body: example.body as components["schemas"]["CreateBaby"],
    });
    expect(() => requireData(result)).toThrow(ContractApiError);
    expect(classifyErrorCode("MODEL_NOT_READY")).toBe("service");
    expect(classifyErrorCode("OWNER_ONLY")).toBe("permission");
    expect(classifyErrorCode("RESOURCE_NOT_FOUND")).toBe("not-found");
    expect(classifyErrorCode("VERSION_CONFLICT")).toBe("version-conflict");
    expect(classifyErrorCode("IDEMPOTENCY_KEY_REUSED")).toBe("idempotency-conflict");
    expect(classifyErrorCode("INVITE_EXPIRED")).toBe("gone");
    expect(classifyErrorCode("RATE_LIMITED")).toBe("rate-limit");
    expect(contract.components.schemas.ErrorCode.enum.filter((code) => classifyErrorCode(code) === "unknown")).toEqual([]);
  });
});
