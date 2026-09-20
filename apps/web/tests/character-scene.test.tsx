// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CharacterScene, CharacterPoster } from "../src/components/character/character-scene";
import { initialScene, sceneReducer, type SceneState } from "../src/components/character/scene-state";

let reduced = false;
const cancel = vi.fn();
const animate = vi.fn();
let resolveAnimation: () => void;
function playing(): SceneState {
  let state = initialScene("scope", Date.now());
  state = sceneReducer(state, { type: "save-start", scope: "scope", requestId: "r", context: "new" });
  return sceneReducer(state, { type: "save-result", scope: "scope", requestId: "r", success: true, actions: [{ id: "a", code: "holding", sequence: 1, confirmed: true }] });
}
beforeEach(() => {
  vi.useFakeTimers();
  reduced = false;
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  cancel.mockReset();
  animate.mockReset();
  animate.mockImplementation(() => ({ cancel, finished: new Promise<void>((resolve) => { resolveAnimation = resolve; }) }));
  vi.stubGlobal("matchMedia", () => ({ matches: reduced, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  Element.prototype.animate = animate;
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("SVG motion lifecycle", () => {
  it("plays one action and dispatches only the matching completion", async () => {
    const dispatch = vi.fn();
    const state = playing();
    render(<CharacterScene state={state} dispatch={dispatch} />);
    expect(animate).toHaveBeenCalledTimes(1);
    await act(async () => { resolveAnimation(); });
    expect(dispatch).toHaveBeenCalledWith({ type: "finish", scope: "scope", run: state.run });
  });
  it("cancels playback on unmount and ignores an already scheduled completion", async () => {
    const dispatch = vi.fn();
    const view = render(<CharacterScene state={playing()} dispatch={dispatch} />);
    view.unmount();
    expect(cancel).toHaveBeenCalledOnce();
    await act(async () => { resolveAnimation(); });
    expect(dispatch.mock.calls.some(([event]) => event.type === "finish")).toBe(false);
  });
  it("reduced motion never starts WAAPI even before React observes the preference", () => {
    reduced = true;
    const dispatch = vi.fn();
    const state = playing();
    render(<CharacterScene state={state} dispatch={dispatch} />);
    expect(animate).not.toHaveBeenCalled();
    expect(screen.getByText("안아줌")).toBeTruthy();
    act(() => { vi.advanceTimersByTime(2200); });
    expect(dispatch).toHaveBeenCalledWith({ type: "finish", scope: "scope", run: state.run });
  });
  it("handles rejected finished promises without advancing the queue", async () => {
    animate.mockImplementation(() => ({ cancel, finished: Promise.reject(new DOMException("cancelled", "AbortError")) }));
    const dispatch = vi.fn();
    render(<CharacterScene state={playing()} dispatch={dispatch} />);
    await act(async () => { await Promise.resolve(); });
    expect(dispatch.mock.calls.some(([event]) => event.type === "finish")).toBe(false);
  });
  it("visibility changes request immediate cancellation and poster rendering has no controller", () => {
    const dispatch = vi.fn();
    const view = render(<CharacterScene state={playing()} dispatch={dispatch} />);
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    expect(dispatch).toHaveBeenCalledWith({ type: "environment", scope: "scope", hidden: true, reduced: false });
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    view.unmount();
    animate.mockClear();
    const poster = render(<CharacterPoster pose={{ kind: "action", code: "patting" }} />);
    expect(poster.container.querySelector("svg")?.getAttribute("data-idle")).toBe("false");
    expect(animate).not.toHaveBeenCalled();
  });
});
