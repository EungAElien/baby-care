import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EntryWorkspace } from "../src/lib/care-entries/workspace";
import { emptySource, observationDisplay, ruleContent, textEvidence } from "../src/lib/care-entries/content";
import { ContractApiError } from "../src/lib/api/errors";
import { NetworkRequestError } from "../src/lib/api/client";
import type { CareEntriesApi, Entry, Run, Confirmed, Observation, Source } from "../src/lib/api/care-entries";
import type { components } from "../src/lib/api/generated";

const fixtures = JSON.parse(readFileSync(resolve(process.cwd(), "../../contracts/목 응답과 시험 사용자 배치.json"), "utf8")) as { scenarios: { name: string; response: { body: unknown } }[] };
export const run = fixtures.scenarios.find((item) => item.name === "normalization_review")!.response.body as Run;
const confirmed = fixtures.scenarios.find((item) => item.name === "confirm_once")!.response.body as Confirmed;
const observation = fixtures.scenarios.find((item) => item.name === "confirmed_state_visual")!.response.body as Observation;
const choice: Source["choices"][number] = { choice_id: "choice1", kind: "ACTION", code: "FEEDING", assertion: "PLANNED" };
const baseEntry: Entry = { entry_id: run.entry_id, baby_id: observation.baby_id, author_user_id: observation.created_by_user_id,
  original_author_user_id: observation.created_by_user_id, episode_id: null, input_mode: "TEXT", raw_text: "분유 80mL 먹였어요", choices: [],
  occurred_at: null, time_precision: "UNKNOWN", input_revision: 1, status: "DRAFT", normalization_run_id: null, normalized_content: null,
  supersedes_entry_id: null, base_record_versions: [], confirmed_resources: null, confirmed_by_user_id: null, confirmed_at: null,
  data_origin: "DEMO", version: 1, recorded_at: run.recorded_at, updated_at: run.recorded_at };
const capabilities = { normalizer_available: true, normalizer_unavailable_reason: null } as components["schemas"]["Capabilities"];
function setup(options: { approved?: boolean; entry?: Entry } = {}) {
  let entry = structuredClone(options.entry ?? baseEntry);
  const api: CareEntriesApi = {
    capabilities: vi.fn(async () => capabilities), list: vi.fn(async () => ({ items: [entry], next_cursor: null })),
    get: vi.fn(async () => entry),
    create: vi.fn(async (body) => { entry = { ...entry, ...body }; return entry; }),
    patch: vi.fn(async (_id, body) => { entry = { ...entry, ...body, input_revision: body.input_revision + 1 }; return entry; }),
    normalize: vi.fn(async (_id, body) => ({ ...run, run_id: body.run_id, input_revision: body.input_revision })),
    run: vi.fn(async (_id, runId) => ({ ...run, run_id: runId })),
    confirm: vi.fn(async () => confirmed), observation: vi.fn(async () => observation), baseRecords: vi.fn(async () => []),
  };
  const workspace = new EntryWorkspace(api, () => {}, options.approved ?? true);
  return { api, workspace };
}
const apiError = (code: string, status = 409) => new ContractApiError(status, { code, message: "synthetic", retryable: false,
  request_id: crypto.randomUUID(), field_errors: [{ field: "actions.0.amount", code: "INVALID", message: "수량 확인" }], details: {} }, new Headers());
const deferred = <T>() => { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; };
afterEach(() => vi.useRealTimers());

