import { notFound } from "next/navigation";
import { isMockNavEnabled } from "@/lib/mock/config";
import { ProductionReview } from "./review";

export default function ProductionReviewPage() {
  if (process.env.NODE_ENV !== "development" && !isMockNavEnabled()) notFound();
  return <ProductionReview />;
}
