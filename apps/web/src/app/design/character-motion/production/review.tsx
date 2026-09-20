"use client";

import { useRef, useState } from "react";
import { LottieStage } from "@/components/character/lottie-stage";
import { productionAssets, sourcePosters } from "@/components/character/production-assets";
import styles from "./review.module.css";

const scenes = [
  { id: "observation-CALM", label: "편안해 보임", source: "보호자 관찰", file: "observation-calm", middle: 30, blink: 43 },
  { id: "inference-hungry", label: "배고픔 가능성", source: "AI 추정 · 가능성", file: "ai-hungry", middle: 34, blink: 55 },
  { id: "action-holding", label: "안아줌", source: "완료한 돌봄 조치", file: "action-holding", middle: 20, blink: 30 },
] as const;

export function ProductionReview() {
  const [frame, setFrame] = useState<number | undefined>(0);
  const [size, setSize] = useState(320);
  const [selected, setSelected] = useState(0);
  const [run, setRun] = useState(0);
  const [exportStatus, setExportStatus] = useState("");
  const stage = useRef<HTMLDivElement>(null);
  const scene = scenes[selected] ?? scenes[0];
  const exported = Boolean(productionAssets[scene.id]);
  const asset = productionAssets[scene.id] ?? {
    id: scene.id, animation: "", poster: sourcePosters[scene.id]!, fps: 24,
    start: 0, end: 53, loop: false, representativeFrame: 0, width: 400, height: 400,
  };
  async function saveFrame() {
    const element = stage.current?.querySelector("svg");
    if (!element || frame === undefined) return;
    try {
      const svg = element.cloneNode(true) as SVGSVGElement;
      svg.setAttribute("width", String(asset.width)); svg.setAttribute("height", String(asset.height));
      const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg)], { type: "image/svg+xml" }));
      try {
        const source = new Image(); source.src = url; await source.decode();
        const canvas = document.createElement("canvas"); canvas.width = asset.width; canvas.height = asset.height;
        const context = canvas.getContext("2d"); if (!context) throw new Error("Canvas unavailable");
        context.drawImage(source, 0, 0, asset.width, asset.height);
        const link = document.createElement("a"); link.href = canvas.toDataURL("image/png"); link.download = `${scene.file}-frame-${frame}.png`; link.click();
        setExportStatus(`${frame}f PNG 저장 요청을 보냈습니다.`);
      } finally { URL.revokeObjectURL(url); }
    } catch { setExportStatus("프레임 저장에 실패했습니다. 재생 자산 로딩을 확인해 주세요."); }
  }
  return <main className={styles.page}><div className={styles.inner}>
    <p className={styles.eyebrow}>CHARACTER PRODUCTION / V2 · 제작·검수 중</p>
    <h1>시안에서 움직임까지</h1><p className={styles.status}>현재: 부위별 원본 3종 · 실제 Lottie 2종. 안아주기는 편집기 오류로 모션 미완료이며, 나머지 19종은 아직 제작하지 않았습니다.</p>
    <p>Figma 편집 원본 → Lottie Creator 키프레임·실제 내보내기 → lottie-web 5.13.0. 실제 API와 연결하지 않은 검토 화면입니다.</p>
    <div className={styles.buttons}>{scenes.map((item, index) => <button key={item.id} aria-pressed={selected === index} onClick={() => { setSelected(index); setFrame(0); setExportStatus(""); }}>{item.label}</button>)}</div>
    <div className={styles.workbench}>
      <div className={styles.stage}><div ref={stage} style={{ width: size, maxWidth: "100%" }}><LottieStage key={`${asset.id}-${run}`} asset={asset} animate={exported && frame === undefined} frame={exported ? frame : undefined} onComplete={() => setExportStatus("1회 재생 종료 · 돌봄의 효과를 판단하지 않습니다.")} /></div></div>
      <div className={styles.controls}><span className={styles.badge}>{scene.source}</span><h2>{scene.label}</h2><p>{exported ? `실제 JSON 내보내기 · 24fps · ${(asset.end / 24).toFixed(2)}초 · 반복 · 대표 프레임 0` : "Figma 정지 원본 · Creator 모션 내보내기 미완료"}</p><fieldset disabled={!exported} style={{ border: 0, padding: 0 }}>
        <div className={styles.buttons}><button aria-pressed={frame === undefined} onClick={() => { setRun(run + 1); setFrame(undefined); setExportStatus(""); }}>{asset.loop ? "반복 재생" : "1회 재생"}</button><button aria-pressed={frame === 0} onClick={() => setFrame(0)}>대표 프레임</button><button aria-pressed={frame === scene.middle} onClick={() => setFrame(scene.middle)}>동작 {scene.middle}f</button><button aria-pressed={frame === scene.blink} onClick={() => setFrame(scene.blink)}>눈 감김 {scene.blink}f</button><button aria-pressed={frame === asset.end - 1} onClick={() => setFrame(asset.end - 1)}>끝 {asset.end - 1}f</button></div>
        <label>검수 프레임 <input aria-label="검수 프레임" type="range" min={0} max={asset.end - 1} value={frame ?? 0} onChange={(event) => setFrame(Number(event.target.value))} /></label><output>{frame === undefined ? "재생 중" : `${frame}f`}</output>
        <p><label>홈 표시 크기 <select aria-label="홈 표시 크기" value={size} onChange={(event) => setSize(Number(event.target.value))}><option value={240}>240px</option><option value={280}>280px</option><option value={320}>320px</option></select></label></p>
        </fieldset><p>관찰과 완료 조치, AI 추정은 별개의 출처입니다. 재생 완료는 아기 진정이나 돌봄 성공을 뜻하지 않습니다.</p>
        <div className={styles.buttons}><button disabled={!exported || frame === undefined} onClick={() => void saveFrame()}>현재 프레임 PNG 저장</button>{exported && <a className={styles.download} href={asset.animation} download>Lottie JSON</a>}</div><p role="status">{exportStatus}</p>
      </div>
    </div>
    <h2>같은 크기로 비교한 원본</h2><p>왼쪽은 PNG 시안, 가운데는 실제 Figma 출력, 오른쪽은 윤곽 겹침입니다. 점수로 환산하지 않습니다.</p>
    {["observation-calm", "ai-hungry", "action-holding"].map((id) => <figure key={id} style={{ margin: "24px 0" }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={`/characters/2.0.0-review/${id}-comparison.png`} alt={`${id}: 시안, 편집 원본, 윤곽 겹침 비교`} style={{ maxWidth: "100%" }} />
    </figure>)}
    <h2>로그 크기 · 대표 정지 이미지</h2><div style={{ display: "flex", alignItems: "end", gap: 24 }}>{[56,64,72].map((width) => <figure key={width} style={{ margin: 0 }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={`/characters/2.0.0-review/log/${scene.file}-${width}.png`} alt={`${scene.label} 대표 정지 이미지`} width={width} /><figcaption>{width}px</figcaption>
    </figure>)}</div>
    <p><a href="/design/character-motion/lifecycle">상태 전환·중복 방지 검토</a> · <a href="/characters/2.0.0-review/manifest.json">22종 제작 상태와 자산 목록</a></p><p><a href="https://www.figma.com/design/etV160fUzbwxOEUwgK4g00">Figma 부위별 편집 원본</a> · <a href="https://creator.lottiefiles.com/?fileId=3ef2a7bd-3603-4157-ba93-6db315053602">실제 Creator 편집 프로젝트</a></p>
  </div></main>;
}
