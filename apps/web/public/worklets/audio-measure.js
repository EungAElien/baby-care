class AudioMeasureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.samples = 0;
    this.firstFrame = null;
    this.lastFrameExclusive = null;
    this.lastReportFrame = 0;
    this.port.onmessage = (event) => {
      if (event.data === "snapshot") this.report();
    };
  }

  report() {
    this.port.postMessage({
      samples: this.samples,
      firstFrame: this.firstFrame,
      lastFrameExclusive: this.lastFrameExclusive,
      sampleRate,
    });
  }

  process(inputs, outputs) {
    // No PCM leaves the worklet. The output is silent so the graph stays active.
    for (const channel of outputs[0] ?? []) channel.fill(0);
    const frames = inputs[0]?.[0]?.length ?? 0;
    if (frames > 0) {
      if (this.firstFrame === null) this.firstFrame = currentFrame;
      this.samples += frames;
      this.lastFrameExclusive = currentFrame + frames;
      if (currentFrame - this.lastReportFrame >= sampleRate / 4) {
        this.lastReportFrame = currentFrame;
        this.report();
      }
    }
    return true;
  }
}

registerProcessor("audio-measure", AudioMeasureProcessor);
