import type { ConsentScope } from "@/lib/api/shared-care";

export type ApprovedConsentPolicy = Readonly<{ version: string; text: string }>;

export function resolveApprovedPolicy(versionValue: string | undefined, textValue: string | undefined): ApprovedConsentPolicy | null {
  const version = versionValue?.trim();
  const text = textValue?.trim();
  return version && text ? { version, text } : null;
}

// Product/legal owners provide these public values together. An absent text or version
// keeps the grant path closed; an API policy_version must never be invented in the browser.
const configured: Record<ConsentScope, Readonly<{ version: string | undefined; text: string | undefined }>> = {
  SERVICE_PROCESSING: {
    version: process.env.NEXT_PUBLIC_POLICY_SERVICE_PROCESSING_VERSION,
    text: process.env.NEXT_PUBLIC_POLICY_SERVICE_PROCESSING_TEXT,
  },
  AUDIO_RETENTION: {
    version: process.env.NEXT_PUBLIC_POLICY_AUDIO_RETENTION_VERSION,
    text: process.env.NEXT_PUBLIC_POLICY_AUDIO_RETENTION_TEXT,
  },
  BABY_TRAINING: {
    version: process.env.NEXT_PUBLIC_POLICY_BABY_TRAINING_VERSION,
    text: process.env.NEXT_PUBLIC_POLICY_BABY_TRAINING_TEXT,
  },
  CONTRIBUTOR_TRAINING: {
    version: process.env.NEXT_PUBLIC_POLICY_CONTRIBUTOR_TRAINING_VERSION,
    text: process.env.NEXT_PUBLIC_POLICY_CONTRIBUTOR_TRAINING_TEXT,
  },
  SHARED_USE: {
    version: process.env.NEXT_PUBLIC_POLICY_SHARED_USE_VERSION,
    text: process.env.NEXT_PUBLIC_POLICY_SHARED_USE_TEXT,
  },
};

// Only for synthetic local integration. The mock-navigation flag must also be
// explicit, so a production build never silently treats this draft as approved.
const localSynthetic: Record<ConsentScope, ApprovedConsentPolicy> = {
  SERVICE_PROCESSING: {
    version: "a03-local-synthetic-v1",
    text: "합성 시험 입력을 돌봄 기록 저장과 서비스 처리 기능 확인에 사용합니다. 실제 아기 자료는 입력하지 않습니다.",
  },
  AUDIO_RETENTION: {
    version: "a03-local-synthetic-v1",
    text: "합성 시험 음원을 재생과 보관 기능 확인에 사용합니다. 보관 선택은 서비스 처리 동의와 별개입니다.",
  },
  BABY_TRAINING: {
    version: "a03-local-synthetic-v1",
    text: "합성 시험 아기 자료의 모델 개선 기능을 확인합니다. 실제 아동 자료의 학습 참여를 승인하는 문구가 아닙니다.",
  },
  CONTRIBUTOR_TRAINING: {
    version: "a03-local-synthetic-v1",
    text: "내 합성 시험 기여자료의 모델 개선 기능을 확인합니다. 공동 기록 사용 동의와 별개입니다.",
  },
  SHARED_USE: {
    version: "a03-local-synthetic-v1",
    text: "같은 시험 아기의 활성 구성원과 확정 돌봄 기록을 함께 봅니다. 개인 미전송 초안은 공유하지 않습니다.",
  },
};

export function resolveConsentPolicy(scope: ConsentScope, version: string | undefined, text: string | undefined,
  useLocalSynthetic: boolean): ApprovedConsentPolicy | null {
  if (version?.trim() || text?.trim()) return resolveApprovedPolicy(version, text);
  return useLocalSynthetic ? localSynthetic[scope] : null;
}

export function approvedConsentPolicy(scope: ConsentScope): ApprovedConsentPolicy | null {
  const entry = configured[scope];
  return resolveConsentPolicy(scope, entry.version, entry.text,
    process.env.NEXT_PUBLIC_ENABLE_MOCK_NAV === "true" &&
    process.env.NEXT_PUBLIC_POLICY_PROFILE === "LOCAL_SYNTHETIC_V1");
}
