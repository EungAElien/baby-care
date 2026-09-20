"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { NotebookPen, MessageSquareText } from "lucide-react";
import { CareEntryWorkspace } from "@/components/care-entry-workspace";
import { CareEventForm } from "@/components/care-event-form";
import { useRealSession } from "@/lib/auth/real-session";
import { useDraft } from "@/lib/mock/draft";
import { ScreenSection } from "@/components/screen-state";
import { PageHeading } from "@/components/page-heading";
import { ActionButton } from "@/components/seed-design/ui/action-button";
import {
  TextField,
  TextFieldTextarea,
} from "@/components/seed-design/ui/text-field";
import { DemoOnly } from "@/components/demo-only";

export default function QuickRecordPage() {
  const { babyId } = useParams<{ babyId: string }>();
  const draft = useDraft();
  const real = useRealSession();
  const [mode, setMode] = useState<"structured" | "text">("structured");
  const liveText = draft.getLiveText(babyId);
  const loadedForBaby = useRef<string | null>(null);
  useEffect(() => {
    if (loadedForBaby.current === babyId) return;
    loadedForBaby.current = babyId;
    const savedDraft = draft.getSavedDraft(babyId);
    if (savedDraft && !draft.getLiveText(babyId))
      draft.setLiveText(babyId, savedDraft);
  }, [babyId, draft]);

  return (
    <div className="flex flex-col gap-6">
      <PageHeading
        title="빠른 기록"
        description="실제로 한 일과 직접 관찰한 모습을 구분해 기록해요."
      />
      <div className="record-methods" role="group" aria-label="기록 입력 방식">
        <button
          type="button"
          aria-pressed={mode === "structured"}
          onClick={() => setMode("structured")}
        >
          <NotebookPen aria-hidden="true" size={22} />
          <span>
            선택해서 기록<small>수유 · 수면 · 기저귀 · 달래기</small>
          </span>
        </button>
        <button
          type="button"
          aria-pressed={mode === "text"}
          onClick={() => setMode("text")}
        >
          <MessageSquareText aria-hidden="true" size={22} />
          <span>
            문장으로 초안<small>확인하고 수정한 뒤 저장</small>
          </span>
        </button>
      </div>
      <div hidden={mode !== "structured"}>
        <CareEventForm
          key={babyId}
          babyId={babyId}
          canSave={real.status === "signed-in"}
        />
      </div>
      <div hidden={mode !== "text"}>
        {real.status === "signed-in" ? (
          <CareEntryWorkspace babyId={babyId} />
        ) : (
          <ScreenSection title="확인 전 개인 초안">
            <TextField
              label="어떤 돌봄을 했나요?"
              description="실제 행동과 관찰한 모습을 적어 주세요. 원인 추정은 확정된 사실로 저장하지 않아요."
              value={liveText}
              onValueChange={({ value }) => draft.setLiveText(babyId, value)}
            >
              <TextFieldTextarea placeholder="예: 분유를 먹인 뒤 아기가 차분해 보였어요" />
            </TextField>
            <p className="text-sm text-muted-foreground">
              이 체험 화면의 입력은 공동 기록에 저장되지 않아요. 실제 계정에서는
              개인 초안을 저장하고 내용을 확인할 수 있어요.
            </p>
            <DemoOnly>
              <ActionButton variant="neutralWeak" asChild>
                <Link href={`/babies/${babyId}/entries/review`}>
                  DEMO · 확인 과정 살펴보기
                </Link>
              </ActionButton>
            </DemoOnly>
          </ScreenSection>
        )}
      </div>
    </div>
  );
}