describe("A-07 eventless draft and recovery", () => {
  it.each(["CHOICE", "TEXT", "MIXED"] as const)("creates and edits %s preserving sources", async (input_mode) => {
    const { api, workspace } = setup();
    const source = { ...emptySource(), input_mode, raw_text: input_mode === "CHOICE" ? null : "합성 원문 👶", choices: input_mode === "TEXT" ? [] : [choice] };
    workspace.setSource(source);
    await workspace.save();
    expect(api.create).toHaveBeenCalledWith(expect.objectContaining({ ...source, episode_id: null, base_record_versions: [] }));
    workspace.setSource({ ...source, raw_text: input_mode === "CHOICE" ? null : "바뀐 합성 원문" });
    await workspace.save();
    expect(api.patch).toHaveBeenCalledWith(run.entry_id, expect.objectContaining({ input_revision: 1 }));
    expect(workspace.snapshot().entry?.input_revision).toBe(2);
  });
  it("lists only server drafts and opens latest revision/run after refresh", async () => {
    const { workspace, api } = setup({ entry: { ...baseEntry, normalization_run_id: run.run_id } });
    await workspace.load(); await workspace.open(run.entry_id);
    expect(api.run).toHaveBeenCalledWith(run.entry_id, run.run_id);
    expect(workspace.snapshot().mode).toBe("LLM");
    expect(workspace.snapshot().reviewed).toBe(false);
  });
  it("replays a lost draft create with identical UUID and body", async () => {
    const { workspace, api } = setup();
    vi.mocked(api.create).mockRejectedValueOnce(new NetworkRequestError());
    workspace.setSource({ ...emptySource(), raw_text: "합성 초안" });
    await workspace.save();
    workspace.setSource({ ...emptySource(), raw_text: "응답 미확정 중 바꾸기" });
    await workspace.save();
    expect(vi.mocked(api.create).mock.calls[0]).toEqual(vi.mocked(api.create).mock.calls[1]);
    expect(workspace.snapshot().source.raw_text).toBe("합성 초안");
  });
  it("at 35 seconds only GETs the same run and never starts another POST", async () => {
    vi.useFakeTimers();
    const { workspace, api } = setup();
    const wait = deferred<Run>();
    vi.mocked(api.normalize).mockReturnValue(wait.promise);
    await workspace.load(); await workspace.open(run.entry_id);
    const task = workspace.normalize();
    const body = vi.mocked(api.normalize).mock.calls[0]![1];
    await vi.advanceTimersByTimeAsync(34999);
    expect(api.run).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(api.run).toHaveBeenCalledWith(run.entry_id, body.run_id);
    expect(api.normalize).toHaveBeenCalledTimes(1);
    wait.resolve({ ...run, run_id: body.run_id, status: "RUNNING", result: null }); await task;
    expect(workspace.snapshot().runStatus).toBe("COMPLETE");
  });
  it("recovers a lost normalization response by GET without recreating the run", async () => {
    const { workspace, api } = setup();
    vi.mocked(api.normalize).mockRejectedValueOnce(new NetworkRequestError());
    await workspace.load(); await workspace.open(run.entry_id); await workspace.normalize();
    const body = vi.mocked(api.normalize).mock.calls[0]![1];
    expect(api.run).toHaveBeenCalledWith(run.entry_id, body.run_id);
    expect(body.client_request_id).not.toBe(body.run_id);
  });
  it("does not replace user edits with a late duplicate COMPLETE after 35-second recovery", async () => {
    vi.useFakeTimers();
    const { workspace, api } = setup(); const wait = deferred<Run>(); vi.mocked(api.normalize).mockReturnValue(wait.promise);
    await workspace.load(); await workspace.open(run.entry_id); const task = workspace.normalize();
    const body = vi.mocked(api.normalize).mock.calls[0]![1];
    await vi.advanceTimersByTimeAsync(35000);
    workspace.setContent({ ...workspace.snapshot().content, actions: [] });
    wait.resolve({ ...run, run_id: body.run_id }); await task;
    expect(workspace.snapshot().content.actions).toHaveLength(0);
  });
  it("keeps a late prior revision STALE and never applies its proposal", async () => {
    const { workspace, api } = setup();
    const wait = deferred<Run>(); vi.mocked(api.normalize).mockReturnValue(wait.promise);
    await workspace.load(); await workspace.open(run.entry_id);
    const task = workspace.normalize();
    workspace.setSource({ ...emptySource(), raw_text: "수유 안 했어요" });
    expect(workspace.snapshot().runStatus).toBe("STALE");
    await workspace.save();
    wait.resolve(run); await task;
    expect(workspace.snapshot().entry?.input_revision).toBe(2);
    expect(workspace.snapshot().content.actions).toHaveLength(0);
    expect(workspace.snapshot().mode).toBe("MANUAL");
  });
  it("treats a retrieved prior revision as STALE even if the response says COMPLETE", async () => {
    const { workspace } = setup({ entry: { ...baseEntry, input_revision: 2, normalization_run_id: run.run_id } });
    await workspace.open(run.entry_id);
    expect(workspace.snapshot().runStatus).toBe("STALE");
    expect(workspace.snapshot().content.actions).toHaveLength(0);
  });
  it.each(["RUNNING", "FAILED", "STALE", "COMPLETE"] as const)("represents run %s independently", async (status) => {
    vi.useFakeTimers();
    const { workspace, api } = setup({ entry: { ...baseEntry, normalization_run_id: run.run_id } });
    vi.mocked(api.run).mockResolvedValue({ ...run, status, result: status === "COMPLETE" ? run.result : null });
    await workspace.open(run.entry_id);
    expect(workspace.snapshot().runStatus).toBe(status);
    workspace.dispose();
  });
  it.each([false, true])("blocks provider POST without both server and product approval (server=%s)", async (available) => {
    const { workspace, api } = setup({ approved: !available });
    vi.mocked(api.capabilities).mockResolvedValue({ ...capabilities, normalizer_available: available });
    await workspace.load(); await workspace.open(run.entry_id); await workspace.normalize();
    expect(api.normalize).not.toHaveBeenCalled();
    workspace.useDirect("MANUAL"); workspace.setReviewed(true); await workspace.confirm();
    expect(api.confirm).toHaveBeenCalledWith(run.entry_id, expect.objectContaining({ normalization_mode: "MANUAL", run_id: null }));
  });
  it("capability outage still lists drafts and supports RULE", async () => {
    const { workspace, api } = setup({ entry: { ...baseEntry, input_mode: "CHOICE", choices: [choice] } });
    vi.mocked(api.capabilities).mockRejectedValue(new NetworkRequestError());
    await workspace.load();
    expect(workspace.snapshot().entries).toHaveLength(1);
    await workspace.open(run.entry_id); workspace.useDirect("RULE"); workspace.setReviewed(true); await workspace.confirm();
    expect(api.confirm).toHaveBeenCalledWith(run.entry_id, expect.objectContaining({ normalization_mode: "RULE", run_id: null }));
  });
  it.each(["PLANNED", "NEGATED", "UNCERTAIN"] as const)("preserves %s instead of turning it into PERFORMED", async (assertion) => {
    const content = ruleContent({ ...emptySource(), choices: [{ ...choice, assertion }] });
    expect(content.actions[0]?.assertion).toBe(assertion);
    expect(content.states).toHaveLength(0);
  });
  it("returns to MANUAL after provider failure, without a success run_id", async () => {
    const { workspace, api } = setup();
    vi.mocked(api.normalize).mockImplementation(async (_id, body) => ({ ...run, run_id: body.run_id, status: "FAILED", result: null }));
    await workspace.load(); await workspace.open(run.entry_id); await workspace.normalize();
    expect(workspace.snapshot().runStatus).toBe("FAILED");
    workspace.useDirect("MANUAL"); workspace.setReviewed(true); await workspace.confirm();
    expect(api.confirm).toHaveBeenCalledWith(run.entry_id, expect.objectContaining({ normalization_mode: "MANUAL", run_id: null }));
    expect(workspace.snapshot().source.raw_text).toBe(baseEntry.raw_text);
  });
  it("requires explicit confirmation and sends successful LLM run and exact base versions", async () => {
    const versions: Entry["base_record_versions"] = [{ resource_type: "CARE_EVENT", resource_id: confirmed.care_event_ids[0]!, version: 4 }];
    const { workspace, api } = setup({ entry: { ...baseEntry, base_record_versions: versions } });
    await workspace.load(); await workspace.open(run.entry_id); await workspace.normalize(); await workspace.confirm();
    expect(api.confirm).not.toHaveBeenCalled();
    workspace.setReviewed(true); await workspace.confirm();
    expect(api.confirm).toHaveBeenCalledWith(run.entry_id, expect.objectContaining({ normalization_mode: "LLM", input_revision: 1,
      run_id: vi.mocked(api.normalize).mock.calls[0]![1].run_id, base_record_versions: versions }));
  });
  it("replays identical confirmation after response loss and blocks edits or double saves", async () => {
    const { workspace, api } = setup();
    vi.mocked(api.confirm).mockRejectedValueOnce(new NetworkRequestError());
    await workspace.open(run.entry_id); workspace.useDirect("MANUAL"); workspace.setReviewed(true); await workspace.confirm();
    workspace.setContent(ruleContent({ ...emptySource(), choices: [choice] }));
    await workspace.confirm(); await workspace.confirm();
    expect(api.confirm).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.confirm).mock.calls[0]).toEqual(vi.mocked(api.confirm).mock.calls[1]);
  });
  it.each(["SOURCE_REVISION_CHANGED", "VERSION_CONFLICT", "ALREADY_CONFIRMED", "VALIDATION_ERROR"])("preserves source and exposes recovery for %s", async (code) => {
    const { workspace, api } = setup();
    vi.mocked(api.confirm).mockRejectedValue(apiError(code, code === "VALIDATION_ERROR" ? 422 : 409));
    await workspace.open(run.entry_id); workspace.setReviewed(true); await workspace.confirm();
    expect(workspace.snapshot().issue?.code).toBe(code);
    expect(workspace.snapshot().issue?.fields[0]?.field).toBe("actions.0.amount");
    expect(workspace.snapshot().source.raw_text).toBe(baseEntry.raw_text);
    expect(workspace.snapshot().pendingConfirm).toBeNull();
    await workspace.refreshLatest();
    expect(workspace.snapshot().latest?.entry_id).toBe(run.entry_id);
  });
  it("uses existing_run_id after NORMALIZATION_IN_PROGRESS", async () => {
    const { workspace, api } = setup(); const error = apiError("NORMALIZATION_IN_PROGRESS");
    vi.mocked(api.normalize).mockRejectedValue(new ContractApiError(409, { ...error.envelope, details: { existing_run_id: run.run_id } }, new Headers()));
    await workspace.load(); await workspace.open(run.entry_id); await workspace.normalize();
    expect(api.run).toHaveBeenCalledWith(run.entry_id, run.run_id);
    expect(api.normalize).toHaveBeenCalledTimes(1);
  });
  it("prepares a replacement correction using freshly read base versions without creating a new record", async () => {
    const oldVersions: Entry["base_record_versions"] = [{ resource_type: "CARE_EVENT", resource_id: confirmed.care_event_ids[0]!, version: 1 }];
    const original = crypto.randomUUID();
    const { workspace, api } = setup({ entry: { ...baseEntry, supersedes_entry_id: original, base_record_versions: oldVersions } });
    vi.mocked(api.baseRecords).mockResolvedValue([{ record: { ...oldVersions[0]!, version: 2 }, summary: "최신 합성 기록" }]);
    vi.mocked(api.create).mockImplementation(async (body) => ({ ...baseEntry, ...body, entry_id: crypto.randomUUID() }));
    await workspace.open(run.entry_id); await workspace.refreshLatest(); await workspace.rebaseCorrection();
    expect(api.create).toHaveBeenCalledWith(expect.objectContaining({ supersedes_entry_id: original,
      base_record_versions: [{ ...oldVersions[0]!, version: 2 }] }));
    expect(workspace.snapshot().reviewed).toBe(false);
    expect(workspace.snapshot().mode).toBe("MANUAL");
  });
  it("restores confirmed IDs after ALREADY_CONFIRMED without another confirmation POST", async () => {
    const { workspace, api } = setup(); await workspace.open(run.entry_id);
    vi.mocked(api.confirm).mockRejectedValue(apiError("ALREADY_CONFIRMED"));
    workspace.setReviewed(true); await workspace.confirm();
    vi.mocked(api.get).mockResolvedValue({ ...baseEntry, status: "CONFIRMED", confirmed_resources: confirmed });
    await workspace.refreshLatest(); await workspace.confirm();
    expect(workspace.snapshot().confirmed).toEqual(confirmed);
    expect(api.confirm).toHaveBeenCalledTimes(1);
  });
  it("clears private state on permission loss and ignores late completion after disposal", async () => {
    const { workspace, api } = setup(); await workspace.open(run.entry_id);
    vi.mocked(api.get).mockRejectedValue(apiError("RESOURCE_NOT_FOUND", 404));
    await workspace.refreshLatest(); expect(workspace.snapshot().source.raw_text).toBe("");
    expect(workspace.snapshot().entry).toBeNull();
    const other = setup(); const wait = deferred<Run>(); vi.mocked(other.api.normalize).mockReturnValue(wait.promise);
    await other.workspace.load(); await other.workspace.open(run.entry_id); const task = other.workspace.normalize();
    other.workspace.dispose(); wait.resolve(run); await task;
    expect(other.workspace.snapshot().entry).toBeNull();
  });
});

