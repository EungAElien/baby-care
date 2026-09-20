import Link from "next/link";
import { ArrowUpRight, Mic, NotebookPen, Users } from "lucide-react";
import { RecentCareEvent } from "@/components/recent-care-event";
import { HomeObservation } from "@/components/home-observation";
import { ActionButton } from "@/components/seed-design/ui/action-button";
import { BrandScene } from "@/components/brand-scene";

export default async function BabyHomePage({ params }: Readonly<{ params: Promise<{ babyId: string }> }>) {
  const { babyId } = await params;
  return (
    <div className="home-grid">
      <section className="brand-hero" aria-labelledby="home-title">
        <div className="hero-topline"><span>BABY CARE</span><span>함께 돌보는 하루</span></div>
        <div><h1 id="home-title" className="hero-heading">작은 신호를,<br />함께 알아가요.</h1><p className="hero-copy">울음은 살펴보고, 돌봄은 남겨요.<br />아기의 하루를 보호자와 함께 이어가요.</p></div>
        <BrandScene />
        <div className="hero-footer">
          <ActionButton asChild><Link href={`/babies/${babyId}/detect`}><Mic size={20} aria-hidden="true" />감지 시작<ArrowUpRight size={20} aria-hidden="true" /></Link></ActionButton>
          <p className="text-sm">감지 지원 여부를 확인한 뒤 시작해요.<br />직접 녹음과 파일 입력도 여기에서 할 수 있어요.</p>
        </div>
      </section>
      <div className="home-aside">
        <HomeObservation babyId={babyId} />
        <RecentCareEvent babyId={babyId} />
        <section className="screen-section">
          <div><p className="section-eyebrow">보호자가 직접 남기는 기록</p><h2 className="mt-1 text-lg font-bold">방금 한 돌봄, 잊기 전에</h2></div>
          <ActionButton variant="neutralWeak" asChild><Link href={`/babies/${babyId}/quick-record`}><NotebookPen size={20} aria-hidden="true" />빠른 기록</Link></ActionButton>
        </section>
        <ActionButton variant="ghost" size="medium" asChild><Link href={`/babies/${babyId}/care-team`}><Users size={19} aria-hidden="true" />공동양육 구성원 보기</Link></ActionButton>
      </div>
    </div>
  );
}

