// @vitest-environment jsdom
import { StrictMode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { AudioMeasureScreen } from "@/app/babies/[babyId]/audio-measure/screen";

const fakes = vi.hoisted(() => ({
  start: vi.fn(), stop: vi.fn(), abort: vi.fn(), clearScope: vi.fn(), dispose: vi.fn(),
  scopeCleanup: null as (() => void) | null,
}));

vi.mock("@/components/app-providers", () => ({
  usePrivateScope: () => ({
    subscribe: () => () => {},
    snapshot: () => ({ generation: 0 }),
    registerCleanup: (cleanup: () => void) => {
      fakes.scopeCleanup = cleanup;
      return () => { fakes.scopeCleanup = null; };
    },
  }),
}));

vi.mock("@/lib/audio/audio-measurement", () => ({
  AudioMeasurementSession: class {
    private disposed = false;

    start = () => {
      if (!this.disposed) fakes.start();
    };
    stop = fakes.stop;
    abort = fakes.abort;
    clearScope = fakes.clearScope;
    dispose = () => {
      this.disposed = true;
      fakes.dispose();
    };
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
});

afterEach(() => { fakes.scopeCleanup = null; });

it("requires a click and wires hidden, page exit, scope, and unmount cleanup without restart", () => {
  const view = render(<AudioMeasureScreen />);
  expect(fakes.start).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "측정 시작" }));
  expect(fakes.start).toHaveBeenCalledTimes(1);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  fireEvent(document, new Event("visibilitychange"));
  expect(fakes.abort).toHaveBeenCalledTimes(1);
  fireEvent(window, new Event("pagehide"));
  expect(fakes.abort).toHaveBeenCalledTimes(2);
  fakes.scopeCleanup?.();
  expect(fakes.clearScope).toHaveBeenCalledTimes(1);
  view.unmount();
  expect(fakes.dispose).toHaveBeenCalledTimes(1);
  fireEvent(window, new Event("pagehide"));
  expect(fakes.abort).toHaveBeenCalledTimes(2);
  expect(fakes.start).toHaveBeenCalledTimes(1);
});

it("creates a usable session after Strict Mode's setup-cleanup-setup cycle", () => {
  const view = render(<StrictMode><AudioMeasureScreen /></StrictMode>);
  expect(screen.getByRole("status").textContent).toContain("idle");
  expect(fakes.start).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "측정 시작" }));
  expect(fakes.start).toHaveBeenCalledTimes(1);
  view.unmount();
  expect(fakes.dispose).toHaveBeenCalledTimes(2);
});
