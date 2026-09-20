// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BabyProfile } from "../src/components/baby-profile";
import { PrivateScope } from "../src/lib/private-scope";
import { ContractApiError } from "../src/lib/api/errors";
import { NetworkRequestError } from "../src/lib/api/client";
import type { components } from "../src/lib/api/generated";

const client = { PATCH: vi.fn(), GET: vi.fn() };
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const scope = new PrivateScope(queryClient);
vi.mock("@/components/app-providers", () => ({ usePrivateScope: () => scope }));
vi.mock("@/lib/api/real-client", () => ({ useApiClient: () => client }));
const baby: components["schemas"]["Baby"] = {
  baby_id: "baby-a", owner_user_id: "user-a", alias: "아기 A", birth_date: "2026-07-01", feeding_mode: "MIXED",
  timezone: "Asia/Seoul", status: "ACTIVE", context_revision: 1, version: 1,
  recorded_at: "2026-09-20T01:00:00Z", updated_at: "2026-09-20T01:00:00Z",
};
const ok = (value: unknown) => ({ data: value, response: Response.json(value) });
function show(isOwner = true) {
  return render(<QueryClientProvider client={queryClient}><BabyProfile baby={baby} isOwner={isOwner} /></QueryClientProvider>);
}
async function openAndEdit() {
  fireEvent.click(screen.getByRole("button", { name: "프로필 수정" }));
  const name = await screen.findByRole("textbox", { name: "아기 이름" });
  fireEvent.change(name, { target: { value: "새 이름" } });
  return name.closest("form")!;
}
beforeEach(() => { client.PATCH.mockReset(); client.GET.mockReset(); scope.set("user-a", "baby-a"); });
afterEach(() => { cleanup(); queryClient.clear(); });

describe("baby profile editing", () => {
  it("shows shared profile values without an edit control to a caregiver", () => {
    show(false);
    expect(screen.getByText("아기 A")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "프로필 수정" })).toBeNull();
  });
  it("prevents duplicate submission while preserving the version and idempotency key", async () => {
    let finish!: (value: unknown) => void;
    client.PATCH.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    show();
    const form = await openAndEdit();
    fireEvent.submit(form); fireEvent.submit(form);
    expect(client.PATCH).toHaveBeenCalledOnce();
    const [path, request] = client.PATCH.mock.calls[0]!;
    expect(path).toBe("/babies/{baby_id}");
    expect(request.body).toMatchObject({ alias: "새 이름", version: 1 });
    expect(request.params.header["Idempotency-Key"]).toBe(request.body.client_request_id);
    await act(async () => finish(ok({ ...baby, alias: "새 이름", version: 2 })));
    expect(await screen.findByText("프로필을 저장했어요.")).toBeTruthy();
  });
  it("recovers an uncertain response with the identical request body", async () => {
    client.PATCH.mockRejectedValueOnce(new NetworkRequestError()).mockResolvedValueOnce(ok({ ...baby, alias: "새 이름", version: 2 }));
    show();
    fireEvent.submit(await openAndEdit());
    fireEvent.click(await screen.findByRole("button", { name: "같은 요청으로 결과 확인" }));
    await waitFor(() => expect(client.PATCH).toHaveBeenCalledTimes(2));
    expect(client.PATCH.mock.calls[0]![1]).toEqual(client.PATCH.mock.calls[1]![1]);
  });
  it("requires explicit adoption of an authorized fresh version after conflict", async () => {
    const conflict = new ContractApiError(409, { code: "VERSION_CONFLICT", message: "version", retryable: false, request_id: "request", field_errors: [], details: {} }, new Headers());
    client.PATCH.mockRejectedValueOnce(conflict).mockResolvedValueOnce(ok({ ...baby, version: 3 }));
    client.GET.mockResolvedValue(ok({ items: [{ baby: { ...baby, alias: "다른 이름", version: 2 } }] }));
    show();
    fireEvent.submit(await openAndEdit());
    fireEvent.click(await screen.findByRole("button", { name: "최신 값으로 다시 작성" }));
    expect((screen.getByRole("textbox", { name: "아기 이름" }) as HTMLInputElement).value).toBe("다른 이름");
    fireEvent.click(screen.getByRole("button", { name: "변경 저장" }));
    await waitFor(() => expect(client.PATCH).toHaveBeenCalledTimes(2));
    expect(client.PATCH.mock.calls[1]![1].body.version).toBe(2);
    expect(client.PATCH.mock.calls[1]![1].body.client_request_id).not.toBe(client.PATCH.mock.calls[0]![1].body.client_request_id);
  });
  it("does not display a late save after the private baby scope changes", async () => {
    let finish!: (value: unknown) => void;
    client.PATCH.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    show();
    fireEvent.submit(await openAndEdit());
    act(() => scope.set("user-a", "baby-b"));
    await act(async () => finish(ok({ ...baby, version: 2 })));
    expect(screen.queryByText("프로필을 저장했어요.")).toBeNull();
    expect(screen.queryByRole("textbox", { name: "아기 이름" })).toBeNull();
  });
});
