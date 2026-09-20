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

export function approvedConsentPolicy(scope: ConsentScope): ApprovedConsentPolicy | null {
  const entry = configured[scope];
  return resolveApprovedPolicy(entry.version, entry.text);
}
