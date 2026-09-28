import type { components } from "@/lib/api/generated";
import type { Content, Source, Observation } from "@/lib/api/care-entries";

export const actionLabels = { FEEDING: "수유", DIAPER_CHECK: "기저귀 확인", DIAPER_CHANGE: "기저귀 교체", HOLDING: "안아주기", BURPING: "트림", SLEEP_PREPARATION: "수면 준비", ENVIRONMENT_ADJUSTMENT: "환경 조절", OTHER: "기타" } as const;
export const assertionLabels = { PERFORMED: "수행했어요", PLANNED: "계획이에요", NEGATED: "하지 않았어요", UNCERTAIN: "불확실해요" } as const;
export const stateLabels = { CRYING: "울고 있어요", FUSSING: "보채요", CALM: "차분해 보여요", SLEEPY_APPEARING: "졸려 보여요", ASLEEP: "자고 있어요", AWAKE: "깨어 있어요", CHEERFUL_APPEARING: "기분 좋아 보여요", UNKNOWN: "알 수 없어요", NEUTRAL: "중립 표시" } as const;
export const emptyContent = (): Content => ({ actions: [], states: [], outcomes: [], caregiver_interpretations: [], unresolved: [] });
export const emptySource = (): Source => ({ input_mode: "TEXT", raw_text: "", choices: [], occurred_at: null, time_precision: "UNKNOWN" });
export const correction = (quote: string): components["schemas"]["Evidence"] => ({ source: "USER_CORRECTION", quote, choice_id: null, span_start: null, span_end: null });

/** DOM selection offsets are UTF-16; the API uses Unicode code points, not graphemes. */
export function textEvidence(text: string, start: number, end: number): components["schemas"]["Evidence"] {
  if (start < 0 || end <= start || end > text.length || !Number.isInteger(start) || !Number.isInteger(end)) throw new Error("근거 범위를 선택해 주세요.");
  const boundary = (offset: number) => offset === 0 || offset === text.length ||
    !(text.charCodeAt(offset - 1) >= 0xd800 && text.charCodeAt(offset - 1) <= 0xdbff && text.charCodeAt(offset) >= 0xdc00 && text.charCodeAt(offset) <= 0xdfff);
  if (!boundary(start) || !boundary(end)) throw new Error("문자 중간을 근거로 선택할 수 없어요.");
  return { source: "TEXT", choice_id: null, span_start: Array.from(text.slice(0, start)).length,
    span_end: Array.from(text.slice(0, end)).length, quote: text.slice(start, end) };
}

export function ruleContent(source: Source): Content {
  const content = emptyContent();
  source.choices.forEach((choice) => {
    const evidence: components["schemas"]["Evidence"][] = [{ source: "CHOICE", choice_id: choice.choice_id, span_start: null, span_end: null, quote: null }];
    if (choice.kind === "ACTION" && choice.code in actionLabels && choice.assertion) {
      content.actions.push({ action_ref: choice.choice_id, action_code: choice.code as keyof typeof actionLabels,
        assertion: choice.assertion, performed_by_user_id: null, occurred_at: source.occurred_at,
        time_precision: source.time_precision, relative_time: null, sequence: content.actions.length + 1,
        amount: null, unit: null, feeding_mode: choice.code === "FEEDING" ? "UNSPECIFIED" : null, evidence });
    }
    if (choice.kind === "STATE" && choice.code in stateLabels && choice.code !== "NEUTRAL") {
      content.states.push({ state_codes: [choice.code as components["schemas"]["DraftState"]["state_codes"][number]],
        phase: "UNRELATED", observed_at: source.occurred_at, time_precision: source.time_precision, linked_action_refs: [], evidence });
    }
  });
  return content;
}

export function observationDisplay(observation: Observation, now: number) {
  const codes = observation.state_codes;
  const neutral = codes.includes("UNKNOWN") || (codes.includes("CRYING") && codes.includes("CALM")) ||
    (codes.includes("ASLEEP") && codes.includes("AWAKE")) || observation.visual_mapping_version !== "care-visual-v1";
  const age = observation.observed_at === null ? null : now - Date.parse(observation.observed_at);
  return { visual: neutral ? "NEUTRAL" as const : observation.visual_state_code,
    minutes: age !== null && Number.isFinite(age) ? Math.max(0, Math.floor(age / 60000)) : null,
    stale: age !== null && age > 30 * 60000 };
}
