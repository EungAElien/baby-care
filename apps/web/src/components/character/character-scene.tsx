"use client";

import { useEffect, useRef, type Dispatch } from "react";
import { CharacterArt } from "./character-art";
import { poseLabel, type Pose } from "./pose-catalog";
import { currentScene, type SceneEvent, type SceneState } from "./scene-state";
import "./character.css";

export function CharacterPoster({ pose, size = 64 }: { pose: Pose; size?: number }) {
  return <span style={{ display: "inline-block", width: size, flexShrink: 0 }}><CharacterArt pose={pose} decorative /></span>;
}

export function CharacterScene({ state, dispatch }: { state: SceneState; dispatch: Dispatch<SceneEvent> }) {
  const root = useRef<HTMLDivElement>(null);
  const scene = currentScene(state);
  const activeAction = state.queue[0];
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => dispatch({ type: "environment", scope: state.scope, hidden: document.hidden, reduced: media.matches });
    sync();
    media.addEventListener("change", sync);
    document.addEventListener("visibilitychange", sync);
    const interval = window.setInterval(() => dispatch({ type: "tick", scope: state.scope, now: Date.now() }), 10_000);
    return () => { media.removeEventListener("change", sync); document.removeEventListener("visibilitychange", sync); window.clearInterval(interval); };
  }, [state.scope, dispatch]);

  useEffect(() => {
    if (!activeAction || state.hidden || document.hidden) return;
    // Consult the browser directly too: never start motion before the preference effect updates state.
    const reduced = state.reduced || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let valid = true;
    const finish = () => { if (valid) dispatch({ type: "finish", scope: state.scope, run: state.run }); };
    const targets = root.current?.querySelectorAll<SVGElement>("[data-motion]");
    const animations: Animation[] = [];
    let timer: number | undefined;
    if (reduced || !targets?.length || typeof Element.prototype.animate !== "function") {
      // Give the same representative poster reading time, without any motion or save delay.
      timer = window.setTimeout(finish, 2200);
    } else {
      try {
        targets.forEach((element) => {
          const frames = element.dataset.motion === "contact"
            ? [{ transform: "rotate(-2deg)" }, { transform: "rotate(0deg)", offset: .75 }, { transform: "rotate(0deg)" }]
            : [{ transform: "translateY(0)" }, { transform: "translateY(-4px)", offset: .3 }, { transform: "translateY(0)", offset: .45 }, { transform: "translateY(-4px)", offset: .6 }, { transform: "translateY(0)", offset: .75 }, { transform: "translateY(0)" }];
          animations.push(element.animate(frames, { duration: 2200, iterations: 1, easing: "ease-in-out" }));
        });
        void Promise.all(animations.map((animation) => animation.finished)).then(finish, () => { /* Cancellation never advances the queue. */ });
      } catch {
        animations.forEach((animation) => animation.cancel());
        timer = window.setTimeout(finish, 2200);
      }
    }
    return () => { valid = false; window.clearTimeout(timer); animations.forEach((animation) => animation.cancel()); };
  }, [activeAction, state.run, state.scope, state.hidden, state.reduced, dispatch]);

  return <section className="character-scene" aria-label="현재 장면">
    <div className="character-stage" ref={root}>
      <CharacterArt pose={scene.pose} decorative animated={!state.hidden && !state.reduced && !scene.stale && !scene.system && scene.pose.kind !== "action"} />
    </div>
    <div className="character-caption" aria-live="polite" aria-atomic="true">
      <span className={`character-badge ${scene.pose.kind}`}>{scene.source}</span>
      <h2>{scene.system ?? (state.observation || state.analysis || activeAction ? poseLabel(scene.pose) : "관찰을 기다리고 있어요")}</h2>
      {scene.at !== null && <p><time dateTime={new Date(scene.at).toISOString()}>{new Date(scene.at).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })}</time>{scene.stale && ` · ${Math.floor((state.now - scene.at) / 60_000)}분 전 관찰 · 현재 상태와 다를 수 있어요`}</p>}
      {state.notice && <p>{state.notice}</p>}
    </div>
    {activeAction && <button className="character-button" onClick={() => dispatch({ type: "skip", scope: state.scope })}>남은 장면 건너뛰기</button>}
  </section>;
}
