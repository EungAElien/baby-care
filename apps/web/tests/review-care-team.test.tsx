// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { useEffect, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CareTeamPage from "../src/app/babies/[babyId]/care-team/page";
import { AppProviders, usePrivateScope } from "../src/components/app-providers";
import { DraftProvider, useDraft } from "../src/lib/mock/draft";
import type { components } from "../src/lib/api/generated";
import { privateQueryKey, ScopeChangedError, type PrivateScope } from "../src/lib/private-scope";

const fixtureFile = resolve(process.cwd(), "../../contracts/목 응답과 시험 사용자 배치.json");
const fixtures = JSON.parse(readFileSync(fixtureFile, "utf8")) as {
  scenarios: { name: string; response: { body: unknown } }[];
};
function fixture(name: string) {
  const scenario = fixtures.scenarios.find((item) => item.name === name);
  if (!scenario) throw new Error(`Missing fixture ${name}`);
  return scenario.response.body;
}
const accepted = fixture("invite_accepted") as components["schemas"]["BabyAccess"];
const owner = fixture("baby_created") as components["schemas"]["BabyAccess"];
const left = fixture("member_left") as components["schemas"]["Membership"];

const api = vi.hoisted(() => ({
  userId: "10000000-0000-4000-8000-000000000004",
  push: vi.fn(),
  get: vi.fn(),
  delete: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ babyId: "10000000-0000-4000-8000-000000000101" }),
  useRouter: () => ({ push: api.push }),
}));
vi.mock("@/lib/auth/real-session", () => ({
  useRealSession: () => ({ status: "signed-in", userId: api.userId }),
}));
vi.mock("@/lib/mock/session", () => ({ useMockSessionOptional: () => null }));
vi.mock("@/lib/api/real-client", () => ({
  useApiClient: () => ({ GET: api.get, DELETE: api.delete }),
}));

let scope: PrivateScope;
let queryClient: QueryClient;
let draft: ReturnType<typeof useDraft>;
function Probe() {
  const currentScope = usePrivateScope();
  const currentQueryClient = useQueryClient();
  const currentDraft = useDraft();
  useEffect(() => {
    scope = currentScope;
    queryClient = currentQueryClient;
    draft = currentDraft;
  }, [currentScope, currentQueryClient, currentDraft]);
  return null;
}

function ScopeSeeder({ children }: Readonly<{ children: ReactNode }>) {
  const privateScope = usePrivateScope();
  const snapshot = useSyncExternalStore(
    (listener) => privateScope.subscribe(listener),
    () => privateScope.snapshot(),
    () => privateScope.snapshot(),
  );
  useEffect(() => {
    privateScope.set(api.userId, accepted.baby.baby_id);
  }, [privateScope]);
  return snapshot.userId === api.userId && snapshot.babyId === accepted.baby.baby_id ? children : null;
}

function renderCareTeam() {
  return render(
    <AppProviders>
      <DraftProvider>
        <Probe />
        <ScopeSeeder>
          <CareTeamPage />
        </ScopeSeeder>
      </DraftProvider>
    </AppProviders>,
  );
}

beforeEach(() => {
  api.userId = accepted.membership.user_id;
  api.push.mockReset();
  api.get.mockReset();
  api.delete.mockReset();
  api.get.mockImplementation(async (path: string) => {
    if (path === "/babies") {
      const access = api.userId === owner.membership.user_id ? owner : accepted;
      return { data: { items: [access] }, response: Response.json({}) };
    }
    if (path === "/babies/{baby_id}/members") {
      return { data: { items: [owner.membership, accepted.membership] }, response: Response.json({}) };
    }
    return { data: { items: [] }, response: Response.json({}) };
  });
  api.delete.mockResolvedValue({ data: left, response: Response.json({}) });
});
afterEach(cleanup);

describe("A-03 real membership loss", () => {
  it("clears the departing caretaker's baby scope, pending request, cache and draft after server success", async () => {
    renderCareTeam();
    fireEvent.click(await screen.findByRole("button", { name: "이 아기에서 나가기" }));
    const before = scope.snapshot();
    const key = privateQueryKey(before, "private-record");
    queryClient.setQueryData(key, { sensitive: true });
    const controller = new AbortController();
    scope.trackRequest(controller);
    act(() => {
      draft.setLiveText(accepted.baby.baby_id, "미전송 입력");
    });
    act(() => draft.saveLiveAsDraft(accepted.baby.baby_id));
    act(() => draft.setLiveText(accepted.baby.baby_id, "다시 입력한 원문"));

    fireEvent.click(screen.getByRole("button", { name: "나가기 확정" }));
    await waitFor(() => expect(api.push).toHaveBeenCalledWith("/login"));

    expect(api.delete).toHaveBeenCalledTimes(1);
    expect(scope.snapshot()).toMatchObject({ userId: api.userId, babyId: null });
    expect(controller.signal.aborted).toBe(true);
    expect(queryClient.getQueryData(key)).toBeUndefined();
    expect(() => scope.assertCurrent(before)).toThrow(ScopeChangedError);
    expect(draft.getLiveText(accepted.baby.baby_id)).toBe("");
    expect(draft.getSavedDraft(accepted.baby.baby_id)).toBeNull();
  });

  it("does not clear an owner's own scope when removing another member", async () => {
    api.userId = owner.membership.user_id;
    renderCareTeam();
    fireEvent.click(await screen.findByRole("button", { name: "제거" }));
    await waitFor(() => expect(api.delete).toHaveBeenCalledTimes(1));
    expect(scope.snapshot()).toMatchObject({ userId: api.userId, babyId: owner.baby.baby_id });
    expect(api.push).not.toHaveBeenCalled();
  });
});
