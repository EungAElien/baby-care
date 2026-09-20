"use client";
import { ActionButton } from "./seed-design/ui/action-button";

import Link from "next/link";
import { PageHeading } from "./page-heading";
import { TextField, TextFieldTextarea } from "./seed-design/ui/text-field";
import { useEffect, useState, useSyncExternalStore } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { usePrivateScope } from "./app-providers";
import {
  CareEntryContent,
  EntrySelect,
  entryControl,
} from "./care-entry-content";
import { CareEntryObservation } from "./care-entry-observation";
import { ScreenSection } from "./screen-state";
import { careEntriesApi, type Source } from "@/lib/api/care-entries";
import { useApiClient, type RealApiClient } from "@/lib/api/real-client";
import { useRealSession } from "@/lib/auth/real-session";
import {
  actionLabels,
  assertionLabels,
  stateLabels,
  textEvidence,
} from "@/lib/care-entries/content";
import { EntryWorkspace } from "@/lib/care-entries/workspace";
import type { PrivateScope, PrivateScopeSnapshot } from "@/lib/private-scope";

// Actual-data external processing approval is pending. Neither DEMO labels nor a public env flag bypass it.
const actualProcessingApproved = false;

export function CareEntryWorkspace({ babyId }: { babyId: string }) {
  const scope = usePrivateScope();
  const snapshot = useSyncExternalStore(
    (listener) => scope.subscribe(listener),
    () => scope.snapshot(),
    () => scope.snapshot(),
  );
  const client = useApiClient();
  const real = useRealSession();
  if (
    real.status !== "signed-in" ||
    !client ||
    !snapshot.userId ||
    snapshot.babyId !== babyId ||
    snapshot.userId !== real.userId
  ) {
    return (
      <p>개인 초안을 저장하고 복구하려면 실제 계정으로 로그인해 주세요.</p>
    );
  }
  return (
    <ConnectedWorkspace
      key={`${snapshot.userId}:${babyId}:${snapshot.generation}`}
      client={client}
      scope={scope}
      snapshot={snapshot}
      babyId={babyId}
    />
  );
}

function ConnectedWorkspace({
  client,
  scope,
  snapshot,
  babyId,
}: {
  client: RealApiClient;
  scope: PrivateScope;
  snapshot: PrivateScopeSnapshot;
  babyId: string;
}) {
  const [workspace] = useState(
    () =>
      new EntryWorkspace(
        careEntriesApi(client, babyId, snapshot.userId!),
        () => scope.assertCurrent(snapshot),
        actualProcessingApproved,
      ),
  );
  const state = useSyncExternalStore(
    workspace.subscribe,
    workspace.snapshot,
    workspace.snapshot,
  );
  const queryClient = useQueryClient();
  useEffect(() => {
    workspace.activate();
    const unregister = scope.registerCleanup(() => workspace.dispose());
    const entryId = window.location.hash.slice(1);
    void workspace.load().then(() => {
      if (/^[0-9a-f-]{36}$/i.test(entryId)) return workspace.open(entryId);
    });
    return () => {
      unregister();
      workspace.dispose();
    };
  }, [workspace, scope]);
  useEffect(() => {
    if (state.entry)
      window.history.replaceState(null, "", `#${state.entry.entry_id}`);
  }, [state.entry]);
  return (
    <CareEntryWorkspaceView
      workspace={workspace}
      babyId={babyId}
      onConfirmed={() => {
        void queryClient.invalidateQueries({
          queryKey: ["private", snapshot.userId, babyId, "timeline"],
        });
      }}
    />
  );
}

