import { notFound } from "next/navigation";
import { isMockNavEnabled } from "@/lib/mock/config";
import { ProductionReview } from "./production/review";

export default function CharacterMotionPage() {
  if (process.env.NODE_ENV !== "development" && !isMockNavEnabled()) notFound();
  return <ProductionReview />;
}
