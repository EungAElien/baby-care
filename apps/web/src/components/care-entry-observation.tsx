"use client";

import { useEffect, useState } from "react";
import type { Observation } from "@/lib/api/care-entries";
import { observationDisplay, stateLabels } from "@/lib/care-entries/content";

/** A-07 owns this text-only presentation; A-12 owns character assets and motion. */
export function CareEntryObservation({ observation }: { observation: Observation }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(timer);
  }, []);
  const display = observationDisplay(observation, now);
  return <section aria-label="확인된 상태 관찰" className="grid gap-2 rounded-lg border border-border p-4">
    <h3 className="font-medium">보호자가 확인한 관찰</h3>
    <p role="img" aria-label={`상태 표시: ${stateLabels[display.visual]}`}>{stateLabels[display.visual]}</p>
    <p>{observation.state_codes.map((code) => stateLabels[code]).join(" · ")}</p>
    <p className={display.stale ? "font-semibold" : "text-sm"}>{display.stale ? "오래된 상태 · " : ""}
      {display.minutes === null ? "관찰 시각 모름 · 현재 상태를 뜻하지 않아요" : `${display.minutes}분 전 관찰`}</p>
    <p className="text-xs text-muted-foreground">{observation.observed_at ?? "시각 미상"} · {observation.time_precision} ·
      {observation.observation_source === "SELF_REPORTED" ? "직접 관찰 보고" : "전해 들은 관찰"} ·
      {observation.confirmation_status === "USER_CORRECTED" ? "수정 후 확인" : "사용자 확인"} · {observation.data_origin}</p>
    <p className="text-xs text-muted-foreground">표현 기준 {observation.visual_mapping_version} · {observation.visual_state_code}</p>
  </section>;
}
