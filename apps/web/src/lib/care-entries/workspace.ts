import { ContractApiError } from "@/lib/api/errors";
import { newClientRequestId } from "@/lib/api/client";
import type { components } from "@/lib/api/generated";
import type { CareEntriesApi, Entry, Source, Content, Run, Confirmation, Confirmed, Observation } from "@/lib/api/care-entries";
import { emptyContent, emptySource, ruleContent } from "./content";

type Schema = components["schemas"];
type Issue = { code: string; message: string; fields: readonly { field: string; message: string }[] };
type PendingSave = { id: string | null; body: Schema["CreateCareEntry"] | Schema["PatchCareEntry"] };
type State = {
  source: Source; entry: Entry | null; latest: Entry | null; entries: Entry[]; cursor: string | null;
  content: Content; mode: Confirmation["normalization_mode"]; run: Run | null;
  runId: string | null; runStatus: Run["status"] | "RECOVERING" | null;
  capabilities: Schema["Capabilities"] | null; issue: Issue | null; busy: boolean;
  dirty: boolean; reviewed: boolean; pendingSave: PendingSave | null; pendingConfirm: Confirmation | null;
  confirmed: Confirmed | null; observations: Observation[];
  latestRecords: Awaited<ReturnType<CareEntriesApi["baseRecords"]>> | null;
};

const initial = (): State => ({ source: emptySource(), entry: null, latest: null, entries: [], cursor: null,
  content: emptyContent(), mode: "MANUAL", run: null, runId: null, runStatus: null,
  capabilities: null, issue: null, busy: false, dirty: true, reviewed: false,
  pendingSave: null, pendingConfirm: null, confirmed: null, observations: [], latestRecords: null });

const messages: Record<string, string> = {
  SOURCE_REVISION_CHANGED: "다른 화면에서 원문이 바뀌었어요. 내 입력과 최신 원문을 비교해 주세요.",
  NORMALIZATION_IN_PROGRESS: "이미 정리 중이에요. 같은 작업을 조회해 복구할 수 있어요.",
  VERSION_CONFLICT: "연결된 기록이 바뀌었어요. 최신 기록과 내 초안을 비교해 주세요.",
  ALREADY_CONFIRMED: "이미 확인 저장된 초안이에요. 저장 결과를 다시 조회해 주세요.",
  VALIDATION_ERROR: "표시된 항목을 수정해 주세요. 원문은 그대로 남아 있어요.",
  RESOURCE_NOT_FOUND: "찾을 수 없거나 접근할 수 없어요.",
};

/** Memory only. The owning React boundary is replaced on every private-scope generation. */
export class EntryWorkspace {
  private state = initial();
  private listeners = new Set<() => void>();
  private active = true;
  private runGeneration = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private polling = false;

  constructor(private api: CareEntriesApi, private assertCurrent: () => void,
    readonly processingApproved = false) {}

