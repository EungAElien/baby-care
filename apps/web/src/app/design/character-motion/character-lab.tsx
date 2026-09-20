"use client";

import { useReducer, useState } from "react";
import { CharacterPoster, CharacterScene } from "@/components/character/character-scene";
import { poseCatalog, poseLabel, poseId, type Pose, type ObservationCode, type InferenceCode } from "@/components/character/pose-catalog";
import { initialScene, sceneReducer } from "@/components/character/scene-state";

const log: { time: string; pose: Pose; detail: string }[] = [
  { time: "14:15", pose: { kind: "observation", code: "CRYING" }, detail: "보호자 관찰 · 조치 후에도 우는 중" },
  { time: "14:12", pose: { kind: "action", code: "patting" }, detail: "확인한 돌봄 · 수행자: 보호자 A (합성)" },
  { time: "14:10", pose: { kind: "action", code: "holding" }, detail: "확인한 돌봄 · 수행자: 보호자 A (합성)" },
  { time: "14:06", pose: { kind: "inference", code: "hungry" }, detail: "AI 추정 · 사건 01 · STUB" },
  { time: "14:00", pose: { kind: "observation", code: "CALM" }, detail: "보호자 관찰 · 사건 전" },
];

export function CharacterLab() {
  const [state, dispatch] = useReducer(sceneReducer, undefined, () => initialScene("demo-0", 0));
  const [tab, setTab] = useState<Pose["kind"]>("observation");
  const [lastSave, setLastSave] = useState<string | null>(null);
  const observe = (code: ObservationCode, old = false) => {
    const now = Date.now();
    if (old) {
      const scope = crypto.randomUUID();
      dispatch({ type: "reset", scope, now });
      dispatch({ type: "observation", scope, observation: { id: crypto.randomUUID(), version: 1, code, at: now - 31 * 60_000, source: "보호자 관찰 · 합성" } });
    } else dispatch({ type: "observation", scope: state.scope, observation: { id: crypto.randomUUID(), version: 1, code, at: now, source: "보호자 관찰 · 합성" } });
  };
  const analyze = (status: "COMPLETE" | "ABSTAIN" | "FAILED" | "NO_CRY", code?: InferenceCode) => {
    const id = crypto.randomUUID();
    dispatch({ type: "analysis-start", scope: state.scope, id, at: Date.now() });
    dispatch({ type: "analysis-result", scope: state.scope, id, status, code, supported: true });
  };
  const save = (success: boolean, replay = false) => {
    const requestId = replay ? lastSave : crypto.randomUUID();
    if (!requestId) return;
    if (!replay) { setLastSave(requestId); dispatch({ type: "save-start", scope: state.scope, requestId, context: "new" }); }
    dispatch({ type: "save-result", scope: state.scope, requestId, success, actions: [
      { id: `${requestId}-holding`, code: "holding", sequence: 1, confirmed: true },
      { id: `${requestId}-patting`, code: "patting", sequence: 2, confirmed: true },
    ] });
  };
  return <main className="character-lab">
    <header><span className="character-wordmark">아기 곁에</span><span className="eyebrow">Character studies / 01</span><span className="character-badge">DEMO · STUB · 합성 입력</span></header>
    <div style={{ paddingTop: 34 }}><p className="eyebrow">작은 표정, 함께하는 돌봄</p><h1>돌봄의 순간을<br />눈에 보이는 이야기로.</h1><p className="intro">관찰한 모습, AI가 추정한 가능성, 보호자가 완료한 돌봄을 각각 표현합니다. 아래 버튼은 디자인 검토용입니다. 실제 기록을 저장하거나 울음을 분석하지 않습니다. 현재 편안함·AI 추정 6종·안아주기의 실제 출력 8종을 제공합니다. 판단 어려움은 중립 정지 자세입니다. 나머지 14종은 제작 중입니다.</p><a href="/design/character-motion/production">시안·원본·실제 Lottie 비교 화면</a></div>
    <div className="character-workbench">
      <div className="character-preview"><CharacterScene state={state} dispatch={dispatch} /></div>
      <div className="character-controls">
        <h3>01 / 관찰에서 시작하기</h3><p>확인한 관찰이 홈의 기준입니다. 돌봄 후에도 우는 모습을 그대로 남길 수 있어요.</p>
        <div className="buttons"><button className="character-button" onClick={() => observe("CRYING")}>우는 중 관찰</button><button className="character-button" onClick={() => observe("CALM")}>편안한 모습 관찰</button><button className="character-button" onClick={() => observe("CALM", true)}>31분 전 관찰</button></div>
        <h3>02 / 가능성과 실제 행동 구별하기</h3>
        <div className="buttons"><button className="character-button" onClick={() => analyze("COMPLETE", "hungry")}>배고픔 추정</button><button className="character-button" onClick={() => analyze("COMPLETE", "tired")}>졸림 추정</button><button className="character-button" onClick={() => analyze("COMPLETE", "belly_pain")}>배 불편함 추정</button><button className="character-button primary" onClick={() => save(true)}>안기 → 토닥임 확인 저장</button></div>
        <h3>03 / 흐름과 예외 확인하기</h3>
        <div className="buttons"><button className="character-button" onClick={() => save(false)}>저장 실패</button><button className="character-button" onClick={() => save(true, true)}>같은 응답 재수신</button><button className="character-button" onClick={() => dispatch({ type: "analysis-start", scope: state.scope, id: crypto.randomUUID(), at: Date.now() })}>새 울음 사건</button><button className="character-button" onClick={() => analyze("ABSTAIN")}>판단 어려움</button><button className="character-button" onClick={() => analyze("FAILED")}>처리 실패</button><button className="character-button" onClick={() => analyze("NO_CRY")}>울음 확인 안 됨</button><button className="character-button" onClick={() => dispatch({ type: "reset", scope: crypto.randomUUID(), now: Date.now() })}>아기 전환</button><button className="character-button" onClick={() => dispatch({ type: "invalidate", scope: state.scope })}>삭제·권한 회수</button></div>
        <p>조치 순서와 중단을 합성 입력으로 시험합니다. 안아주기는 실제 완료 조치 Lottie를 1회 재생하고, 나머지 미제작 조치는 제작 중 안내를 표시합니다. 저장과 다음 입력은 기다리지 않습니다.</p>
      </div>
    </div>
    <section className="character-gallery"><p className="eyebrow">Pose library / 22 categories · 제작 상태</p><h2>같은 캐릭터, 서로 다른 의미</h2>
      <div className="character-tabs">{(["observation", "inference", "action"] as const).map((kind) => <button className="character-button" key={kind} aria-pressed={tab === kind} onClick={() => setTab(kind)}>{kind === "observation" ? "관찰 8종" : kind === "inference" ? "AI 추정 5종 + 판단 어려움" : "완료 조치 8종"}</button>)}</div>
      <div className="character-grid">{poseCatalog.filter((pose) => pose.kind === tab).map((pose) => <article className="character-tile" key={poseId(pose)}><div className="art"><CharacterPoster pose={pose} size={220} /></div><p>{poseLabel(pose)}</p><div style={{ width: 64, margin: "auto" }}><CharacterPoster pose={pose} /></div><small>원본별 제작 상태 · 로그 64px</small></article>)}</div>
      {tab === "action" && <p className="intro">안아주기는 실제 Lottie를 제공합니다. 나머지 완료 조치 7종은 제작·검수 중입니다.</p>}
    </section>
    <section className="character-log"><p className="eyebrow">Still moments / example episode</p><h2>돌봄 뒤에도 관찰은 별도로</h2><p className="intro">최근 기록부터 표시한 합성 예시입니다. 가지의 합류는 후속 관찰의 연결을 뜻합니다.</p><ol>{log.map((row) => <li className={row.pose.kind === "observation" ? "" : "branch"} key={row.time}><time>{row.time}</time><CharacterPoster pose={row.pose} /><div><p>{poseLabel(row.pose)}</p><small>{row.detail}</small></div></li>)}</ol></section>
  </main>;
}
