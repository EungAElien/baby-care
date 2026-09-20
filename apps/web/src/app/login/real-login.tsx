"use client";

// SC01 시작 — 실제 Supabase 이메일 OTP 로그인과 아기 목록/생성/선택
// (B-04 인계 "A-03 연결 순서" 1). 재인증·세션 회수·아동 자료 확인
// 게이트는 다음 커밋에서 잇는다(개발계약의 [직접] 항목은 그대로 둔다).
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useRealSession } from "@/lib/auth/real-session";
import {
  useActiveBabyQuery,
  useBabiesQuery,
  useCreateBabyMutation,
  useSetActiveBabyMutation,
  type CreateBabyInput,
} from "@/lib/api/babies";
import { ContractApiError } from "@/lib/api/errors";
import { LoadingState, ErrorState, ScreenSection } from "@/components/screen-state";
import type { components } from "@/lib/api/generated";

type Step = "email" | "otp";

const feedingModeLabel: Record<components["schemas"]["CreateBaby"]["feeding_mode"], string> = {
  BREAST: "모유",
  FORMULA: "분유",
  MIXED: "혼합",
  UNSPECIFIED: "미설정",
};

function errorMessage(error: unknown): string {
  if (error instanceof ContractApiError) return error.envelope.message;
  return "요청을 처리하지 못했어요. 다시 시도해 주세요.";
}

export function RealLogin() {
  const session = useRealSession();

  if (session.status === "signed-in") return <BabySelection />;

  return <EmailOtpForm />;
}

export function EmailOtpForm() {
  const session = useRealSession();
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function sendOtp(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    const result = await session.requestOtp(email);
    setPending(false);
    if (!result.ok) {
      setError(result.error ?? "인증코드를 보내지 못했어요.");
      return;
    }
    setStep("otp");
  }

  async function verify(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    const result = await session.verifyOtp(email, code);
    setPending(false);
    if (!result.ok) {
      setError(result.error ?? "인증코드가 올바르지 않아요.");
    }
    // 성공하면 onAuthStateChange가 status를 signed-in으로 바꾸고 RealLogin이 다음 화면을 그린다.
  }

  return (
    <ScreenSection title={step === "email" ? "이메일로 로그인" : "인증코드 확인"}>
      {step === "email" ? (
        <form onSubmit={sendOtp} className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm text-foreground">
            이메일
            <input
              type="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="min-h-11 rounded-md border border-border bg-background px-3 text-sm text-foreground"
              placeholder="you@example.com"
            />
          </label>
          {error && <ErrorState label={error} />}
          <button
            type="submit"
            disabled={pending || !email}
            className="flex min-h-11 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50"
          >
            {pending ? "보내는 중…" : "인증코드 보내기"}
          </button>
        </form>
      ) : (
        <form onSubmit={verify} className="flex flex-col gap-3">
          <p className="text-sm text-muted-foreground">{email}로 보낸 인증코드를 입력하세요.</p>
          <label className="flex flex-col gap-1 text-sm text-foreground">
            인증코드
            <input
              type="text"
              inputMode="numeric"
              required
              value={code}
              onChange={(event) => setCode(event.target.value)}
              className="min-h-11 rounded-md border border-border bg-background px-3 text-sm text-foreground"
              placeholder="123456"
            />
          </label>
          {error && <ErrorState label={error} />}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => {
                setStep("email");
                setError(null);
              }}
              className="min-h-11 rounded-md border border-border px-4 text-sm text-foreground"
            >
              이메일 다시 입력
            </button>
            <button
              type="submit"
              disabled={pending || !code}
              className="flex min-h-11 flex-1 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50"
            >
              {pending ? "확인 중…" : "로그인"}
            </button>
          </div>
        </form>
      )}
    </ScreenSection>
  );
}

function BabySelection() {
  const router = useRouter();
  const babies = useBabiesQuery(true);
  const activeBaby = useActiveBabyQuery(true);
  const setActiveBaby = useSetActiveBabyMutation();

  const items = babies.data?.items ?? [];

  useEffect(() => {
    if (items.length !== 1) return;
    const onlyBaby = items[0]!;
    setActiveBaby.mutate(onlyBaby.baby.baby_id);
    router.push(`/babies/${onlyBaby.baby.baby_id}`);
    // 아기가 정확히 하나면 선택 화면 없이 바로 이동한다 — items 배열 참조가 매 렌더 바뀌므로 길이만 본다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items.length]);

  function choose(babyId: string) {
    setActiveBaby.mutate(babyId);
    router.push(`/babies/${babyId}`);
  }

  if (babies.isLoading) return <LoadingState label="아기 정보를 불러오고 있어요" />;
  if (babies.isError) return <ErrorState label={errorMessage(babies.error)} retryable />;

  if (items.length === 0) return <CreateBabyForm />;
  if (items.length === 1) return <LoadingState label="아기 홈으로 이동하고 있어요" />;

  const suggested = activeBaby.data?.baby_id ?? null;

  return (
    <ScreenSection title="아기를 선택하세요">
      <ul className="flex flex-col gap-2">
        {items.map((item) => (
          <li key={item.baby.baby_id}>
            <button
              type="button"
              onClick={() => choose(item.baby.baby_id)}
              className="flex min-h-11 w-full flex-col items-start gap-0.5 rounded-md border border-border bg-background px-3 py-2 text-left hover:border-primary"
            >
              <span className="text-sm font-medium text-foreground">
                {item.baby.alias}
                {suggested === item.baby.baby_id && " (마지막 선택)"}
              </span>
              <span className="text-xs text-muted-foreground">
                {item.membership.role === "OWNER" ? "관리 보호자" : "공동 보호자"}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </ScreenSection>
  );
}

function CreateBabyForm() {
  const router = useRouter();
  const createBaby = useCreateBabyMutation();
  const setActiveBaby = useSetActiveBabyMutation();
  const [form, setForm] = useState<CreateBabyInput>({
    alias: "",
    birth_date: "",
    feeding_mode: "UNSPECIFIED",
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  });

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const created = await createBaby.mutateAsync(form);
    setActiveBaby.mutate(created.baby.baby_id);
    router.push(`/babies/${created.baby.baby_id}`);
  }

  return (
    <ScreenSection title="참여한 아기가 없어요 — 아기를 만들어 주세요">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm text-foreground">
          별칭
          <input
            type="text"
            required
            value={form.alias}
            onChange={(event) => setForm((prev) => ({ ...prev, alias: event.target.value }))}
            className="min-h-11 rounded-md border border-border bg-background px-3 text-sm text-foreground"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm text-foreground">
          생년월일
          <input
            type="date"
            required
            max={new Date().toISOString().slice(0, 10)}
            value={form.birth_date}
            onChange={(event) => setForm((prev) => ({ ...prev, birth_date: event.target.value }))}
            className="min-h-11 rounded-md border border-border bg-background px-3 text-sm text-foreground"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm text-foreground">
          수유 방식
          <select
            value={form.feeding_mode}
            onChange={(event) =>
              setForm((prev) => ({
                ...prev,
                feeding_mode: event.target.value as CreateBabyInput["feeding_mode"],
              }))
            }
            className="min-h-11 rounded-md border border-border bg-background px-3 text-sm text-foreground"
          >
            {Object.entries(feedingModeLabel).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        {createBaby.isError && <ErrorState label={errorMessage(createBaby.error)} />}
        <button
          type="submit"
          disabled={createBaby.isPending || !form.alias || !form.birth_date}
          className="flex min-h-11 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          {createBaby.isPending ? "만드는 중…" : "아기 만들기"}
        </button>
      </form>
    </ScreenSection>
  );
}
