"use client";
import { ActionButton } from "./seed-design/ui/action-button";


import type { Content } from "@/lib/api/care-entries";
import type { components } from "@/lib/api/generated";
import { actionLabels, assertionLabels, stateLabels, correction } from "@/lib/care-entries/content";

export const entryControl = "min-h-11 rounded-md border border-border bg-background px-3 py-2 text-sm disabled:opacity-50";
export function EntrySelect<T extends string>({ label, value, options, onChange }: {
  label: string; value: T; options: Readonly<Record<T, string>>; onChange: (value: T) => void;
}) {
  return <label className="flex flex-col gap-1 text-sm">{label}<select className={entryControl} value={value}
    onChange={(event) => onChange(event.target.value as T)}>
    {Object.entries<string>(options).map(([key, text]) => <option key={key} value={key}>{text}</option>)}
  </select></label>;
}

function Evidence({ items }: { items: components["schemas"]["Evidence"][] }) {
  return <ul className="text-xs text-muted-foreground">{items.map((item, index) => <li key={index}>
    근거: {item.source === "TEXT" ? `원문 [${item.span_start}, ${item.span_end}) · ${item.quote}`
      : item.source === "CHOICE" ? `선택지 ${item.choice_id}` : `사용자 수정 · ${item.quote}`}
  </li>)}</ul>;
}

