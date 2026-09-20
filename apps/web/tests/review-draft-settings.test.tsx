// @vitest-environment jsdom
import { createElement, useEffect } from "react";
import type { ReactNode } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SettingsPage from "../src/app/babies/[babyId]/settings/page";
import { DraftProvider, useDraft } from "../src/lib/mock/draft";

const identity = vi.hoisted(() => ({
  realStatus: "signed-in" as "loading" | "signed-in" | "signed-out",
  realUserId: "real-a" as string | null,
  mockAlias: "owner_a" as string | null,
}));

vi.mock("@/lib/auth/real-session", () => ({
  useRealSession: () => ({ status: identity.realStatus, userId: identity.realUserId }),
}));
vi.mock("@/lib/mock/session", () => ({
  useMockSessionOptional: () => identity.mockAlias ? { alias: identity.mockAlias, membershipFor: () => null } : null,
  useMockSession: () => { throw new Error("MockSessionProvider is absent"); },
}));
vi.mock("next/navigation", () => ({
  useParams: () => ({ babyId: "baby-a" }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: ReactNode }) => createElement("a", { href }, children),
}));
vi.mock("@/lib/api/babies", () => ({
  useBabiesQuery: () => ({ isLoading: false, isError: false, data: { items: [] } }),
  findBabyAccess: () => ({ membership: { role: "OWNER" } }),
}));
vi.mock("@/components/consent-panel", () => ({
  ConsentPanel: () => createElement("p", null, "실제 동의 패널"),
}));

let draft: ReturnType<typeof useDraft>;
function DraftProbe() {
  const currentDraft = useDraft();
  useEffect(() => { draft = currentDraft; }, [currentDraft]);
  return <p data-testid="draft-text">{currentDraft.getLiveText("baby-a")}</p>;
}

beforeEach(() => {
  identity.realStatus = "signed-in";
  identity.realUserId = "real-a";
  identity.mockAlias = "owner_a";
});
afterEach(cleanup);

describe("A-03 draft identity and settings", () => {
  it("prefers the real account over a stale mock alias and drops live and saved text on every owner change", () => {
    const view = render(<DraftProvider><DraftProbe /></DraftProvider>);
    act(() => draft.setLiveText("baby-a", "A의 비공개 원문"));
    act(() => draft.saveLiveAsDraft("baby-a"));
    act(() => draft.setLiveText("baby-a", "A의 미전송 입력"));
    expect(draft.getLiveText("baby-a")).toBe("A의 미전송 입력");
    expect(draft.getSavedDraft("baby-a")).toBe("A의 비공개 원문");

    identity.realUserId = "real-b";
    view.rerender(<DraftProvider><DraftProbe /></DraftProvider>);
    expect(draft.getLiveText("baby-a")).toBe("");
    expect(draft.getSavedDraft("baby-a")).toBeNull();
    expect(screen.getByTestId("draft-text").textContent).toBe("");

    act(() => draft.setLiveText("baby-a", "B의 입력"));
    identity.realStatus = "signed-out";
    identity.realUserId = null;
    view.rerender(<DraftProvider><DraftProbe /></DraftProvider>);
    expect(draft.getLiveText("baby-a")).toBe("");

    act(() => draft.setLiveText("baby-a", "목 계정 입력"));
    identity.mockAlias = "owner_b";
    view.rerender(<DraftProvider><DraftProbe /></DraftProvider>);
    expect(draft.getLiveText("baby-a")).toBe("");
  });

  it("renders real settings without a mock provider or fixture-only controls", () => {
    render(<SettingsPage />);
    expect(screen.getByRole("link", { name: "공동양육 관리로 이동" }).getAttribute("href"))
      .toBe("/babies/baby-a/care-team");
    expect(screen.getByText(/아직 실제 API와 연결되지 않았어요/)).toBeTruthy();
    expect(screen.queryByText("별칭 저장 시도 (예시)")).toBeNull();
  });
});
