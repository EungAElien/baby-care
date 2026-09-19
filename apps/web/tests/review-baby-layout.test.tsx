// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { useEffect, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import BabyLayout from "../src/app/babies/[babyId]/layout";
import { AppProviders, usePrivateScope } from "../src/components/app-providers";
import type { components } from "../src/lib/api/generated";
import type { PrivateScope } from "../src/lib/private-scope";

const fixtureFile = resolve(process.cwd(), "../../contracts/목 응답과 시험 사용자 배치.json");
const fixtures = JSON.parse(readFileSync(fixtureFile, "utf8")) as {
  scenarios: { name: string; response: { body: unknown } }[];
};
const babyA = fixtures.scenarios.find((scenario) => scenario.name === "baby_created")!
  .response.body as components["schemas"]["BabyAccess"];
const babyAId = babyA.baby.baby_id;
const babyBId = "10000000-0000-4000-8000-000000000102";

const route = vi.hoisted(() => ({
  babyId: "10000000-0000-4000-8000-000000000101",
  userId: "10000000-0000-4000-8000-000000000001",
  babyAId: "10000000-0000-4000-8000-000000000101",
  babyBId: "10000000-0000-4000-8000-000000000102",
  realStatus: "signed-in" as "signed-in" | "signed-out",
  mockEnabled: false,
  getBabies: vi.fn(async () => ({ data: { items: [] as unknown[] }, response: Response.json({}) })),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ babyId: route.babyId }),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));
vi.mock("@/lib/auth/real-session", () => ({
  useRealSession: () => ({ status: route.realStatus, userId: route.userId, signOut: vi.fn() }),
}));
vi.mock("@/lib/mock/config", () => ({ isMockNavEnabled: () => route.mockEnabled }));
vi.mock("@/lib/mock/session", () => ({
  useMockSession: () => ({
    alias: "owner_a",
    userId: route.userId,
    membershipFor: () => ({ role: "OWNER" }),
    activeMemberships: [{ baby_id: route.babyAId }, { baby_id: route.babyBId }],
    signOut: vi.fn(),
  }),
}));
vi.mock("@/lib/api/real-client", () => ({ useApiClient: () => ({ GET: route.getBabies }) }));
vi.mock("@/components/baby-shell", () => ({
  BabyShell: ({ babyId, children }: { babyId: string; children: ReactNode }) => (
    <div data-testid="baby-shell" data-baby-id={babyId}>{children}</div>
  ),
}));

let scope: PrivateScope;
function ScopeSeeder({ children }: Readonly<{ children: ReactNode }>) {
  const privateScope = usePrivateScope();
  const snapshot = useSyncExternalStore(
    (listener) => privateScope.subscribe(listener),
    () => privateScope.snapshot(),
    () => privateScope.snapshot(),
  );
  useEffect(() => {
    scope = privateScope;
    privateScope.set(babyA.membership.user_id, null);
  }, [privateScope]);
  return snapshot.userId === babyA.membership.user_id ? children : null;
}

function renderBaby() {
  return render(
    <AppProviders>
      <ScopeSeeder><BabyLayout><p>아기 화면</p></BabyLayout></ScopeSeeder>
    </AppProviders>,
  );
}

beforeEach(() => {
  route.babyId = babyAId;
  route.realStatus = "signed-in";
  route.mockEnabled = false;
  route.getBabies.mockReset();
  route.getBabies.mockResolvedValue({ data: { items: [babyA] }, response: Response.json({}) });
});
afterEach(cleanup);

describe("A-03 baby scope before queries", () => {
  it.each([false, true])("loads on first entry with mock navigation %s, then clears before a baby switch", async (mockEnabled) => {
    route.mockEnabled = mockEnabled;
    const view = renderBaby();
    expect((await screen.findByTestId("baby-shell")).getAttribute("data-baby-id")).toBe(babyAId);
    expect(route.getBabies).toHaveBeenCalledTimes(1);
    expect(scope.snapshot().babyId).toBe(babyAId);

    route.babyId = babyBId;
    view.rerender(
      <AppProviders>
        <ScopeSeeder><BabyLayout><p>아기 화면</p></BabyLayout></ScopeSeeder>
      </AppProviders>,
    );
    expect(await screen.findByText("찾을 수 없거나 접근할 수 없어요.")).toBeTruthy();
    expect(scope.snapshot().babyId).toBe(babyBId);
    expect(route.getBabies).toHaveBeenCalledTimes(2);
    expect(screen.queryByTestId("baby-shell")).toBeNull();
  });

  it("prepares the mock fallback scope without starting a real API query", async () => {
    route.mockEnabled = true;
    route.realStatus = "signed-out";
    renderBaby();
    expect((await screen.findByTestId("baby-shell")).getAttribute("data-baby-id")).toBe(babyAId);
    expect(route.getBabies).not.toHaveBeenCalled();
    expect(scope.snapshot().babyId).toBe(babyAId);
  });
});