export function CareEntryContent({ content, onChange, disabled, fields }: {
  content: Content; onChange: (content: Content) => void; disabled: boolean;
  fields: readonly { field: string; message: string }[];
}) {
  const errors = (prefix: string) => fields.filter((item) => item.field.replace(/^content\./, "").startsWith(prefix))
    .map((item) => <p role="alert" className="text-sm text-destructive" key={item.field}>{item.field}: {item.message}</p>);
  const action = (index: number, patch: Partial<Content["actions"][number]>) => {
    const previous = content.actions[index];
    if (!previous) return;
    const next = { ...previous, ...patch };
    next.evidence = [correction(`${actionLabels[next.action_code]} · ${assertionLabels[next.assertion]} · ${next.amount ?? "양 모름"} ${next.unit ?? ""}`)];
    onChange({ ...content, actions: content.actions.map((item, position) => position === index ? next : item) });
  };
  return <fieldset disabled={disabled} className="flex flex-col gap-4">
    <legend className="font-semibold">정규화 제안 · 확인 전 초안</legend>
    <p className="text-sm text-muted-foreground">계획·부정·불확실은 수행 기록으로 저장되지 않아요. 관찰과 해석은 원인 정답이 아니에요.</p>
    <h3 className="font-medium">행동</h3>
    {content.actions.map((item, index) => <section aria-label={`행동 ${index + 1}`} className="grid gap-2 rounded-lg border p-3" key={item.action_ref}>
      <EntrySelect label="행동 종류" value={item.action_code} options={actionLabels} onChange={(action_code) => action(index, { action_code, amount: null, unit: null, feeding_mode: action_code === "FEEDING" ? "UNSPECIFIED" : null })} />
      <EntrySelect label="행동의 의미" value={item.assertion} options={assertionLabels} onChange={(assertion) => action(index, { assertion })} />
      {item.action_code === "FEEDING" && <>
        <EntrySelect label="수유 방식" value={item.feeding_mode ?? "UNSPECIFIED"} options={{ BREAST: "모유", FORMULA: "분유", MIXED: "혼합", UNSPECIFIED: "모름" }} onChange={(feeding_mode) => action(index, { feeding_mode, amount: null, unit: null })} />
        <label className="text-sm">수유량 (빈칸은 모름)<input className={`${entryControl} w-full`} type="number" min="0" value={item.amount ?? ""}
          onChange={(event) => action(index, { amount: event.target.value === "" ? null : Number(event.target.value), unit: event.target.value === "" ? null : item.feeding_mode === "BREAST" ? "MINUTES" : "ML" })} /></label>
        <p className="text-xs">단위: {item.unit ?? "모름"}</p>
      </>}
      <label className="text-sm">행동 시각 (시간대 포함, 빈칸은 모름)<input className={`${entryControl} w-full`} value={item.occurred_at ?? ""} placeholder="2026-09-20T09:00:00+09:00"
        onChange={(event) => action(index, { occurred_at: event.target.value || null, time_precision: event.target.value ? "EXACT" : "UNKNOWN", relative_time: null })} /></label>
      {item.relative_time && <p>원문의 상대 시각: {item.relative_time}</p>}
      <Evidence items={item.evidence} />{errors(`actions.${index}`)}{errors(`actions.${item.action_ref}`)}
      <ActionButton variant="neutralWeak" type="button" className={entryControl} onClick={() => onChange({ ...content, actions: content.actions.filter((_, position) => position !== index) })}>이 행동 제외</ActionButton>
    </section>)}
    <ActionButton variant="neutralWeak" type="button" className={entryControl} onClick={() => onChange({ ...content, actions: [...content.actions, {
      action_ref: crypto.randomUUID(), action_code: "HOLDING", assertion: "UNCERTAIN", performed_by_user_id: null,
      occurred_at: null, relative_time: null, time_precision: "UNKNOWN", sequence: Math.max(0, ...content.actions.map((item) => item.sequence)) + 1,
      amount: null, unit: null, feeding_mode: null, evidence: [correction("안아주기 여부 불확실 — 직접 추가")],
    }] })}>행동 직접 추가</ActionButton>
    <h3 className="font-medium">상태 관찰 · 아직 확인되지 않았어요</h3>
    {content.states.map((item, index) => <section aria-label={`상태 관찰 ${index + 1}`} key={index} className="grid gap-2 rounded-lg border p-3">
      <p>{item.state_codes.map((code) => stateLabels[code]).join(" · ")}</p>
      <EntrySelect label="관찰한 상태로 수정" value={item.state_codes[0] ?? "UNKNOWN"}
        options={{ CRYING: stateLabels.CRYING, FUSSING: stateLabels.FUSSING, CALM: stateLabels.CALM, SLEEPY_APPEARING: stateLabels.SLEEPY_APPEARING, ASLEEP: stateLabels.ASLEEP, AWAKE: stateLabels.AWAKE, CHEERFUL_APPEARING: stateLabels.CHEERFUL_APPEARING, UNKNOWN: stateLabels.UNKNOWN }}
        onChange={(code) => onChange({ ...content, states: content.states.map((state, position) => index === position ? { ...state, state_codes: [code], evidence: [correction(stateLabels[code])] } : state) })} />
      <label className="text-sm">관찰 시각 (시간대 포함, 빈칸은 모름)<input className={`${entryControl} w-full`} value={item.observed_at ?? ""}
        onChange={(event) => onChange({ ...content, states: content.states.map((state, position) => index === position ? { ...state, observed_at: event.target.value || null, time_precision: event.target.value ? "EXACT" : "UNKNOWN", evidence: [correction(`${state.state_codes.join(" · ")} 관찰 시각 수정`)] } : state) })} /></label>
      <Evidence items={item.evidence} />{errors(`states.${index}`)}
      <ActionButton variant="neutralWeak" type="button" className={entryControl} onClick={() => onChange({ ...content, states: content.states.filter((_, position) => position !== index) })}>이 관찰 제외</ActionButton>
    </section>)}
    <ActionButton variant="neutralWeak" type="button" className={entryControl} onClick={() => onChange({ ...content, states: [...content.states, {
      state_codes: ["UNKNOWN"], observed_at: null, time_precision: "UNKNOWN", phase: "UNRELATED", linked_action_refs: [], evidence: [correction("관찰 상태 모름 — 직접 추가")],
    }] })}>상태 관찰 직접 추가</ActionButton>
    <h3 className="font-medium">보호자 해석</h3>
    {content.caregiver_interpretations.map((item, index) => <section key={index} className="grid gap-2 rounded-lg border p-3">
      <label>보호자가 생각한 이유<input className={`${entryControl} w-full`} value={item.text} onChange={(event) => onChange({ ...content,
        caregiver_interpretations: content.caregiver_interpretations.map((value, position) => position === index ? { ...value, text: event.target.value, evidence: [correction(event.target.value)] } : value) })} /></label>
      <Evidence items={item.evidence} />{errors(`caregiver_interpretations.${index}`)}
      <ActionButton variant="neutralWeak" type="button" className={entryControl} onClick={() => onChange({ ...content, caregiver_interpretations: content.caregiver_interpretations.filter((_, position) => position !== index) })}>이 해석 제외</ActionButton>
    </section>)}
    <ActionButton variant="neutralWeak" type="button" className={entryControl} onClick={() => onChange({ ...content, caregiver_interpretations: [...content.caregiver_interpretations, { text: "", certainty: "CAREGIVER_REPORTED", evidence: [] }] })}>해석 직접 추가</ActionButton>
    {content.outcomes.length > 0 && <section className="grid gap-2 rounded-lg border p-3">
      <h3>행동 후 반응 · 사건 연결 필요</h3>
      {content.outcomes.map((item, index) => <p key={index}>{item.response_code}</p>)}
      <p>이 화면은 사건 없는 입력만 지원해요. 반응 후보를 제외한 뒤 확인할 수 있어요.</p>
      <ActionButton variant="neutralWeak" type="button" className={entryControl} onClick={() => onChange({ ...content, outcomes: [] })}>반응 후보 제외</ActionButton>
    </section>}
    <h3 className="font-medium">미해결 항목</h3>
    {content.unresolved.map((item, index) => <section key={`${item.field}-${index}`} className="grid gap-2 rounded-lg border p-3">
      <p>{({ CONFLICT: "충돌", UNKNOWN_VALUE: "값 모름", UNKNOWN_TIME: "시각 모름", UNSUPPORTED_CODE: "미지원 항목", MISSING_EVIDENCE: "근거 없음" })[item.code]} · {item.code}</p>
      <p className="text-sm">{item.field}: {item.message}</p>{errors(`unresolved.${index}`)}
      <ActionButton variant="neutralWeak" type="button" className={entryControl} onClick={() => onChange({ ...content, unresolved: content.unresolved.filter((_, position) => position !== index) })}>관련 카드를 수정했고 이 항목을 해소했어요</ActionButton>
    </section>)}
  </fieldset>;
}
