import { describe, expect, it } from "vitest";
import { currentScene, initialScene, sceneReducer, type SceneState, type SavedAction } from "../src/components/character/scene-state";

const scope = "user-a/baby-a/visit-1";
const now = 1_000_000;
const action: SavedAction = { id: "action-1", code: "holding", sequence: 1, confirmed: true };
function observed(): SceneState {
  return sceneReducer(initialScene(scope, now), { type: "observation", scope, observation: { id: "o1", version: 1, code: "CRYING", at: now, source: "보호자 관찰" } });
}
function saved(state = observed(), actions = [action], requestId = "request-1") {
  state = sceneReducer(state, { type: "save-start", scope, requestId, context: "new" });
  return sceneReducer(state, { type: "save-result", scope, requestId, success: true, actions });
}

describe("character presentation lifecycle", () => {
  it("does not infer calm after holding; returns to the latest observation", () => {
    let state = saved();
    expect(currentScene(state).pose).toEqual({ kind: "action", code: "holding" });
    state = sceneReducer(state, { type: "observation", scope, observation: { id: "o2", version: 1, code: "FUSSING", at: now + 10, source: "보호자 관찰" } });
    state = sceneReducer(state, { type: "finish", scope, run: state.run });
    expect(currentScene(state).pose).toEqual({ kind: "observation", code: "FUSSING" });
  });
  it("orders actions by confirmed sequence and deduplicates even within a response", () => {
    const state = saved(observed(), [{ ...action, id: "action-2", code: "patting", sequence: 2 }, action, action]);
    expect(state.queue.map((item) => item.id)).toEqual(["action-1", "action-2"]);
  });
  it("replay, polling, reload and historical saves cannot start animation", () => {
    const event = { type: "save-result" as const, scope, requestId: "request-1", success: true, actions: [action] };
    expect(sceneReducer(observed(), event).queue).toHaveLength(0);
    const history = sceneReducer(observed(), { type: "save-start", scope, requestId: "request-1", context: "history" });
    expect(sceneReducer(history, event).queue).toHaveLength(0);
    let state = saved();
    state = sceneReducer(state, { type: "skip", scope });
    expect(sceneReducer(state, event).queue).toHaveLength(0);
    expect(saved(state, [action], "new-request-same-action").queue).toHaveLength(0);
  });
  it("failure, recommendations and unconfirmed outcomes never imply completed care", () => {
    let state = sceneReducer(observed(), { type: "save-start", scope, requestId: "r", context: "new" });
    state = sceneReducer(state, { type: "save-result", scope, requestId: "r", success: false, actions: [action] });
    expect(state.queue).toHaveLength(0);
    expect(saved(observed(), [{ ...action, confirmed: false }]).queue).toHaveLength(0);
    for (const code of ["burped", "sleeping"] as const) {
      expect(saved(observed(), [{ ...action, code }]).queue).toHaveLength(0);
      expect(saved(observed(), [{ ...action, code, resultConfirmed: true }]).queue).toHaveLength(1);
    }
  });
  it("new episodes cancel queues and pending saves; cancelled completion cannot advance another run", () => {
    const old = saved();
    let state = sceneReducer(old, { type: "analysis-start", scope, id: "e2", at: now + 10 });
    expect(state.queue).toHaveLength(0);
    expect(currentScene(state).system).toContain("분석");
    state = saved(state, [{ ...action, id: "a3" }], "r3");
    expect(sceneReducer(state, { type: "finish", scope, run: old.run })).toBe(state);
  });
  it("late inference cannot overwrite a newer observation", () => {
    let state = sceneReducer(observed(), { type: "analysis-start", scope, id: "e1", at: now + 1 });
    state = sceneReducer(state, { type: "observation", scope, observation: { id: "o2", version: 1, code: "CRYING", at: now + 20, source: "후속 관찰" } });
    state = sceneReducer(state, { type: "analysis-result", scope, id: "e1", status: "COMPLETE", code: "hungry", supported: true });
    expect(currentScene(state).pose).toEqual({ kind: "observation", code: "CRYING" });
  });
  it("only the current supported COMPLETE selects a candidate pose", () => {
    const running = sceneReducer(observed(), { type: "analysis-start", scope, id: "e1", at: now + 1 });
    const unsupported = sceneReducer(running, { type: "analysis-result", scope, id: "e1", status: "COMPLETE", code: "hungry", supported: false });
    expect(currentScene(unsupported).pose).toEqual({ kind: "inference", code: "uncertain" });
    expect(sceneReducer(running, { type: "analysis-result", scope, id: "old", status: "COMPLETE", code: "hungry", supported: true })).toBe(running);
    const supported = sceneReducer(running, { type: "analysis-result", scope, id: "e1", status: "COMPLETE", code: "hungry", supported: true });
    expect(currentScene(supported).pose).toEqual({ kind: "inference", code: "hungry" });
    expect(supported.observation).toEqual(running.observation);
  });
  it("keeps FAILED, ABSTAIN and NO_CRY distinct", () => {
    const running = sceneReducer(observed(), { type: "analysis-start", scope, id: "e1", at: now + 1 });
    const failure = sceneReducer(running, { type: "analysis-result", scope, id: "e1", status: "FAILED" });
    expect(currentScene(failure).system).toContain("실패");
    const abstain = sceneReducer(running, { type: "analysis-result", scope, id: "e1", status: "ABSTAIN" });
    expect(currentScene(abstain).pose).toEqual({ kind: "inference", code: "uncertain" });
    const noCry = sceneReducer(running, { type: "analysis-result", scope, id: "e1", status: "NO_CRY" });
    expect(currentScene(noCry).pose).toEqual({ kind: "observation", code: "CRYING" });
    expect(noCry.notice).toContain("울음 확인 안 됨");
  });
  it("scope reset and invalidation reject all old callbacks", () => {
    const old = saved();
    for (const state of [sceneReducer(old, { type: "reset", scope: "user-a/baby-b/visit-2", now }), sceneReducer(old, { type: "invalidate", scope })]) {
      expect(state.queue).toHaveLength(0);
      expect(state.observation).toBeNull();
      expect(sceneReducer(state, { type: "finish", scope, run: old.run })).toBe(state);
      expect(sceneReducer(state, { type: "observation", scope, observation: observed().observation! })).toBe(state);
    }
  });
  it("does not catch up hidden actions, and preserves reduced-motion poster sequence", () => {
    const hidden = sceneReducer(saved(), { type: "environment", scope, hidden: true, reduced: false });
    expect(hidden.queue).toHaveLength(0);
    const visible = sceneReducer(hidden, { type: "environment", scope, hidden: false, reduced: false });
    expect(saved(visible).queue).toHaveLength(0);
    const reduced = sceneReducer(saved(), { type: "environment", scope, hidden: false, reduced: true });
    expect(reduced.queue).toHaveLength(1);
  });
  it("uses no-record text and marks old observations stale without inventing new state", () => {
    expect(currentScene(initialScene(scope, now)).source).toBe("아직 상태 기록이 없어요");
    const state = sceneReducer(observed(), { type: "tick", scope, now: now + 31 * 60_000 });
    expect(currentScene(state).stale).toBe(true);
    expect(currentScene(state).pose).toEqual({ kind: "observation", code: "CRYING" });
  });
});
