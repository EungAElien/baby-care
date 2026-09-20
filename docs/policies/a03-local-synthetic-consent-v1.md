# A-03 합성 로컬 연동 시험용 동의 문구

- 버전: `a03-local-synthetic-v1`
- 적용 범위: 격리 로컬 Supabase/FastAPI와 합성 계정·합성 아기 자료를 사용하는 개발 시험
- 상태: 시험 초안. 운영 승인 또는 실제 아동 자료 처리의 법적 동의 문구가 아니다.
- 활성화: 웹 로컬 환경에서 `NEXT_PUBLIC_ENABLE_MOCK_NAV=true`와 `NEXT_PUBLIC_POLICY_PROFILE=LOCAL_SYNTHETIC_V1`을 함께 설정한다. 운영 기본값은 비활성이다.

| 범위 | 화면 문구 |
| --- | --- |
| `SERVICE_PROCESSING` | 합성 시험 입력을 돌봄 기록 저장과 서비스 처리 기능 확인에 사용합니다. 실제 아기 자료는 입력하지 않습니다. |
| `AUDIO_RETENTION` | 합성 시험 음원을 재생과 보관 기능 확인에 사용합니다. 보관 선택은 서비스 처리 동의와 별개입니다. |
| `BABY_TRAINING` | 합성 시험 아기 자료의 모델 개선 기능을 확인합니다. 실제 아동 자료의 학습 참여를 승인하는 문구가 아닙니다. |
| `CONTRIBUTOR_TRAINING` | 내 합성 시험 기여자료의 모델 개선 기능을 확인합니다. 공동 기록 사용 동의와 별개입니다. |
| `SHARED_USE` | 같은 시험 아기의 활성 구성원과 확정 돌봄 기록을 함께 봅니다. 개인 미전송 초안은 공유하지 않습니다. |

문구와 버전은 `apps/web/src/lib/consent-policy.ts`의 로컬 시험 프로필과 맞춘다. 특정 범위의 승인된 운영 문구와 버전이 제공되면 해당 범위의 `NEXT_PUBLIC_POLICY_*_TEXT`·`NEXT_PUBLIC_POLICY_*_VERSION`을 함께 설정한다. 둘 중 하나만 설정하면 그 범위의 신규 동의는 열리지 않는다. 아기 자료 학습은 이 시험 문구만으로 열리지 않으며 서버의 아동 자료 확인 게이트와 새 OTP 재인증도 필요하다.