describe("Unicode evidence and confirmed observation display", () => {
  it("converts emoji + Korean DOM offsets to code points and preserves combining characters", () => {
    expect(textEvidence("👶 분유 먹었어요", 3, 5)).toEqual({ source: "TEXT", choice_id: null, span_start: 2, span_end: 4, quote: "분유" });
    expect(textEvidence("가👨‍👩‍👧 나", 1, 9).quote).toBe("👨‍👩‍👧");
    expect(textEvidence("가🙂", 0, 2).span_end).toBe(2);
    expect(() => textEvidence("👶", 1, 2)).toThrow();
  });
  it.each([["UNKNOWN"], ["CRYING", "CALM"], ["ASLEEP", "AWAKE"]] as Observation["state_codes"][])("uses NEUTRAL for %j", (...codes) => {
    // it.each expands each array as arguments.
    const state_codes = codes.flat() as Observation["state_codes"];
    expect(observationDisplay({ ...observation, state_codes, visual_state_code: "CALM" }, Date.now()).visual).toBe("NEUTRAL");
  });
  it("marks >30 minutes as old and never invents a timestamp for UNKNOWN_TIME", () => {
    const start = Date.parse(observation.observed_at!);
    expect(observationDisplay(observation, start + 1800000).stale).toBe(false);
    expect(observationDisplay(observation, start + 1800001).stale).toBe(true);
    expect(observationDisplay({ ...observation, observed_at: null }, start).minutes).toBeNull();
  });
});
