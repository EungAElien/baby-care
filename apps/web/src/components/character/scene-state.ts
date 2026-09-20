import { type ActionCode, type InferenceCode, type ObservationCode, type Pose, neutralPose } from "./pose-catalog";

/** A view model for the isolated design preview, not an API response or persistence schema. */
export type Observation = { id: string; version: number; code: ObservationCode; at: number; source: string };
export type SavedAction = { id: string; code: ActionCode; sequence: number; confirmed: boolean; resultConfirmed?: boolean };
type Analysis = { id: string; at: number; status: "RUNNING" | "COMPLETE" | "ABSTAIN" | "FAILED" | "NO_CRY"; code?: InferenceCode };
export type SceneState = {
  scope: string; observation: Observation | null; analysis: Analysis | null;
  queue: SavedAction[]; consumed: string[]; pending: string[];
  run: number; now: number; hidden: boolean; reduced: boolean; notice: string;
};
export type SceneEvent =
  | { type: "reset"; scope: string; now: number }
  | { type: "observation"; scope: string; observation: Observation }
  | { type: "analysis-start"; scope: string; id: string; at: number }
  | { type: "analysis-result"; scope: string; id: string; status: Exclude<Analysis["status"], "RUNNING">; code?: InferenceCode; supported?: boolean }
  | { type: "save-start"; scope: string; requestId: string; context: "new" | "history" }
  | { type: "save-result"; scope: string; requestId: string; success: boolean; actions: SavedAction[] }
  | { type: "finish"; scope: string; run: number }
  | { type: "skip"; scope: string }
  | { type: "invalidate"; scope: string }
  | { type: "environment"; scope: string; hidden: boolean; reduced: boolean }
  | { type: "tick"; scope: string; now: number };

export function initialScene(scope: string, now: number): SceneState {
  return { scope, observation: null, analysis: null, queue: [], consumed: [], pending: [], run: 0, now, hidden: false, reduced: false, notice: "" };
}

export function sceneReducer(state: SceneState, event: SceneEvent): SceneState {
  if (event.type === "reset") return { ...initialScene(event.scope, event.now), hidden: state.hidden, reduced: state.reduced, run: state.run + 1 };
  if (event.scope !== state.scope) return state;
  switch (event.type) {
    case "observation": {
      const previous = state.observation;
      if (previous && (event.observation.at < previous.at || (event.observation.id === previous.id && event.observation.version <= previous.version))) return state;
      return { ...state, observation: event.observation, analysis: state.analysis && state.analysis.at >= event.observation.at ? state.analysis : null };
    }
    case "analysis-start":
      if ((state.observation && event.at <= state.observation.at) || (state.analysis && event.at <= state.analysis.at)) return state;
      return { ...state, analysis: { id: event.id, at: event.at, status: "RUNNING" }, queue: [], pending: [], run: state.run + 1, notice: "" };
    case "analysis-result":
      if (!state.analysis || state.analysis.id !== event.id || state.analysis.status !== "RUNNING" || (state.observation && state.observation.at >= state.analysis.at)) return state;
      return { ...state, analysis: event.status === "NO_CRY" ? null : {
        ...state.analysis, status: event.status === "COMPLETE" && (!event.supported || !event.code || event.code === "uncertain") ? "ABSTAIN" : event.status,
        code: event.code,
      }, notice: event.status === "NO_CRY" ? "울음 확인 안 됨 · 최근 관찰을 표시해요" : "" };
    case "save-start":
      if (event.context !== "new" || state.pending.includes(event.requestId)) return state;
      return { ...state, pending: [...state.pending, event.requestId] };
    case "save-result": {
      if (!state.pending.includes(event.requestId)) return state;
      const pending = state.pending.filter((id) => id !== event.requestId);
      if (!event.success) return { ...state, pending, notice: "저장하지 못했어요 · 완료 장면은 재생하지 않아요" };
      const seen = new Set(state.consumed);
      const queue = [...event.actions].sort((a, b) => a.sequence - b.sequence).filter((action) => {
        if (!action.confirmed || seen.has(action.id) || ((action.code === "burped" || action.code === "sleeping") && !action.resultConfirmed)) return false;
        seen.add(action.id);
        return true;
      });
      if (!queue.length) return { ...state, pending };
      return { ...state, pending, consumed: [...seen], analysis: null,
        queue: state.hidden ? [] : [...state.queue, ...queue],
        run: state.queue.length ? state.run : state.run + 1, notice: "확인한 돌봄 조치 · 아기 반응은 별도 관찰" };
    }
    case "finish":
      if (state.run !== event.run || !state.queue.length) return state;
      return { ...state, queue: state.queue.slice(1), run: state.run + 1 };
    case "skip": return { ...state, queue: [], run: state.run + 1 };
    case "invalidate": return { ...initialScene(`${state.scope}:invalidated`, state.now), hidden: state.hidden, reduced: state.reduced, consumed: state.consumed, run: state.run + 1, notice: "자료를 정리했어요 · 현재 유효한 관찰을 다시 확인해 주세요" };
    case "environment": return { ...state, hidden: event.hidden, reduced: event.reduced,
      queue: event.hidden ? [] : state.queue, pending: event.hidden ? [] : state.pending,
      run: event.hidden || event.reduced !== state.reduced ? state.run + 1 : state.run };
    case "tick": return { ...state, now: event.now };
  }
}

export function currentScene(state: SceneState): { pose: Pose; source: string; at: number | null; stale: boolean; system: string | null } {
  const action = state.queue[0];
  if (action) return { pose: { kind: "action", code: action.code }, source: "확인한 돌봄 조치", at: null, stale: false, system: null };
  const analysis = state.analysis;
  if (analysis) {
    const system = analysis.status === "RUNNING" ? "울음을 분석하고 있어요" : analysis.status === "FAILED" ? "처리 실패 · 다시 시도해 주세요" : null;
    return { pose: system ? neutralPose : { kind: "inference", code: analysis.status === "COMPLETE" ? analysis.code ?? "uncertain" : "uncertain" },
      source: system ? "시스템 안내" : "AI 추정", at: analysis.at, stale: false, system };
  }
  const observation = state.observation;
  return { pose: observation ? { kind: "observation", code: observation.code } : neutralPose,
    source: observation ? observation.source : "아직 상태 기록이 없어요", at: observation?.at ?? null,
    stale: Boolean(observation && state.now - observation.at > 30 * 60_000), system: null };
}
