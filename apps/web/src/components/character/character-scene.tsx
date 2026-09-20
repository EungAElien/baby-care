"use client";

import { useEffect, useState, type Dispatch } from "react";
import { LottieStage, type LottieAsset } from "./lottie-stage";
import { productionAsset, sourcePosters } from "./production-assets";
import { poseId, poseLabel, type Pose } from "./pose-catalog";
import { currentScene, type SceneEvent, type SceneState } from "./scene-state";
import "./character.css";

export function CharacterPoster({ pose, size = 64 }: { pose: Pose; size?: number }) {
  const asset = productionAsset(pose);
  const source = sourcePosters[poseId(pose)];
  const poster = asset?.poster ?? source;
  const logSize = size <= 56 ? 56 : size <= 64 ? 64 : 72;
  const logPoster = poster?.replace(/\/([^/]+)\.png$/, (_, file: string) => `/log/${file.replace(/-source$/, "")}-${logSize}.png`);
  return <span style={{ display: "inline-block", width: size, flexShrink: 0 }}>
    {size <= 72 && logPoster ? <SourcePoster src={logPoster} /> : asset ? <LottieStage asset={asset} animate={false} /> : source ? <SourcePoster src={source} /> : <span className="character-pending" aria-label={`${poseLabel(pose)} 그림 제작 중`}>제작 중</span>}
  </span>;
}

export function CharacterScene({ state, dispatch }: { state: SceneState; dispatch: Dispatch<SceneEvent> }) {
  const scene = currentScene(state);
  const activeAction = state.queue[0];
  const asset = productionAsset(scene.pose);
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => dispatch({ type: "environment", scope: state.scope, hidden: document.hidden, reduced: media.matches });
    sync();
    media.addEventListener("change", sync);
    document.addEventListener("visibilitychange", sync);
    const interval = window.setInterval(() => dispatch({ type: "tick", scope: state.scope, now: Date.now() }), 10_000);
    return () => { media.removeEventListener("change", sync); document.removeEventListener("visibilitychange", sync); window.clearInterval(interval); };
  }, [state.scope, dispatch]);

  return <section className="character-scene" aria-label="현재 장면">
    <div className="character-stage">
      <ScenePlayback key={`${state.scope}:${state.run}:${scene.pose.kind}:${scene.pose.code}`} asset={asset} sourcePoster={sourcePosters[poseId(scene.pose)]} state={state} action={Boolean(activeAction)} staticScene={scene.stale || Boolean(scene.system)} dispatch={dispatch} />
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

function SourcePoster({ src }: { src: string }) {
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt="" style={{ width: "100%", height: "auto" }} />;
}

function ScenePlayback({ asset, sourcePoster, state, action, staticScene, dispatch }: {
  asset: LottieAsset | undefined; sourcePoster?: string; state: SceneState; action: boolean; staticScene: boolean; dispatch: Dispatch<SceneEvent>;
}) {
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!action || state.hidden || document.hidden) return;
    const reduced = state.reduced || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (asset && !failed && !reduced) return;
    const timer = window.setTimeout(() => {
      if (!document.hidden) dispatch({ type: "finish", scope: state.scope, run: state.run });
    }, 2200);
    return () => window.clearTimeout(timer);
  }, [action, asset, failed, state.hidden, state.reduced, state.scope, state.run, dispatch]);
  if (!asset) return sourcePoster ? <SourcePoster src={sourcePoster} /> : <div className="character-pending" role="img" aria-label="이 범주의 전용 원본 제작 중">이 장면은 제작 중입니다</div>;
  return <LottieStage asset={asset} animate={!state.hidden && !state.reduced && !staticScene && !failed}
    onFailure={() => setFailed(true)} onComplete={() => dispatch({ type: "finish", scope: state.scope, run: state.run })} />;
}
