"use client";
import { isMockNavEnabled } from "@/lib/mock/config";

// SC09 내용 확인 — 원문·정규화 초안·근거·미해결 항목을 확인 저장 전에
// 보여준다. `scenario`로 REVIEW_READY(원문 포함)와 RUNNING/STALE/FAILED
// 작업 상태를 목 화면으로 오갈 수 있다. 실제 확인 저장은 A-07에서 연결한다.
import Link from "next/link";
import { notFound, useParams } from "next/navigation";
import {
  isForBaby,
  mockCareEntry,
  mockErrorEnvelope,
  mockNormalizationRun,
} from "@/lib/mock/fixtures";
import { useMockSessionOptional } from "@/lib/mock/session";
import {
  ScreenSection,
  LoadingState,
  ErrorState,
} from "@/components/screen-state";
import { PageHeading } from "@/components/page-heading";
import { DemoOnly } from "@/components/demo-only";
import { Callout } from "@/components/seed-design/ui/callout";

const unresolvedLabel: Record<string, string> = {
  CONFLICT: "선택과 문장이 서로 달라요",
  UNKNOWN_VALUE: "값을 알 수 없어요",
  UNKNOWN_TIME: "시각을 알 수 없어요",
  UNSUPPORTED_CODE: "지원하지 않는 항목이에요",
  MISSING_EVIDENCE: "근거를 찾지 못했어요",
};

const previewLinks = [
  { scenario: "review", label: "확인 준비됨 (원문 포함)" },
  { scenario: "running", label: "정리 중" },
  { scenario: "stale", label: "원문 수정으로 오래됨" },
  { scenario: "failure", label: "정리 실패" },
] as const;

export default function EntryConfirmPage() {
  const { babyId, scenario } = useParams<{
    babyId: string;
    scenario: string;
  }>();

  if (!isMockNavEnabled()) notFound();
  return (
    <DemoOnly
      fallback={
        <Link href={`/babies/${babyId}/entries`}>
          내 개인 초안 작성·복구로 이동
        </Link>
      }
    >
      <div className="flex flex-col gap-6">
        <PageHeading
          title="저장 전에 확인해요"
          description="보호자의 행동과 관찰을 나누어 읽고, 잘못 정리된 부분을 확인해 주세요."
        />
        <Callout
          title="확인 전 초안 · DEMO"
          description="정규화 결과의 예시예요. 이 화면의 내용은 공동 기록으로 저장되지 않아요."
        />

        {scenario === "review" && <ReviewReady babyId={babyId} />}
        {scenario === "running" && (
          <LoadingState label="문장을 정리하고 있어요" />
        )}
        {scenario === "stale" && <Stale babyId={babyId} />}
        {scenario === "failure" && <Failure babyId={babyId} />}
        {!["review", "running", "stale", "failure"].includes(scenario) &&
          notFound()}

        <details className="preview-tools">
          <summary>DEMO · 다른 상태 살펴보기</summary>
          <ul className="flex flex-col gap-2">
            {previewLinks.map((link) => (
              <li key={link.scenario}>
                <Link
                  href={`/babies/${babyId}/entries/${link.scenario}`}
                  className="flex min-h-11 items-center rounded-md border border-border px-3 text-sm text-foreground hover:border-primary"
                >
                  {link.label}
                </Link>
              </li>
            ))}
          </ul>
        </details>
      </div>
    </DemoOnly>
  );
}

function ReviewReady({ babyId }: Readonly<{ babyId: string }>) {
  // Optional: a real (non-mock) signed-in user can reach this still-mock-only
  // screen when NEXT_PUBLIC_ENABLE_MOCK_NAV is off, and must see the same
  // safe "not visible" fallback rather than a thrown exception.
  const session = useMockSessionOptional();
  const entry = mockCareEntry();

  // OWNER를 포함해 타인의 비공개 초안은 존재 자체를 숨기는 404 목 상태로 대체한다 (개발계약 §2 / getCareEntry).
  const isVisibleToCurrentUser =
    isForBaby(babyId, entry) && entry.author_user_id === session?.userId;
  if (!isVisibleToCurrentUser) {
    const notFoundError = mockErrorEnvelope("draft_other_author");
    return <ErrorState label={notFoundError.message} />;
  }

  const content = entry.normalized_content;
  return (
    <ScreenSection title="원문">
      <p className="rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground">
        {entry.raw_text}
      </p>
      {content && (
        <div className="flex flex-col gap-2 border-t border-border pt-3">
          <p className="text-sm font-medium text-foreground">정리한 행동</p>
          {content.actions.map((action) => (
            <p
              key={action.action_ref}
              className="text-sm text-muted-foreground"
            >
              {action.action_code} · {action.assertion}
              {action.amount !== null &&
                ` · ${action.amount}${action.unit === "ML" ? "mL" : "분"}`}
            </p>
          ))}
          {content.unresolved.length > 0 && (
            <div className="flex flex-col gap-1">
              <p className="text-sm font-medium text-foreground">모르는 항목</p>
              {content.unresolved.map((item) => (
                <p key={item.field} className="text-xs text-muted-foreground">
                  {unresolvedLabel[item.code] ?? item.code} — {item.message}
                </p>
              ))}
            </div>
          )}
        </div>
      )}
      <Link
        href={`/babies/${babyId}`}
        className="flex min-h-11 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground"
      >
        예시 확인 마치고 홈으로
      </Link>
    </ScreenSection>
  );
}

function Stale({ babyId }: Readonly<{ babyId: string }>) {
  const run = mockNormalizationRun("normalization_stale");
  return (
    <ScreenSection title="원문이 바뀌어 다시 확인해야 해요">
      <p className="text-sm text-muted-foreground">
        run_id {run.run_id.slice(0, 8)}…의 결과는 오래됐어요. 최신 원문을 다시
        정리해 주세요.
      </p>
      <Link
        href={`/babies/${babyId}/quick-record`}
        className="flex min-h-11 items-center justify-center rounded-md border border-border px-4 text-sm text-foreground"
      >
        다시 작성하기
      </Link>
    </ScreenSection>
  );
}

function Failure({ babyId }: Readonly<{ babyId: string }>) {
  const run = mockNormalizationRun("normalization_failure");
  return (
    <ScreenSection title="문장을 정리하지 못했어요">
      <ErrorState
        label={run.failure?.message ?? "알 수 없는 오류"}
        retryable={run.failure?.retryable}
      />
      <p className="text-xs text-muted-foreground">
        원문은 그대로 남아 있어요. 선택지로 직접 정리할 수 있어요.
      </p>
      <Link
        href={`/babies/${babyId}/quick-record`}
        className="flex min-h-11 items-center justify-center rounded-md border border-border px-4 text-sm text-foreground"
      >
        선택지로 정리하기
      </Link>
    </ScreenSection>
  );
}
