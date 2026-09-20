import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { careEntriesApi, type Entry, type Run } from "../src/lib/api/care-entries";
import { createApiClient } from "../src/lib/api/client";
import { PrivateScope } from "../src/lib/private-scope";

const fixtures = JSON.parse(readFileSync(resolve(process.cwd(), "../../contracts/목 응답과 시험 사용자 배치.json"), "utf8")) as { scenarios: { name: string; response: { body: unknown } }[] };
const entry = fixtures.scenarios.find((item) => item.name === "draft_private")!.response.body as Entry;
const run = fixtures.scenarios.find((item) => item.name === "normalization_review")!.response.body as Run;
function setup(response: unknown, userId = entry.author_user_id) {
  const requests: Request[] = [];
  const scope = new PrivateScope(new QueryClient()); scope.set(userId, entry.baby_id);
  const client = createApiClient({ baseUrl: "https://api.example.invalid/v1", scope,
    auth: { getSession: async () => ({ userId, accessToken: "synthetic-token" }), refreshSession: async () => null },
    fetchImpl: async (input) => { const request = input as Request; requests.push(request.clone()); return Response.json(response); } });
  return { api: careEntriesApi(client, entry.baby_id, userId), requests, scope };
}
describe("A-07 API boundary", () => {
  it("creates only eventless drafts with identical UUID header/body", async () => {
    const { api, requests } = setup({ ...entry, episode_id: null });
    const body = { client_request_id: crypto.randomUUID(), episode_id: null, input_mode: "TEXT" as const, raw_text: "합성 👶 원문", choices: [], occurred_at: null, time_precision: "UNKNOWN" as const, supersedes_entry_id: null, base_record_versions: [] };
    await api.create(body);
    expect(new URL(requests[0]!.url).pathname).toBe(`/v1/babies/${entry.baby_id}/care-entries`);
    expect(requests[0]!.headers.get("Idempotency-Key")).toBe(body.client_request_id);
    expect(await requests[0]!.json()).toEqual(body);
    expect(requests[0]!.cache).toBe("no-store");
  });
  it("never exposes another author's private draft, including to OWNER", async () => {
    const { api } = setup({ ...entry, episode_id: null }, "10000000-0000-4000-8000-000000000001");
    const other = { ...entry, author_user_id: "10000000-0000-4000-8000-000000000999" };
    await expect(setup(other).api.get(entry.entry_id)).rejects.toThrow("scope mismatch");
    if (entry.author_user_id !== "10000000-0000-4000-8000-000000000001") await expect(api.get(entry.entry_id)).rejects.toThrow("scope mismatch");
    await expect(setup({ items: [other], next_cursor: null }).api.list()).rejects.toThrow("scope mismatch");
  });
  it("rejects another baby and filters event-linked own drafts from the list", async () => {
    await expect(setup({ ...entry, baby_id: "other" }).api.get(entry.entry_id)).rejects.toThrow();
    const { api } = setup({ items: [{ ...entry, episode_id: "event" }], next_cursor: "opaque" });
    expect(await api.list()).toEqual({ items: [], next_cursor: "opaque" });
  });
  it("recovers exactly the requested run and rejects mismatched response IDs", async () => {
    const { api, requests } = setup(run);
    await api.run(run.entry_id, run.run_id);
    expect(requests[0]!.method).toBe("GET");
    expect(new URL(requests[0]!.url).pathname).toBe(`/v1/normalizations/${run.run_id}`);
    await expect(api.run("other", run.run_id)).rejects.toThrow("scope mismatch");
  });
  it("confirmation replay is identical and never calls createCareEvent", async () => {
    const result = fixtures.scenarios.find((item) => item.name === "confirm_once")!.response.body;
    const { api, requests } = setup(result);
    const body = { client_request_id: crypto.randomUUID(), input_revision: 1, run_id: run.run_id, normalization_mode: "LLM" as const, content: run.result!, base_record_versions: [] };
    await api.confirm(run.entry_id, body); await api.confirm(run.entry_id, body);
    expect(await requests[0]!.json()).toEqual(await requests[1]!.json());
    expect(requests[0]!.headers.get("Idempotency-Key")).toBe(requests[1]!.headers.get("Idempotency-Key"));
    expect(requests.every((request) => new URL(request.url).pathname.endsWith("/confirm"))).toBe(true);
  });
});
