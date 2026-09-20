// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AudioMeasurementSession, type AudioMeasurementState } from "@/lib/audio/audio-measurement";

const tracks: FakeTrack[] = [];
const recorders: FakeRecorder[] = [];
const nodes: FakeWorkletNode[] = [];

class FakeTrack {
  stop = vi.fn();
  private listeners = new Set<() => void>();
  addEventListener(_name: string, callback: () => void) { this.listeners.add(callback); }
  removeEventListener(_name: string, callback: () => void) { this.listeners.delete(callback); }
  end() { for (const callback of this.listeners) callback(); }
  listenerCount() { return this.listeners.size; }
}

class FakeRecorder {
  static instances = recorders;
  state: RecordingState = "inactive";
  mimeType = "audio/webm";
  ondataavailable: ((event: BlobEvent) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;
  start = vi.fn(() => { this.state = "recording"; });
  stop = vi.fn(() => {
    this.state = "inactive";
    queueMicrotask(() => {
      this.ondataavailable?.({ data: new Blob(["audio"], { type: this.mimeType }) } as BlobEvent);
      this.onstop?.();
    });
  });
  constructor(stream: MediaStream) { void stream; recorders.push(this); }
}

class FakeWorkletNode {
  port = { onmessage: null as ((event: MessageEvent) => void) | null, postMessage: vi.fn(), close: vi.fn() };
  connect = vi.fn();
  disconnect = vi.fn();
  constructor() { nodes.push(this); }
  report() {
    this.port.onmessage?.({ data: { samples: 24000, sampleRate: 48000, firstFrame: 4800, lastFrameExclusive: 28800 } } as MessageEvent);
  }
}

class FakeContext {
  state: AudioContextState = "running";
  audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) };
  destination = {};
  close = vi.fn().mockResolvedValue(undefined);
  resume = vi.fn().mockResolvedValue(undefined);
  createMediaStreamSource = vi.fn(() => ({ connect: vi.fn(), disconnect: vi.fn() }));
  createGain = vi.fn(() => ({ gain: { value: 1 }, connect: vi.fn(), disconnect: vi.fn() }));
}

let getUserMedia: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.useFakeTimers();
  tracks.length = 0;
  recorders.length = 0;
  nodes.length = 0;
  getUserMedia = vi.fn(async () => {
    const track = new FakeTrack();
    tracks.push(track);
    return { getTracks: () => [track] } as unknown as MediaStream;
  });
  Object.defineProperty(window, "isSecureContext", { configurable: true, value: true });
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia } });
  vi.stubGlobal("AudioContext", FakeContext);
  vi.stubGlobal("AudioWorkletNode", FakeWorkletNode);
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:short") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("A-02 audio measurement", () => {
  it("does not request permission until explicit start, and separates Worklet from recorder values", async () => {
    const updates: AudioMeasurementState[] = [];
    const session = new AudioMeasurementSession((state) => updates.push(state));
    expect(getUserMedia).not.toHaveBeenCalled();
    await session.start();
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(session.snapshot().status).toBe("listening");
    nodes[0]!.report();
    expect(session.snapshot().worklet).toEqual({ samples: 24000, sampleRate: 48000, firstAudioSecond: 0.1, lastAudioSecond: 0.6 });
    expect(session.snapshot().recorder).toBeNull();
    await vi.advanceTimersByTimeAsync(3000);
    expect(recorders[0]!.stop).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(4000);
    expect(session.snapshot().recorder?.bytes).toBe(5);
    expect(session.snapshot().recorder?.mimeType).toBe("audio/webm");
    expect(session.snapshot().recorder?.durationSeconds).toBeNull();
    session.stop();
    expect(tracks[0]!.stop).toHaveBeenCalled();
    expect(tracks[0]!.listenerCount()).toBe(0);
    expect(nodes[0]!.port.close).toHaveBeenCalled();
    expect(updates.at(-1)?.status).toBe("stopped");
  });

  it("reports permission denial and missing API without fabricated measurements", async () => {
    getUserMedia.mockRejectedValueOnce(new DOMException("Denied", "NotAllowedError"));
    const denied = new AudioMeasurementSession(() => {});
    await denied.start();
    expect(denied.snapshot().status).toBe("error");
    expect(denied.snapshot().reason).toContain("거부");
    expect(denied.snapshot().worklet).toBeNull();
    vi.stubGlobal("AudioWorkletNode", undefined);
    const missing = new AudioMeasurementSession(() => {});
    await missing.start();
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(missing.snapshot().reason).toContain("오디오 API");
  });

  it("ends an early user stop immediately and keeps only the measured short-clip metadata", async () => {
    const session = new AudioMeasurementSession(() => {});
    await session.start();
    session.stop();
    expect(tracks[0]!.stop).toHaveBeenCalledTimes(1);
    expect(nodes[0]!.port.close).toHaveBeenCalledTimes(1);
    expect(recorders[0]!.stop).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(4000);
    expect(session.snapshot().recorder).toEqual({ bytes: 5, mimeType: "audio/webm", durationSeconds: null });
    expect(session.snapshot().status).toBe("stopped");
  });

  it("cleans up on track end and scope change, with no automatic restart", async () => {
    const session = new AudioMeasurementSession(() => {});
    await session.start();
    nodes[0]!.report();
    tracks[0]!.end();
    await Promise.resolve();
    expect(session.snapshot().status).toBe("stopped");
    expect(recorders[0]!.stop).toHaveBeenCalled();
    expect(tracks[0]!.stop).toHaveBeenCalled();
    expect(nodes[0]!.port.close).toHaveBeenCalled();
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    session.clearScope();
    expect(session.snapshot().worklet).toBeNull();
    expect(session.snapshot().recorder).toBeNull();
    await vi.advanceTimersByTimeAsync(5000);
    expect(getUserMedia).toHaveBeenCalledTimes(1);
  });

  it("stops a late permission grant after the user cancels", async () => {
    let grant: ((value: MediaStream) => void) | undefined;
    getUserMedia.mockImplementationOnce(() => new Promise<MediaStream>((resolve) => { grant = resolve; }));
    const session = new AudioMeasurementSession(() => {});
    const starting = session.start();
    session.stop();
    const track = new FakeTrack();
    grant?.({ getTracks: () => [track] } as unknown as MediaStream);
    await starting;
    expect(track.stop).toHaveBeenCalled();
    expect(recorders).toHaveLength(0);
    expect(session.snapshot().status).toBe("stopped");
  });

  it("silently disposes an active capture and cannot restart the disposed session", async () => {
    const updates: AudioMeasurementState[] = [];
    const session = new AudioMeasurementSession((state) => updates.push(state));
    await session.start();
    const updateCount = updates.length;
    session.dispose();
    expect(session.snapshot().status).toBe("stopped");
    expect(tracks[0]!.stop).toHaveBeenCalledTimes(1);
    expect(nodes[0]!.port.close).toHaveBeenCalledTimes(1);
    expect(updates).toHaveLength(updateCount);
    await session.start();
    expect(getUserMedia).toHaveBeenCalledTimes(1);
  });
});
