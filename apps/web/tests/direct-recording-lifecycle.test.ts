import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DirectRecording } from "../src/lib/audio/recording";

let track: {
  stop: ReturnType<typeof vi.fn>;
  onended: (() => void) | null;
  onmute: (() => void) | null;
};
let page: EventTarget & { hidden: boolean };
let browser: EventTarget;
let getUserMedia: ReturnType<typeof vi.fn>;
class Recorder {
  static isTypeSupported = () => true;
  mimeType = "audio/webm";
  state = "inactive";
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;
  start() {
    this.state = "recording";
  }
  stop() {
    this.ondataavailable?.({ data: new Blob(["audio"]) });
    this.state = "inactive";
    this.onstop?.();
  }
}
beforeEach(() => {
  track = { stop: vi.fn(), onended: null, onmute: null };
  page = Object.assign(new EventTarget(), { hidden: false });
  browser = new EventTarget();
  getUserMedia = vi.fn(async () => ({ getTracks: () => [track] }));
  vi.stubGlobal("document", page);
  vi.stubGlobal("window", browser);
  vi.stubGlobal("navigator", { onLine: true, mediaDevices: { getUserMedia } });
  vi.stubGlobal("MediaRecorder", Recorder);
});
afterEach(() => vi.unstubAllGlobals());

describe("foreground-only direct recording", () => {
  it("reports permission denial with a recovery action and never reports recording", async () => {
    getUserMedia.mockRejectedValue(
      new DOMException("denied", "NotAllowedError"),
    );
    const state = vi.fn();
    await expect(new DirectRecording(state).start()).rejects.toThrow(
      "마이크 권한이 거부됐어요",
    );
    expect(state.mock.calls).toEqual([["requesting"]]);
  });
  it("stops a late permission stream after cancellation without using its audio", async () => {
    let allow!: (stream: unknown) => void;
    getUserMedia.mockReturnValue(
      new Promise((resolve) => {
        allow = resolve;
      }),
    );
    const state = vi.fn();
    const capture = new DirectRecording(state);
    const pending = capture.start();
    const rejected = expect(pending).rejects.toThrow("녹음을 취소");
    capture.abort();
    await rejected;
    allow({ getTracks: () => [track] });
    await vi.waitFor(() => expect(track.stop).toHaveBeenCalledOnce());
    expect(state).not.toHaveBeenCalledWith("recording");
  });
  it.each(["hidden", "offline", "ended", "muted"])(
    "discards interrupted audio after %s and releases the microphone",
    async (reason) => {
      const state = vi.fn();
      const capture = new DirectRecording(state);
      const pending = capture.start();
      const rejected = expect(pending).rejects.toThrow(/다시 녹음/);
      await vi.waitFor(() => expect(state).toHaveBeenCalledWith("recording"));
      if (reason === "hidden") {
        page.hidden = true;
        page.dispatchEvent(new Event("visibilitychange"));
      }
      if (reason === "offline") browser.dispatchEvent(new Event("offline"));
      if (reason === "ended") track.onended?.();
      if (reason === "muted") track.onmute?.();
      await rejected;
      expect(track.stop).toHaveBeenCalledOnce();
      expect(track.onended).toBeNull();
      expect(track.onmute).toBeNull();
    },
  );
  it("does not request microphone permission from an already hidden page", async () => {
    page.hidden = true;
    await expect(new DirectRecording().start()).rejects.toThrow(
      "화면을 다시 연",
    );
    expect(getUserMedia).not.toHaveBeenCalled();
  });
});
