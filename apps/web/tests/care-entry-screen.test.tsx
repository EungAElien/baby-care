// @vitest-environment jsdom
import { createElement } from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CareEntryWorkspaceView } from "../src/components/care-entry-workspace";
import { CareEntryObservation } from "../src/components/care-entry-observation";
import { EntryWorkspace } from "../src/lib/care-entries/workspace";
import type { CareEntriesApi, Entry, Confirmed, Observation } from "../src/lib/api/care-entries";
import type { components } from "../src/lib/api/generated";
import { ContractApiError } from "../src/lib/api/errors";
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => createElement("a", { href }, children) }));
const fixtures = JSON.parse(readFileSync(resolve(process.cwd(), "../../contracts/목 응답과 시험 사용자 배치.json"), "utf8")) as { scenarios: { name: string; response: { body: unknown } }[] };
const entry = { ...fixtures.scenarios.find((item) => item.name === "draft_private")!.response.body as Entry,
  episode_id: null, normalization_run_id: null, normalized_content: null, status: "DRAFT" as const,
  choices: [{ choice_id: "choice1", kind: "ACTION" as const, code: "FEEDING", assertion: "NEGATED" as const }] };
const confirmed = { ...fixtures.scenarios.find((item) => item.name === "confirm_once")!.response.body as Confirmed, care_event_ids: [] };
const observation = fixtures.scenarios.find((item) => item.name === "confirmed_state_visual")!.response.body as Observation;
function setup() {
  const api: CareEntriesApi = { capabilities: vi.fn(async () => ({ normalizer_available: false, normalizer_unavailable_reason: "DISABLED" }) as components["schemas"]["Capabilities"]),
    list: vi.fn(async () => ({ items: [entry], next_cursor: null })), get: vi.fn(async () => entry),
    create: vi.fn(async () => entry), patch: vi.fn(async () => entry), normalize: vi.fn(), run: vi.fn(),
    confirm: vi.fn(async () => confirmed), observation: vi.fn(async () => observation), baseRecords: vi.fn(async () => []) };
  const workspace = new EntryWorkspace(api, () => {});
  return { api, workspace };
}
afterEach(cleanup);
describe("A-07 review UI", () => {
  it("displays server gating and keeps RULE/MANUAL, original input and explicit confirmation usable", async () => {
    const { workspace, api } = setup(); await workspace.load(); await workspace.open(entry.entry_id);
    render(<CareEntryWorkspaceView workspace={workspace} babyId={entry.baby_id} />);
    expect((screen.getByRole("button", { name: "LLM으로 정리" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/서버에서 꺼져/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "선택지만 규칙으로 확인 (RULE)" }));
    expect((screen.getByLabelText("행동의 의미") as HTMLSelectElement).value).toBe("NEGATED");
    expect((screen.getByRole("button", { name: "확인한 내용 한 번 저장" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox"));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "확인한 내용 한 번 저장" })));
    expect(screen.getByText(/생활 기록 0개/)).toBeTruthy();
    expect(api.confirm).toHaveBeenCalledWith(entry.entry_id, expect.objectContaining({ normalization_mode: "RULE", run_id: null }));
  });
  it("editing a proposal removes prior explicit confirmation and shows field errors beside its card", async () => {
    const { workspace, api } = setup(); await workspace.open(entry.entry_id); workspace.useDirect("RULE");
    vi.mocked(api.confirm).mockRejectedValue(new ContractApiError(422, { code: "VALIDATION_ERROR", message: "synthetic", retryable: false, request_id: crypto.randomUUID(), details: {},
      field_errors: [{ field: "actions.0.amount", code: "VALUE", message: "수량을 다시 확인해 주세요" }] }, new Headers()));
    render(<CareEntryWorkspaceView workspace={workspace} babyId={entry.baby_id} />);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.change(screen.getByLabelText("행동의 의미"), { target: { value: "PLANNED" } });
    expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(false);
    fireEvent.click(screen.getByRole("checkbox"));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "확인한 내용 한 번 저장" })));
    expect(within(screen.getByRole("region", { name: "행동 1" })).getByRole("alert").textContent).toContain("수량을 다시 확인");
    expect((screen.getByLabelText("원문") as HTMLTextAreaElement).value).toBe(entry.raw_text);
    expect(screen.getByRole("button", { name: "최신 초안·저장 결과 조회" })).toBeTruthy();
  });
  it("provides static accessible NEUTRAL and old-observation text with provenance", () => {
    render(<CareEntryObservation observation={{ ...observation, state_codes: ["UNKNOWN"], visual_state_code: "NEUTRAL", observed_at: "2020-01-01T00:00:00Z" }} />);
    expect(screen.getByRole("img", { name: "상태 표시: 중립 표시" })).toBeTruthy();
    expect(screen.getByText(/오래된 상태/)).toBeTruthy();
    expect(screen.getByText(/직접 관찰 보고/)).toBeTruthy();
    expect(screen.getByText(/care-visual-v1/)).toBeTruthy();
  });
});