  snapshot = () => this.state;
  activate() { this.active = true; }
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private update(patch: Partial<State>) {
    if (!this.active) return;
    this.assertCurrent();
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((listener) => listener());
  }
  dispose() {
    this.active = false;
    this.runGeneration++;
    if (this.timer) clearTimeout(this.timer);
    this.state = initial();
    this.listeners.clear();
  }
  private canEdit() { return !this.state.busy && !this.state.pendingSave && !this.state.pendingConfirm && !this.state.confirmed; }
  private stopRun() {
    this.runGeneration++;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }
  private fail(error: unknown) {
    if (!this.active) return;
    this.assertCurrent();
    if (error instanceof ContractApiError && [401, 403, 404, 410].includes(error.status)) {
      this.stopRun();
      this.update({ ...initial(), issue: { code: error.envelope.code, message: "찾을 수 없거나 접근할 수 없어요. 권한을 확인한 뒤 다시 열어 주세요.", fields: [] } });
      return;
    }
    const issue: Issue = error instanceof ContractApiError
      ? { code: error.envelope.code, message: messages[error.envelope.code] ?? "요청을 완료하지 못했어요. 원문을 유지하고 다시 확인해 주세요.", fields: error.envelope.field_errors }
      : { code: "RESPONSE_UNKNOWN", message: "응답을 확인하지 못했어요. 같은 요청으로 복구해 주세요.", fields: [] };
    if (error instanceof ContractApiError && error.envelope.code === "NORMALIZATION_IN_PROGRESS" && typeof error.envelope.details.existing_run_id === "string") {
      this.update({ runId: error.envelope.details.existing_run_id, runStatus: "RUNNING" });
    }
    this.update({ issue, busy: false });
  }
  private async perform(action: () => Promise<void>) {
    if (!this.active || this.state.busy) return;
    this.update({ busy: true, issue: null });
    try { await action(); } catch (error) { if (this.active) this.fail(error); }
    finally { if (this.active) this.update({ busy: false }); }
  }
  async load() {
    await this.perform(async () => {
      // Capability failure must not hide recoverable drafts or block manual confirmation.
      const [capabilities, entries] = await Promise.allSettled([this.api.capabilities(), this.api.list()]);
      if (entries.status === "fulfilled") this.update({ entries: entries.value.items, cursor: entries.value.next_cursor });
      else throw entries.reason;
      if (capabilities.status === "fulfilled") this.update({ capabilities: capabilities.value });
      else this.update({ capabilities: null, issue: { code: "CAPABILITIES_UNAVAILABLE", message: "LLM 준비 상태를 조회하지 못했어요. 직접 확인은 사용할 수 있어요.", fields: [] } });
    });
  }
  async more() {
    const cursor = this.state.cursor;
    if (!cursor) return;
    await this.perform(async () => {
      const page = await this.api.list(cursor);
      this.update({ entries: [...this.state.entries, ...page.items.filter((item) => !this.state.entries.some((previous) => previous.entry_id === item.entry_id))], cursor: page.next_cursor });
    });
  }
  setSource(source: Source) {
    if (!this.canEdit()) return;
    this.stopRun();
    this.update({ source, dirty: true, reviewed: false, content: emptyContent(), run: null,
      runStatus: this.state.runId ? "STALE" : null, mode: "MANUAL", issue: null });
  }
  setContent(content: Content) {
    if (!this.canEdit()) return;
    // Manual edits to a successful LLM proposal remain LLM/EDITED and retain its run.
    this.update({ content, reviewed: false, mode: this.state.mode === "RULE" ? "MANUAL" : this.state.mode });
  }
  setReviewed(reviewed: boolean) { this.update({ reviewed }); }
  useDirect(mode: "RULE" | "MANUAL") {
    if (!this.canEdit() || this.state.dirty || !this.state.entry) return;
    this.stopRun();
    this.update({ mode, content: mode === "RULE" ? ruleContent(this.state.source) : this.state.content,
      run: null, runStatus: null, reviewed: false, issue: null });
  }
  newDraft() {
    if (this.state.busy || this.state.pendingSave || this.state.pendingConfirm) return;
    this.stopRun();
    this.update({ ...initial(), capabilities: this.state.capabilities, entries: this.state.entries, cursor: this.state.cursor });
  }
  async open(id: string) {
    if (this.state.pendingConfirm || this.state.pendingSave) return;
    this.stopRun();
    await this.perform(async () => {
      const entry = await this.api.get(id);
      this.adopt(entry);
    });
    if (this.state.entry?.normalization_run_id && !this.state.confirmed) await this.recoverRun();
    if (this.state.confirmed) await this.loadObservations();
  }
  private adopt(entry: Entry) {
    this.update({ entry, source: { input_mode: entry.input_mode, raw_text: entry.raw_text, choices: entry.choices,
      occurred_at: entry.occurred_at, time_precision: entry.time_precision }, latest: null, dirty: false,
      content: emptyContent(), mode: "MANUAL", reviewed: false, run: null,
      runId: entry.normalization_run_id, runStatus: null, pendingSave: null, pendingConfirm: null,
      confirmed: entry.confirmed_resources, observations: [], issue: null, latestRecords: null });
  }
  async save() {
    if (this.state.pendingConfirm || this.state.confirmed) return;
    await this.perform(async () => {
      let pending = this.state.pendingSave;
      if (!pending) {
        const source = this.state.source;
        if ((source.input_mode !== "CHOICE" && !source.raw_text?.trim()) ||
          (source.input_mode !== "TEXT" && source.choices.length === 0) || Array.from(source.raw_text ?? "").length > 2000) {
          this.update({ issue: { code: "INPUT_REQUIRED", message: "원문은 1~2000자, 선택지 입력에는 한 개 이상의 선택이 필요해요.", fields: [] } });
          return;
        }
        const client_request_id = newClientRequestId();
        pending = this.state.entry
          ? { id: this.state.entry.entry_id, body: { ...source, client_request_id, input_revision: this.state.entry.input_revision } }
          : { id: null, body: { ...source, client_request_id, episode_id: null, supersedes_entry_id: null, base_record_versions: [] } };
        this.update({ pendingSave: pending });
      }
      try {
        const entry = pending.id === null
          ? await this.api.create(pending.body as Schema["CreateCareEntry"])
          : await this.api.patch(pending.id, pending.body as Schema["PatchCareEntry"]);
        this.stopRun();
        this.adopt(entry);
        this.update({ entries: [entry, ...this.state.entries.filter((item) => item.entry_id !== entry.entry_id)] });
      } catch (error) {
        if (error instanceof ContractApiError && error.status < 500) this.update({ pendingSave: null });
        throw error;
      }
    });
  }
  private acceptRun(run: Run, generation: number) {
    if (!this.active || generation !== this.runGeneration) return;
    const entry = this.state.entry;
    if (!entry || run.input_revision !== entry.input_revision || run.entry_id !== entry.entry_id || this.state.dirty || this.state.confirmed) {
      this.update({ run: null, runStatus: "STALE", reviewed: false });
      return;
    }
    // A delayed POST response must not regress a terminal result already recovered by GET.
    if (this.state.run?.run_id === run.run_id && this.state.run.status !== "RUNNING" && run.status !== "STALE") {
      this.update({ runStatus: this.state.run.status });
      return;
    }
    this.update({ run, runStatus: run.status, issue: null });
    if (run.status === "COMPLETE" && run.result) this.update({ mode: "LLM", content: run.result, reviewed: false });
    if (run.status === "STALE" || run.status === "FAILED") this.update({ content: emptyContent(), mode: "MANUAL", reviewed: false });
    if (run.status === "RUNNING") this.schedulePoll(generation, 1500);
  }
  private schedulePoll(generation: number, delay: number) {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => { if (generation === this.runGeneration) void this.recoverRun(); }, delay);
  }
  async normalize() {
    const { entry, dirty, capabilities, runStatus } = this.state;
    if (!entry || dirty || !this.canEdit() || !this.processingApproved || !capabilities?.normalizer_available ||
      runStatus === "RUNNING" || runStatus === "RECOVERING") return;
    this.stopRun();
    const generation = this.runGeneration;
    const body = { client_request_id: newClientRequestId(), run_id: newClientRequestId(), input_revision: entry.input_revision };
    this.update({ runId: body.run_id, run: null, runStatus: "RUNNING", content: emptyContent(), reviewed: false, issue: null });
    this.schedulePoll(generation, 35000);
    try {
      const run = await this.api.normalize(entry.entry_id, body);
      if (generation !== this.runGeneration || !this.active) return;
      if (this.timer) clearTimeout(this.timer);
      this.acceptRun(run, generation);
    } catch (error) {
      if (!this.active || generation !== this.runGeneration) return;
      if (this.timer) clearTimeout(this.timer);
      if (error instanceof ContractApiError && error.envelope.code === "NORMALIZATION_IN_PROGRESS" && typeof error.envelope.details.existing_run_id === "string") {
        this.update({ runId: error.envelope.details.existing_run_id });
      } else if (error instanceof ContractApiError && error.status < 500) {
        this.update({ runStatus: "FAILED" });
        this.fail(error);
        return;
      }
      await this.recoverRun();
    }
  }
  async recoverRun() {
    const { entry, runId } = this.state;
    if (!entry || !runId || this.polling || this.state.dirty || this.state.confirmed) return;
    const generation = this.runGeneration;
    this.polling = true;
    this.update({ runStatus: "RECOVERING" });
    try { this.acceptRun(await this.api.run(entry.entry_id, runId), generation); }
    catch (error) { if (this.active && generation === this.runGeneration) this.fail(error); }
    finally { this.polling = false; }
  }
  async refreshLatest() {
    const entry = this.state.entry;
    if (!entry) return;
    await this.perform(async () => {
      const latest = await this.api.get(entry.entry_id);
      this.update({ latest });
      if (latest.base_record_versions.length) this.update({ latestRecords: await this.api.baseRecords(latest.base_record_versions) });
      if (latest.confirmed_resources) {
        this.stopRun();
        this.update({ confirmed: latest.confirmed_resources, pendingConfirm: null, pendingSave: null });
      }
    });
    if (this.state.confirmed) await this.loadObservations();
  }
  async adoptLatest() {
    const latest = this.state.latest;
    if (!latest || this.state.busy || this.state.pendingConfirm) return;
    this.stopRun();
    this.adopt(latest);
    if (latest.normalization_run_id && !latest.confirmed_resources) await this.recoverRun();
  }
  async rebaseCorrection() {
    const { entry, latestRecords, source, content } = this.state;
    if (!entry?.supersedes_entry_id || !latestRecords || !this.canEdit()) return;
    // The saved correction's base versions are immutable. Make an explicitly requested
    // replacement correction against the same confirmed source, never a fresh CareEvent.
    this.update({ pendingSave: { id: null, body: { ...source, client_request_id: newClientRequestId(),
      episode_id: null, supersedes_entry_id: entry.supersedes_entry_id,
      base_record_versions: latestRecords.map((item) => item.record) } } });
    await this.save();
    if (!this.state.pendingSave && this.state.entry?.entry_id !== entry.entry_id) this.update({ content, mode: "MANUAL", reviewed: false });
  }
  async confirm() {
    const { entry, dirty, reviewed, mode, run, content } = this.state;
    if (!entry || dirty || this.state.confirmed || (this.state.latest && !this.state.pendingConfirm) || this.state.pendingSave) return;
    if (!this.state.pendingConfirm && (!reviewed || content.outcomes.length > 0 ||
      content.unresolved.some((item) => ["CONFLICT", "UNSUPPORTED_CODE", "MISSING_EVIDENCE"].includes(item.code)) ||
      (mode === "LLM" && (run?.status !== "COMPLETE" || run.input_revision !== entry.input_revision)))) return;
    await this.perform(async () => {
      const body = this.state.pendingConfirm ?? structuredClone({ client_request_id: newClientRequestId(),
        input_revision: entry.input_revision, normalization_mode: mode, run_id: mode === "LLM" ? run!.run_id : null,
        content, base_record_versions: entry.base_record_versions });
      this.update({ pendingConfirm: body });
      try {
        const confirmed = await this.api.confirm(entry.entry_id, body);
        this.stopRun();
        this.update({ confirmed, pendingConfirm: null, entries: this.state.entries.filter((item) => item.entry_id !== entry.entry_id) });
      } catch (error) {
        if (error instanceof ContractApiError && error.status < 500) this.update({ pendingConfirm: null });
        throw error;
      }
    });
    if (this.state.confirmed) await this.loadObservations();
  }
  async loadObservations() {
    const confirmed = this.state.confirmed;
    if (!confirmed) return;
    await this.perform(async () => {
      const observations = await Promise.all(confirmed.state_observation_ids.map((id) => this.api.observation(id)));
      this.update({ observations });
    });
  }
}
