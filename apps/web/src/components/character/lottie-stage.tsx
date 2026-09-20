"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { AnimationItem } from "lottie-web";

export type LottieAsset = {
  id: string;
  animation: string;
  poster: string;
  fps: number;
  start: number;
  end: number;
  loop: boolean;
  representativeFrame: number;
  width: number;
  height: number;
};

const mediaQuery = "(prefers-reduced-motion: reduce)";
function subscribeMotion(listener: () => void) {
  const media = window.matchMedia(mediaQuery);
  media.addEventListener("change", listener);
  return () => media.removeEventListener("change", listener);
}
function reducedSnapshot() { return window.matchMedia(mediaQuery).matches; }
function serverSnapshot() { return true; }
type StageProps = { asset: LottieAsset; animate: boolean; frame?: number; onComplete?: () => void; onFailure?: () => void };

/** Plays an editor export. Artwork and keyframes belong to the asset, never CSS. */
export function LottieStage(props: StageProps) {
  const reduced = useSyncExternalStore(subscribeMotion, reducedSnapshot, serverSnapshot);
  if (props.frame === undefined && (!props.animate || reduced)) return <div data-lottie-id={props.asset.id} data-lottie-static="true" style={{ width: "100%", aspectRatio: `${props.asset.width} / ${props.asset.height}` }}>
    {/* eslint-disable-next-line @next/next/no-img-element */}
    <img src={props.asset.poster} alt="" style={{ width: "100%", height: "100%", objectFit: "contain" }} />
  </div>;
  return <AnimatedStage key={props.asset.id} {...props} />;
}

function AnimatedStage({ asset, animate, frame, onComplete, onFailure }: StageProps) {
  const host = useRef<HTMLDivElement>(null);
  const player = useRef<AnimationItem | null>(null);
  const callbacks = useRef({ onComplete, onFailure });
  const desired = useRef({ animate, frame });
  const [ready, setReady] = useState(false);
  useEffect(() => { callbacks.current = { onComplete, onFailure }; }, [onComplete, onFailure]);
  useEffect(() => {
    desired.current = { animate, frame };
    const instance = player.current;
    if (!instance) return;
    if (frame !== undefined) instance.goToAndStop(frame, true);
    else if (animate && !document.hidden && !window.matchMedia(mediaQuery).matches) instance.playSegments([asset.start, asset.end], true);
    else instance.goToAndStop(asset.representativeFrame, true);
  }, [animate, frame, asset.representativeFrame, asset.start, asset.end]);

  useEffect(() => {
    const abort = new AbortController();
    let disposed = false;
    let failed = false;
    let completed = false;
    let instance: AnimationItem | null = null;
    const fail = () => {
      if (disposed || failed) return;
      failed = true;
      window.clearTimeout(timeout);
      abort.abort();
      instance?.destroy();
      player.current = null;
      setReady(false);
      callbacks.current.onFailure?.();
    };
    const timeout = window.setTimeout(fail, 8000);
    const visibility = () => {
      if (document.hidden) instance?.pause();
      // The state controller decides what remains valid when the tab returns.
    };
    document.addEventListener("visibilitychange", visibility);
    void (async () => {
      try {
        const [module, response] = await Promise.all([
          import("lottie-web"), fetch(asset.animation, { signal: abort.signal }),
        ]);
        if (!response.ok) throw new Error("Animation asset could not be loaded");
        const data: unknown = await response.json();
        if (disposed || failed || !host.current) return;
        if (!data || typeof data !== "object" || !("layers" in data) || !Array.isArray(data.layers)) throw new Error("Invalid animation export");
        instance = module.default.loadAnimation({
          container: host.current, renderer: "svg", loop: asset.loop,
          autoplay: false, animationData: data,
          rendererSettings: { preserveAspectRatio: "xMidYMid meet", progressiveLoad: false },
        });
        player.current = instance;
        instance.addEventListener("data_failed", fail);
        instance.addEventListener("error", fail);
        instance.addEventListener("complete", () => {
          if (!disposed && !failed && !completed && !asset.loop && !document.hidden) {
            completed = true;
            callbacks.current.onComplete?.();
          }
        });
        instance.addEventListener("DOMLoaded", () => {
          if (disposed || failed || !instance) return;
          window.clearTimeout(timeout);
          setReady(true);
          const current = desired.current;
          if (current.frame !== undefined) instance.goToAndStop(current.frame, true);
          else if (current.animate && !document.hidden && !window.matchMedia(mediaQuery).matches) instance.playSegments([asset.start, asset.end], true);
          else instance.goToAndStop(asset.representativeFrame, true);
        });
      } catch { if (!disposed) fail(); }
    })();
    return () => {
      disposed = true;
      window.clearTimeout(timeout);
      abort.abort();
      document.removeEventListener("visibilitychange", visibility);
      if (!failed) instance?.destroy();
      player.current = null;
    };
  }, [asset]);

  return <div data-lottie-id={asset.id} data-lottie-ready={ready} style={{ position: "relative", width: "100%", aspectRatio: `${asset.width} / ${asset.height}` }}>
    {/* Same editor source supplies the loading/error/reduced-motion representative image. */}
    {/* eslint-disable-next-line @next/next/no-img-element */}
    <img src={asset.poster} alt="" style={{ width: "100%", height: "100%", objectFit: "contain", visibility: ready ? "hidden" : "visible" }} />
    <div ref={host} aria-hidden="true" style={{ position: "absolute", inset: 0, visibility: ready ? "visible" : "hidden" }} />
  </div>;
}
