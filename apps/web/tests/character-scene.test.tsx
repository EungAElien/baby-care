// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CharacterScene, CharacterPoster } from "../src/components/character/character-scene";
import { LottieStage } from "../src/components/character/lottie-stage";
import { productionAssets } from "../src/components/character/production-assets";
import { initialScene, sceneReducer, type SceneState } from "../src/components/character/scene-state";

const mocks = vi.hoisted(() => ({ load: vi.fn() }));
vi.mock("lottie-web", () => ({ default: { loadAnimation: mocks.load } }));
let reduced = false;
let players: ReturnType<typeof makePlayer>[] = [];
function makePlayer() {
  const events: Record<string, () => void> = {};
  return { events, addEventListener: vi.fn((name: string, callback: () => void) => { events[name] = callback; }),
    playSegments: vi.fn(), goToAndStop: vi.fn(), pause: vi.fn(), destroy: vi.fn() };
}
function playing(): SceneState {
  let state = initialScene("scope", Date.now());
  state = sceneReducer(state, { type: "save-start", scope: "scope", requestId: "r", context: "new" });
  return sceneReducer(state, { type: "save-result", scope: "scope", requestId: "r", success: true, actions: [{ id: "a", code: "holding", sequence: 1, confirmed: true }] });
}
async function flush() { await act(async () => { await vi.dynamicImportSettled(); }); }
async function loaded() { await flush(); act(() => players[0]?.events.DOMLoaded?.()); }
beforeEach(() => {
  vi.useFakeTimers(); reduced = false; players = [];
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  mocks.load.mockReset().mockImplementation(() => { const player = makePlayer(); players.push(player); return player; });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ layers: [] }) }));
  vi.stubGlobal("matchMedia", () => ({ matches: reduced, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("editor-export Lottie lifecycle", () => {
  it("keeps an uncertain inference still even when live motion is requested", async () => {
    render(<LottieStage asset={productionAssets["inference-uncertain"]!} animate />);
    await flush();
    expect(fetch).not.toHaveBeenCalled();
    expect(mocks.load).not.toHaveBeenCalled();
  });
  it("plays only the action segment and reports completion once", async () => {
    const dispatch = vi.fn(); const state = playing();
    render(<CharacterScene state={state} dispatch={dispatch} />); await loaded();
    expect(players[0]?.playSegments).toHaveBeenCalledWith([0, 53], true);
    act(() => { players[0]?.events.complete?.(); players[0]?.events.complete?.(); });
    expect(dispatch.mock.calls.filter(([event]) => event.type === "finish")).toEqual([[{ type: "finish", scope: "scope", run: state.run }]]);
  });
  it("destroys an old player and ignores completion after a scope change", async () => {
    const dispatch = vi.fn(); const view = render(<CharacterScene state={playing()} dispatch={dispatch} />); await loaded();
    const old = players[0]!;
    view.rerender(<CharacterScene state={initialScene("other-baby", Date.now())} dispatch={dispatch} />);
    expect(old.destroy).toHaveBeenCalledOnce(); act(() => old.events.complete?.());
    expect(dispatch.mock.calls.some(([event]) => event.type === "finish")).toBe(false);
  });
  it("uses the poster without fetching or starting motion when reduced motion is already enabled", async () => {
    reduced = true; const dispatch = vi.fn(); const state = playing();
    const view = render(<CharacterScene state={state} dispatch={dispatch} />); await flush();
    expect(fetch).not.toHaveBeenCalled(); expect(mocks.load).not.toHaveBeenCalled();
    expect(view.container.querySelector("img")?.getAttribute("src")).toContain("action-holding.png");
    act(() => vi.advanceTimersByTime(2200));
    expect(dispatch).toHaveBeenCalledWith({ type: "finish", scope: "scope", run: state.run });
  });
  it("falls back to the same poster on failure and returns without trapping the queue", async () => {
    vi.mocked(fetch).mockRejectedValue(new Error("offline")); const dispatch = vi.fn();
    const view = render(<CharacterScene state={playing()} dispatch={dispatch} />); await flush();
    expect(view.container.querySelector("img")?.getAttribute("src")).toContain("action-holding.png");
    act(() => vi.advanceTimersByTime(2200));
    expect(dispatch.mock.calls.filter(([event]) => event.type === "finish")).toHaveLength(1);
  });
  it("pauses immediately when hidden and ignores a hidden completion", async () => {
    const dispatch = vi.fn(); render(<CharacterScene state={playing()} dispatch={dispatch} />); await loaded();
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    act(() => { document.dispatchEvent(new Event("visibilitychange")); players[0]?.events.complete?.(); });
    expect(players[0]?.pause).toHaveBeenCalledOnce();
    expect(dispatch).toHaveBeenCalledWith({ type: "environment", scope: "scope", hidden: true, reduced: false });
    expect(dispatch.mock.calls.some(([event]) => event.type === "finish")).toBe(false);
  });
  it("does not create a player for logs or an unfinished category", async () => {
    const view = render(<CharacterPoster pose={{ kind: "observation", code: "CALM" }} />); await flush();
    expect(view.container.querySelector("svg")).toBeNull(); expect(fetch).not.toHaveBeenCalled();
    view.rerender(<CharacterPoster pose={{ kind: "action", code: "patting" }} />);
    expect(screen.getByLabelText("토닥여줌 그림 제작 중")).toBeTruthy();
  });
  it("aborts an in-flight asset and never creates a player after unmount", async () => {
    let resolve!: (response: Response) => void;
    vi.mocked(fetch).mockImplementation(() => new Promise<Response>((done) => { resolve = done; }));
    const view = render(<LottieStage asset={productionAssets["observation-CALM"]!} animate />);
    const options = vi.mocked(fetch).mock.calls[0]?.[1]; view.unmount();
    expect(options?.signal?.aborted).toBe(true);
    await act(async () => resolve({ ok: true, json: async () => ({ layers: [] }) } as Response)); await flush();
    expect(mocks.load).not.toHaveBeenCalled();
  });
  it("does not turn a repeating observation into a care completion", async () => {
    const onComplete = vi.fn();
    render(<LottieStage asset={productionAssets["observation-CALM"]!} animate onComplete={onComplete} />); await loaded();
    act(() => players[0]?.events.complete?.()); expect(onComplete).not.toHaveBeenCalled();
  });
});