export function CareEntryWorkspaceView({
  workspace,
  babyId,
  onConfirmed,
}: {
  workspace: EntryWorkspace;
  babyId: string;
  onConfirmed?: () => void;
}) {
  const state = useSyncExternalStore(
    workspace.subscribe,
    workspace.snapshot,
    workspace.snapshot,
  );
  const [choiceAction, setChoiceAction] =
    useState<keyof typeof actionLabels>("FEEDING");
  const [assertion, setAssertion] =
    useState<keyof typeof assertionLabels>("UNCERTAIN");
  const [choiceState, setChoiceState] =
    useState<Exclude<keyof typeof stateLabels, "NEUTRAL">>("UNKNOWN");
  const [selection, setSelection] = useState({ start: 0, end: 0 });
  const [selectionError, setSelectionError] = useState("");
  const locked =
    state.busy ||
    !!state.pendingSave ||
    !!state.pendingConfirm ||
    !!state.confirmed;
  const changeSource = (patch: Partial<Source>) => {
    const source = { ...state.source, ...patch };
    source.input_mode = source.choices.length
      ? source.raw_text?.trim()
        ? "MIXED"
        : "CHOICE"
      : "TEXT";
    if (source.input_mode === "CHOICE") source.raw_text = null;
    workspace.setSource(source);
  };
  const blocking =
    state.content.outcomes.length > 0 ||
    state.content.unresolved.some((item) =>
      ["CONFLICT", "UNSUPPORTED_CODE", "MISSING_EVIDENCE"].includes(item.code),
    );
  const normalizerReason = {
    DISABLED: "서버에서 꺼져 있어요",
    CREDENTIALS_MISSING: "서버 자격 증명 준비 중",
    DEPENDENCY_MISSING: "서버 실행 환경 준비 중",
    DATABASE_UNAVAILABLE: "데이터베이스 연결 확인 중",
  }[state.capabilities?.normalizer_unavailable_reason ?? "DISABLED"];
  const confirm = async () => {
    await workspace.confirm();
    if (workspace.snapshot().confirmed) onConfirmed?.();
  };
  const addTextAction = () => {
    try {
      const evidence = textEvidence(
        state.source.raw_text ?? "",
        selection.start,
        selection.end,
      );
      workspace.setContent({
        ...state.content,
        actions: [
          ...state.content.actions,
          {
            action_ref: crypto.randomUUID(),
            action_code: "OTHER",
            assertion: "UNCERTAIN",
            performed_by_user_id: null,
            occurred_at: null,
            relative_time: null,
            time_precision: "UNKNOWN",
            sequence:
              Math.max(
                0,
                ...state.content.actions.map((item) => item.sequence),
              ) + 1,
            amount: null,
            unit: null,
            feeding_mode: null,
            evidence: [evidence],
          },
        ],
      });
      setSelectionError("");
    } catch {
      setSelectionError("원문에서 완전한 문자를 선택해 주세요.");
    }
  };
  return (
    <div className="flex flex-col gap-5">
      <PageHeading
        title="내용 확인 · 개인 초안"
        description="내가 한 일과 관찰한 모습, 모델의 제안을 구분해 확인해요."
      />
      <p className="text-sm text-muted-foreground">
        현재 아기의 사건 없는 기록이에요. 확인 전 원문과 정리 결과는 작성자만 볼
        수 있어요.
      </p>
      <ScreenSection title="내 초안 복구">
        <ActionButton
          variant="neutralWeak"
          className={entryControl}
          disabled={state.busy}
          onClick={() => void workspace.load()}
        >
          내 초안 다시 조회
        </ActionButton>
        {state.entries.length === 0 && (
          <p className="text-sm">불러온 개인 초안이 없어요.</p>
        )}
        {state.entries.map((entry) => (
          <ActionButton
            variant="neutralWeak"
            key={entry.entry_id}
            disabled={locked}
            className={`${entryControl} text-left`}
            onClick={() => void workspace.open(entry.entry_id)}
          >
            {entry.raw_text ||
              entry.choices.map((choice) => choice.code).join(" · ")}{" "}
            · revision {entry.input_revision}
          </ActionButton>
        ))}
        {state.cursor && (
          <ActionButton
            variant="neutralWeak"
            className={entryControl}
            disabled={state.busy}
            onClick={() => void workspace.more()}
          >
            초안 더 보기
          </ActionButton>
        )}
        <ActionButton
          variant="neutralWeak"
          className={entryControl}
          disabled={locked && !state.confirmed}
          onClick={() => {
            workspace.newDraft();
            if (!workspace.snapshot().entry)
              window.history.replaceState(null, "", window.location.pathname);
          }}
        >
          새 초안 작성
        </ActionButton>
      </ScreenSection>
      {state.issue && (
        <section role="alert" className="grid gap-2 rounded-lg border p-3">
          <p>{state.issue.message}</p>
          <p className="text-xs">{state.issue.code}</p>
          {state.issue.fields.map((field, index) => (
            <p key={`${field.field}-${index}`} className="text-sm">
              {field.field}: {field.message}
            </p>
          ))}
          {state.entry && (
            <ActionButton
              variant="neutralWeak"
              className={entryControl}
              disabled={state.busy}
              onClick={() => void workspace.refreshLatest()}
            >
              최신 초안·저장 결과 조회
            </ActionButton>
          )}
        </section>
      )}
      {state.latest && (
        <ScreenSection title="최신 초안과 비교">
          <p>내 입력: {state.source.raw_text ?? "선택지만 입력"}</p>
          <p>
            서버 원문: {state.latest.raw_text ?? "선택지만 입력"} · revision{" "}
            {state.latest.input_revision}
          </p>
          <p>
            서버 선택지:{" "}
            {state.latest.choices
              .map((choice) => `${choice.code} ${choice.assertion ?? ""}`)
              .join(" · ")}
          </p>
          {state.latest.base_record_versions.map((record) => (
            <p key={record.resource_id}>
              연결 기록 {record.resource_type} · version {record.version}
              {record.resource_type === "CARE_EVENT" && (
                <Link
                  className="text-primary underline"
                  href={`/babies/${babyId}/care-events/${record.resource_id}`}
                >
                  최신 생활 기록 보기
                </Link>
              )}
            </p>
          ))}
          {state.latestRecords?.map((item) => (
            <p className="break-words text-sm" key={item.record.resource_id}>
              현재 기록 version {item.record.version}: {item.summary}
            </p>
          ))}
          {state.entry?.supersedes_entry_id && state.latestRecords && (
            <ActionButton
              variant="neutralWeak"
              className={entryControl}
              disabled={locked}
              onClick={() => void workspace.rebaseCorrection()}
            >
              최신 기록과 비교했고 내 내용으로 수정 초안 다시 준비
            </ActionButton>
          )}
          <ActionButton
            variant="neutralWeak"
            className={entryControl}
            disabled={state.busy || !!state.pendingConfirm}
            onClick={() => void workspace.adoptLatest()}
          >
            비교했고 서버 초안으로 다시 검토
          </ActionButton>
        </ScreenSection>
      )}
      <ScreenSection title="원문과 선택지">
        <fieldset disabled={locked} className="grid gap-3">
          <TextField
            label="원문"
            value={state.source.raw_text ?? ""}
            onValueChange={({ value }) => changeSource({ raw_text: value })}
          >
            <TextFieldTextarea
              className="min-h-28"
              onSelect={(event) =>
                setSelection({
                  start: event.currentTarget.selectionStart,
                  end: event.currentTarget.selectionEnd,
                })
              }
              placeholder="예: 분유를 먹일까 생각했어요"
            />
          </TextField>
          <p className="text-xs">
            {Array.from(state.source.raw_text ?? "").length}/2000자 ·{" "}
            {state.source.input_mode} ·{" "}
            {state.entry
              ? `revision ${state.entry.input_revision}`
              : "아직 저장하지 않음"}
          </p>
          <EntrySelect
            label="선택할 행동"
            value={choiceAction}
            options={actionLabels}
            onChange={setChoiceAction}
          />
          <EntrySelect
            label="수행 여부"
            value={assertion}
            options={assertionLabels}
            onChange={setAssertion}
          />
          <ActionButton
            variant="neutralWeak"
            className={entryControl}
            onClick={() =>
              changeSource({
                choices: [
                  ...state.source.choices,
                  {
                    choice_id: crypto.randomUUID(),
                    kind: "ACTION",
                    code: choiceAction,
                    assertion,
                  },
                ],
              })
            }
          >
            행동 선택지 추가
          </ActionButton>
          <EntrySelect
            label="선택할 관찰"
            value={choiceState}
            options={{
              CRYING: stateLabels.CRYING,
              FUSSING: stateLabels.FUSSING,
              CALM: stateLabels.CALM,
              SLEEPY_APPEARING: stateLabels.SLEEPY_APPEARING,
              ASLEEP: stateLabels.ASLEEP,
              AWAKE: stateLabels.AWAKE,
              CHEERFUL_APPEARING: stateLabels.CHEERFUL_APPEARING,
              UNKNOWN: stateLabels.UNKNOWN,
            }}
            onChange={setChoiceState}
          />
          <ActionButton
            variant="neutralWeak"
            className={entryControl}
            onClick={() =>
              changeSource({
                choices: [
                  ...state.source.choices,
                  {
                    choice_id: crypto.randomUUID(),
                    kind: "STATE",
                    code: choiceState,
                    assertion: null,
                  },
                ],
              })
            }
          >
            관찰 선택지 추가
          </ActionButton>
          {state.source.choices.map((choice) => (
            <div
              key={choice.choice_id}
              className="flex items-center justify-between gap-2 text-sm"
            >
              <span>
                {actionLabels[choice.code as keyof typeof actionLabels] ??
                  stateLabels[choice.code as keyof typeof stateLabels] ??
                  choice.code}{" "}
                ·{" "}
                {choice.assertion
                  ? assertionLabels[choice.assertion]
                  : "상태 관찰"}
              </span>
              <ActionButton
                variant="neutralWeak"
                className={entryControl}
                onClick={() =>
                  changeSource({
                    choices: state.source.choices.filter(
                      (item) => item.choice_id !== choice.choice_id,
                    ),
                  })
                }
              >
                선택 제외
              </ActionButton>
            </div>
          ))}
        </fieldset>
        <ActionButton
          variant="neutralWeak"
          className={entryControl}
          disabled={
            state.busy ||
            !!state.pendingConfirm ||
            !!state.confirmed ||
            (!state.dirty && !state.pendingSave)
          }
          onClick={() => void workspace.save()}
        >
          {state.pendingSave
            ? "같은 요청으로 초안 저장 복구"
            : state.entry
              ? "원문·선택지 수정 저장"
              : "개인 초안 저장"}
        </ActionButton>
        {state.dirty && (
          <p className="text-xs">
            원문·선택지를 저장한 뒤 내용을 확인할 수 있어요.
          </p>
        )}
      </ScreenSection>
      <ScreenSection title="내용 정리 방법">
        <p className="text-sm">
          LLM:{" "}
          {state.capabilities === null
            ? "준비 상태 조회 필요"
            : state.capabilities.normalizer_available
              ? "서버 준비됨"
              : normalizerReason}
        </p>
        {!workspace.processingApproved && (
          <p className="text-sm">
            실제 자료의 외부 처리 승인 전이에요. 선택지 규칙 또는 직접 확인을
            사용해 주세요.
          </p>
        )}
        <ActionButton
          variant="neutralWeak"
          className={entryControl}
          disabled={
            locked ||
            state.dirty ||
            !state.entry ||
            !workspace.processingApproved ||
            !state.capabilities?.normalizer_available ||
            state.runStatus === "RUNNING" ||
            state.runStatus === "RECOVERING"
          }
          onClick={() => void workspace.normalize()}
        >
          LLM으로 정리
        </ActionButton>
        <ActionButton
          variant="neutralWeak"
          className={entryControl}
          disabled={
            locked ||
            state.dirty ||
            !state.entry ||
            state.source.choices.length === 0
          }
          onClick={() => workspace.useDirect("RULE")}
        >
          선택지만 규칙으로 확인 (RULE)
        </ActionButton>
        <ActionButton
          variant="neutralWeak"
          className={entryControl}
          disabled={locked || state.dirty || !state.entry}
          onClick={() => workspace.useDirect("MANUAL")}
        >
          직접 확인 (MANUAL)
        </ActionButton>
        <p className="text-xs">
          RULE은 선택지만 반영해요. 원문에 담긴 추가 내용은 직접 확인해 주세요.
        </p>
        {state.runStatus && (
          <p role="status">
            {
              {
                RUNNING: "RUNNING · 정리 중",
                RECOVERING: "같은 run 조회로 복구 중",
                COMPLETE: "COMPLETE · 제안 준비됨, 확인 필요",
                FAILED: "FAILED · 정리 실패, 직접 확인 가능",
                STALE: "STALE · 이전 입력 결과 사용 안 함",
              }[state.runStatus]
            }
          </p>
        )}
        {state.run && (
          <p className="text-xs">
            {state.run.execution_mode} · 제공자 호출{" "}
            {state.run.provider_call_executed ? "실행됨" : "실행 안 됨"} ·{" "}
            {state.run.model ?? "모델 없음"}
            {state.run.failure && ` · ${state.run.failure.code}`}
          </p>
        )}
        {state.runId && !state.confirmed && (
          <ActionButton
            variant="neutralWeak"
            className={entryControl}
            disabled={state.busy || state.dirty}
            onClick={() => void workspace.recoverRun()}
          >
            같은 run 다시 조회
          </ActionButton>
        )}
      </ScreenSection>
      {state.entry && !state.dirty && !state.confirmed && (
        <ScreenSection title="확인 카드">
          <p className="text-sm">확인 방식: {state.mode}</p>
          <ActionButton
            variant="neutralWeak"
            className={entryControl}
            disabled={locked}
            onClick={addTextAction}
          >
            선택한 원문을 새 행동의 근거로 사용
          </ActionButton>
          {selectionError && <p role="alert">{selectionError}</p>}
          <CareEntryContent
            content={state.content}
            onChange={(content) => workspace.setContent(content)}
            disabled={locked}
            fields={state.issue?.fields ?? []}
          />
          <label className="flex min-h-11 items-center gap-2 text-sm">
            <input
              type="checkbox"
              disabled={locked}
              checked={state.reviewed}
              onChange={(event) => workspace.setReviewed(event.target.checked)}
            />
            원문·선택지와 제안을 비교했고, 행동의 의미와 모르는 값을 확인했어요.
          </label>
          <ActionButton
            variant="neutralSolid"
            className={entryControl}
            disabled={
              state.busy ||
              !!state.pendingSave ||
              (!state.pendingConfirm &&
                (!state.reviewed || blocking || !!state.latest))
            }
            onClick={() => void confirm()}
          >
            {state.pendingConfirm
              ? "같은 키·본문으로 확인 저장 복구"
              : "확인한 내용 한 번 저장"}
          </ActionButton>
        </ScreenSection>
      )}
      {state.confirmed && (
        <ScreenSection title="확인 저장됨">
          <p role="status">
            생활 기록 {state.confirmed.care_event_ids.length}개 · 상태 관찰{" "}
            {state.confirmed.state_observation_ids.length}개를 저장했어요.
          </p>
          {state.observations.map((observation) => (
            <CareEntryObservation
              key={observation.state_observation_id}
              observation={observation}
            />
          ))}
          {state.confirmed.state_observation_ids.length >
            state.observations.length && (
            <ActionButton
              variant="neutralWeak"
              className={entryControl}
              disabled={state.busy}
              onClick={() => void workspace.loadObservations()}
            >
              확인된 관찰 다시 조회
            </ActionButton>
          )}
          <Link
            className="text-primary underline"
            href={`/babies/${babyId}/timeline`}
          >
            생활 기록에서 확인
          </Link>
        </ScreenSection>
      )}
    </div>
  );
}
