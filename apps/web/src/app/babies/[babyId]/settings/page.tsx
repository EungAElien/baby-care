"use client";
import { useParams } from "next/navigation";
import {
  Baby,
  Users,
  UserRound,
  ChevronRight,
  Mic,
  ShieldCheck,
} from "lucide-react";
import { mockBabyLabel } from "@/lib/mock/fixtures";
import { useMockSessionOptional } from "@/lib/mock/session";
import { useRealSession } from "@/lib/auth/real-session";
import {
  ScreenSection,
  ErrorState,
  LoadingState,
} from "@/components/screen-state";
import { PageHeading } from "@/components/page-heading";
import { ConsentPanel } from "@/components/consent-panel";
import { DeleteBabyPanel } from "@/components/delete-baby-panel";
import { BabyProfile } from "@/components/baby-profile";
import { findBabyAccess, useBabiesQuery } from "@/lib/api/babies";
import { List, ListItem, ListLinkItem } from "@/components/seed-design/ui/list";
import { Callout } from "@/components/seed-design/ui/callout";

function SettingsLinks({ babyId }: Readonly<{ babyId: string }>) {
  return (
    <ScreenSection title="함께 돌보는 사람">
      <List>
        <ListLinkItem
          href={`/babies/${babyId}/care-team`}
          title="공동양육 관리로 이동"
          detail="구성원 · 초대 · 역할과 접근 권한"
          prefix={<Users aria-hidden="true" />}
          suffix={<ChevronRight aria-hidden="true" />}
        />
        <ListLinkItem
          href="/account"
          title="내 계정으로 이동"
          detail="로그인 · 개인 동의 · 탈퇴"
          prefix={<UserRound aria-hidden="true" />}
          suffix={<ChevronRight aria-hidden="true" />}
        />
      </List>
    </ScreenSection>
  );
}
function BrowserPermission() {
  return (
    <Callout
      prefixIcon={<Mic />}
      title="마이크 권한"
      description="녹음 시작 시 브라우저에서 요청해요. 권한을 변경하려면 주소창의 사이트 설정을 열어 주세요. 앱의 분석·보관 동의와는 별개예요."
    />
  );
}
function RealSettings({ babyId }: Readonly<{ babyId: string }>) {
  const babies = useBabiesQuery(true);
  const access = findBabyAccess(babies.data, babyId);
  if (babies.isLoading) return <LoadingState label="설정을 불러오고 있어요" />;
  if (babies.isError || !access)
    return <ErrorState label="설정을 불러오지 못했어요." retryable />;
  return (
    <div className="flex flex-col gap-6">
      <PageHeading
        title="설정"
        description="아기 정보와 공유 범위, 나의 선택을 관리해요."
      />
      <div className="detail-grid">
        <div className="flex flex-col gap-5">
          <BabyProfile
            baby={access.baby}
            isOwner={access.membership.role === "OWNER"}
          />
          <SettingsLinks babyId={babyId} />
        </div>
        <div className="flex flex-col gap-5">
          <BrowserPermission />
          <ConsentPanel
            babyId={babyId}
            isOwner={access.membership.role === "OWNER"}
          />
        </div>
      </div>
      {access.membership.role === "OWNER" && (
        <details className="danger-settings">
          <summary>아기 자료 삭제</summary>
          <p className="my-3 text-sm">
            계정 탈퇴나 학습 동의 철회와 다른 작업이에요. 이 아기의 공동 자료
            전체에 적용돼요.
          </p>
          <DeleteBabyPanel baby={access.baby} />
        </details>
      )}
    </div>
  );
}
function MockSettings({ babyId }: Readonly<{ babyId: string }>) {
  const session = useMockSessionOptional();
  if (!session?.membershipFor(babyId)) return null;
  return (
    <div className="flex flex-col gap-6">
      <PageHeading
        title="설정"
        description="아기 정보와 공유 범위, 나의 선택을 관리해요."
      />
      <div className="detail-grid">
        <div className="flex flex-col gap-5">
          <ScreenSection title="아기 프로필">
            <div className="flex items-center gap-3">
              <span className="brand-symbol">
                <Baby aria-hidden="true" />
              </span>
              <p className="text-xl font-bold">{mockBabyLabel(babyId)}</p>
            </div>
            <p className="text-sm text-muted-foreground">
              예시 프로필이에요. 실제 계정에서는 관리 보호자가 이름·생일·수유
              방식·시간대를 수정할 수 있어요.
            </p>
          </ScreenSection>
          <SettingsLinks babyId={babyId} />
        </div>
        <div className="flex flex-col gap-5">
          <BrowserPermission />
          <ScreenSection title="자료 처리와 동의">
            <List>
              <ListItem
                title="서비스 분석과 기록 처리"
                detail="아기 자료의 서비스 이용 범위"
              />
              <ListItem
                title="음원 보관"
                detail="보관에 동의한 음원만 다시 들을 수 있어요"
              />
              <ListItem
                title="학습 참여"
                detail="서비스 이용 동의와 별도로 선택해요"
              />
            </List>
            <Callout
              prefixIcon={<ShieldCheck />}
              description="화면 체험에서는 동의를 저장하거나 자료를 삭제하지 않아요."
            />
          </ScreenSection>
        </div>
      </div>
    </div>
  );
}
export default function SettingsPage() {
  const { babyId } = useParams<{ babyId: string }>();
  const real = useRealSession();
  if (real.status === "loading")
    return <LoadingState label="계정을 확인하고 있어요" />;
  return real.status === "signed-in" ? (
    <RealSettings babyId={babyId} />
  ) : (
    <MockSettings babyId={babyId} />
  );
}
