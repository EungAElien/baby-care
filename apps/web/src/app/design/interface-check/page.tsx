"use client";

import { useState } from "react";
import { notFound } from "next/navigation";
import { isMockNavEnabled } from "@/lib/mock/config";
import { PageHeading } from "@/components/page-heading";
import { ActionButton } from "@/components/seed-design/ui/action-button";
import {
  TextField,
  TextFieldInput,
} from "@/components/seed-design/ui/text-field";
import {
  BottomSheetRoot,
  BottomSheetTrigger,
  BottomSheetContent,
  BottomSheetBody,
  BottomSheetFooter,
} from "@/components/seed-design/ui/bottom-sheet";

/** Explicit development-only fixture: no API, identity, persistence or simulated successful save. */
export default function InterfaceCheckPage() {
  const [scale, setScale] = useState("100");
  const [open, setOpen] = useState(false);
  if (!isMockNavEnabled()) notFound();
  return (
    <main className="mx-auto flex max-w-xl flex-col gap-6 p-5">
      <style>{`:root { font-size: ${scale}%; }`}</style>
      <PageHeading
        title="DEMO · 접근성 검증"
        description="합성 입력으로 시트의 확대·스크롤·포커스를 확인해요. 자료를 전송하거나 저장하지 않아요."
      />
      <label className="flex flex-col gap-2">
        글자 확대
        <select
          className="rounded-lg border px-3"
          value={scale}
          onChange={(event) => setScale(event.target.value)}
        >
          <option value="100">100%</option>
          <option value="200">200%</option>
        </select>
      </label>
      <BottomSheetRoot open={open} onOpenChange={setOpen}>
        <BottomSheetTrigger asChild>
          <ActionButton>검증용 프로필 시트 열기</ActionButton>
        </BottomSheetTrigger>
        <BottomSheetContent
          title="DEMO · 프로필 입력"
          description="실제 계정과 연결되지 않은 합성 화면이에요."
        >
          <form
            className="flex min-h-0 flex-col"
            onSubmit={(event) => event.preventDefault()}
          >
            <BottomSheetBody>
              <div className="flex flex-col gap-5 py-2">
                <TextField label="아기 이름" defaultValue="예시 아기">
                  <TextFieldInput />
                </TextField>
                <TextField label="생일" defaultValue="2026-01-01">
                  <TextFieldInput type="date" />
                </TextField>
                <TextField
                  label="기록 시간대"
                  description="이 시간대를 기준으로 하루를 구분해요."
                  defaultValue="Asia/Seoul"
                >
                  <TextFieldInput />
                </TextField>
                <p>
                  긴 설명과 확대된 입력도 시트 안에서 스크롤할 수 있어야 해요.
                  닫으면 시트를 열었던 버튼으로 포커스가 돌아가야 해요.
                </p>
              </div>
            </BottomSheetBody>
            <BottomSheetFooter>
              <ActionButton type="button" onClick={() => setOpen(false)}>
                검증 마치기
              </ActionButton>
            </BottomSheetFooter>
          </form>
        </BottomSheetContent>
      </BottomSheetRoot>
    </main>
  );
}
