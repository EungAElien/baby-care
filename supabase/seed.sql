-- Synthetic local-only B-03 fixtures. These addresses do not receive mail and
-- the placeholder Auth rows cannot sign in. HTTP tests create login-capable
-- users through the local Auth API and keep their generated passwords in memory.

begin;

insert into auth.users (id, email, raw_user_meta_data)
values
    ('10000000-0000-4000-8000-000000000001', 'owner-a@baby-care.invalid', '{}'),
    ('10000000-0000-4000-8000-000000000002', 'caregiver-a@baby-care.invalid', '{}'),
    ('10000000-0000-4000-8000-000000000003', 'owner-b@baby-care.invalid', '{}'),
    ('10000000-0000-4000-8000-000000000004', 'invited-a@baby-care.invalid', '{}'),
    ('10000000-0000-4000-8000-000000000005', 'removed-a@baby-care.invalid', '{}')
on conflict (id) do nothing;

insert into baby_data.babies (
    baby_id,
    owner_user_id,
    alias,
    birth_date,
    feeding_mode,
    timezone,
    status,
    context_revision,
    version
)
values
    (
        '10000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000001',
        '합성 아기 A',
        date '2026-06-01',
        'MIXED',
        'Asia/Seoul',
        'ACTIVE',
        3,
        1
    ),
    (
        '10000000-0000-4000-8000-000000000102',
        '10000000-0000-4000-8000-000000000003',
        '합성 아기 B',
        date '2026-07-01',
        'FORMULA',
        'Asia/Seoul',
        'ACTIVE',
        1,
        1
    );

insert into baby_data.baby_memberships (
    membership_id,
    baby_id,
    user_id,
    role,
    relationship,
    display_name,
    status,
    version
)
values
    (
        '10000000-0000-4000-8000-000000000201',
        '10000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000001',
        'OWNER',
        'MOTHER',
        '보호자 A',
        'ACTIVE',
        1
    ),
    (
        '10000000-0000-4000-8000-000000000202',
        '10000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000002',
        'CAREGIVER',
        'FATHER',
        '공동양육자 A',
        'ACTIVE',
        1
    ),
    (
        '10000000-0000-4000-8000-000000000203',
        '10000000-0000-4000-8000-000000000102',
        '10000000-0000-4000-8000-000000000003',
        'OWNER',
        'MOTHER',
        '보호자 B',
        'ACTIVE',
        1
    ),
    (
        '10000000-0000-4000-8000-000000000204',
        '10000000-0000-4000-8000-000000000102',
        '10000000-0000-4000-8000-000000000001',
        'CAREGIVER',
        'OTHER',
        '보호자 A',
        'ACTIVE',
        1
    ),
    (
        '10000000-0000-4000-8000-000000000205',
        '10000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000005',
        'CAREGIVER',
        'GRANDPARENT',
        '해제된 구성원',
        'REVOKED',
        2
    );

insert into baby_data.invitations (
    invite_id,
    baby_id,
    inviter_user_id,
    email,
    token_sha256,
    status,
    expires_at,
    version
)
values (
    '10000000-0000-4000-8000-000000000211',
    '10000000-0000-4000-8000-000000000101',
    '10000000-0000-4000-8000-000000000001',
    'invited-a@baby-care.invalid',
    decode(repeat('11', 32), 'hex'),
    'PENDING',
    clock_timestamp() + interval '24 hours',
    1
);

insert into baby_data.consents (
    consent_id,
    baby_id,
    actor_user_id,
    scope,
    status,
    policy_version,
    granted_at,
    version
)
values
    (
        '10000000-0000-4000-8000-000000000221',
        '10000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000001',
        'SERVICE_PROCESSING',
        'GRANTED',
        '1.0.0',
        clock_timestamp(),
        1
    ),
    (
        '10000000-0000-4000-8000-000000000222',
        '10000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000002',
        'CONTRIBUTOR_TRAINING',
        'NOT_GRANTED',
        '1.0.0',
        null,
        1
    ),
    (
        '10000000-0000-4000-8000-000000000223',
        '10000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000005',
        'CONTRIBUTOR_TRAINING',
        'GRANTED',
        '1.0.0',
        clock_timestamp() - interval '2 days',
        1
    );

insert into baby_data.episodes (
    episode_id,
    baby_id,
    created_by_user_id,
    status,
    source,
    timing_status,
    started_at,
    data_origin,
    version
)
values
    (
        '10000000-0000-4000-8000-000000000301',
        '10000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000001',
        'OPEN',
        'MANUAL',
        'KNOWN',
        clock_timestamp() - interval '10 minutes',
        'DEMO',
        1
    ),
    (
        '10000000-0000-4000-8000-000000000302',
        '10000000-0000-4000-8000-000000000102',
        '10000000-0000-4000-8000-000000000003',
        'OPEN',
        'FILE',
        'UNKNOWN',
        null,
        'DEMO',
        1
    );

insert into baby_data.audio_assets (
    audio_id,
    baby_id,
    episode_id,
    created_by_user_id,
    object_key,
    mime_type,
    bytes,
    status,
    data_origin,
    version
)
values
    (
        '10000000-0000-4000-8000-000000000401',
        '10000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000301',
        '10000000-0000-4000-8000-000000000001',
        '10000000-0000-4000-8000-000000000101/10000000-0000-4000-8000-000000000401/10000000-0000-4000-8000-000000000411',
        'audio/wav',
        1024,
        'ALLOCATED',
        'DEMO',
        1
    ),
    (
        '10000000-0000-4000-8000-000000000402',
        '10000000-0000-4000-8000-000000000102',
        '10000000-0000-4000-8000-000000000302',
        '10000000-0000-4000-8000-000000000003',
        '10000000-0000-4000-8000-000000000102/10000000-0000-4000-8000-000000000402/10000000-0000-4000-8000-000000000412',
        'audio/wav',
        2048,
        'ALLOCATED',
        'DEMO',
        1
    );

insert into baby_data.audio_upload_grants (
    upload_id,
    baby_id,
    episode_id,
    audio_id,
    uploader_user_id,
    auth_session_id,
    object_key,
    method,
    max_bytes,
    expires_at
)
values
    (
        '10000000-0000-4000-8000-000000000411',
        '10000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000301',
        '10000000-0000-4000-8000-000000000401',
        '10000000-0000-4000-8000-000000000001',
        '10000000-0000-4000-8000-000000000011',
        '10000000-0000-4000-8000-000000000101/10000000-0000-4000-8000-000000000401/10000000-0000-4000-8000-000000000411',
        'STANDARD',
        25000000,
        clock_timestamp() + interval '14 minutes 59 seconds'
    ),
    (
        '10000000-0000-4000-8000-000000000412',
        '10000000-0000-4000-8000-000000000102',
        '10000000-0000-4000-8000-000000000302',
        '10000000-0000-4000-8000-000000000402',
        '10000000-0000-4000-8000-000000000003',
        '10000000-0000-4000-8000-000000000013',
        '10000000-0000-4000-8000-000000000102/10000000-0000-4000-8000-000000000402/10000000-0000-4000-8000-000000000412',
        'STANDARD',
        25000000,
        clock_timestamp() + interval '14 minutes 59 seconds'
    );

insert into baby_data.analyses (
    analysis_id,
    baby_id,
    episode_id,
    audio_id,
    created_by_user_id,
    status,
    stage,
    attempt_no,
    quality_status,
    inference_mode,
    inference_executed,
    data_origin
)
values (
    '10000000-0000-4000-8000-000000000501',
    '10000000-0000-4000-8000-000000000101',
    '10000000-0000-4000-8000-000000000301',
    '10000000-0000-4000-8000-000000000401',
    '10000000-0000-4000-8000-000000000001',
    'READY',
    'READY',
    1,
    'PENDING',
    'STUB',
    false,
    'DEMO'
);

insert into baby_data.care_events (
    care_event_id,
    baby_id,
    event_type,
    occurred_at,
    payload,
    status,
    created_by_user_id,
    updated_by_user_id,
    version
)
values
    (
        '10000000-0000-4000-8000-000000000601',
        '10000000-0000-4000-8000-000000000101',
        'FEEDING',
        clock_timestamp() - interval '30 minutes',
        '{"amount_ml":80,"method":"BOTTLE"}',
        'ACTIVE',
        '10000000-0000-4000-8000-000000000001',
        '10000000-0000-4000-8000-000000000001',
        1
    ),
    (
        '10000000-0000-4000-8000-000000000602',
        '10000000-0000-4000-8000-000000000101',
        'DIAPER',
        clock_timestamp() - interval '20 minutes',
        '{"condition":"WET"}',
        'ACTIVE',
        '10000000-0000-4000-8000-000000000002',
        '10000000-0000-4000-8000-000000000002',
        1
    ),
    (
        '10000000-0000-4000-8000-000000000603',
        '10000000-0000-4000-8000-000000000102',
        'FEEDING',
        clock_timestamp() - interval '15 minutes',
        '{"amount_ml":60,"method":"BOTTLE"}',
        'ACTIVE',
        '10000000-0000-4000-8000-000000000003',
        '10000000-0000-4000-8000-000000000003',
        1
    );

insert into baby_data.raw_care_entries (
    entry_id,
    baby_id,
    episode_id,
    author_user_id,
    input_mode,
    choices,
    raw_text,
    time_precision,
    input_revision,
    status,
    confirmed_by_user_id,
    confirmed_at,
    version
)
values
    (
        '10000000-0000-4000-8000-000000000701',
        '10000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000301',
        '10000000-0000-4000-8000-000000000001',
        'TEXT',
        '[]',
        '합성 보호자 원문 초안 A',
        'UNKNOWN',
        1,
        'DRAFT',
        null,
        null,
        1
    ),
    (
        '10000000-0000-4000-8000-000000000702',
        '10000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000301',
        '10000000-0000-4000-8000-000000000002',
        'TEXT',
        '[]',
        '합성 공동양육자 원문 초안',
        'UNKNOWN',
        1,
        'DRAFT',
        null,
        null,
        1
    ),
    (
        '10000000-0000-4000-8000-000000000703',
        '10000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000301',
        '10000000-0000-4000-8000-000000000002',
        'MIXED',
        '[{"type":"ACTION","code":"HOLDING"}]',
        '안아 주었어요',
        'EXACT',
        1,
        'CONFIRMED',
        '10000000-0000-4000-8000-000000000002',
        clock_timestamp() - interval '5 minutes',
        1
    );

insert into baby_data.normalization_runs (
    run_id,
    baby_id,
    entry_id,
    input_revision,
    status,
    execution_mode,
    prompt_version,
    schema_version,
    ontology_version,
    result,
    completed_at
)
values (
    '10000000-0000-4000-8000-000000000711',
    '10000000-0000-4000-8000-000000000101',
    '10000000-0000-4000-8000-000000000703',
    1,
    'COMPLETE',
    'STUB',
    'demo-prompt-1',
    '1.0.0',
    '1.0.0',
    '{"actions":[{"code":"HOLDING","modality":"PERFORMED"}],"states":[],"outcomes":[],"caregiver_interpretations":[],"unresolved":[]}',
    clock_timestamp() - interval '5 minutes'
);

insert into baby_data.state_observations (
    state_observation_id,
    baby_id,
    episode_id,
    source_entry_id,
    phase,
    observed_at,
    state_codes,
    source,
    created_by_user_id,
    confirmed_by_user_id,
    version
)
values (
    '10000000-0000-4000-8000-000000000721',
    '10000000-0000-4000-8000-000000000101',
    '10000000-0000-4000-8000-000000000301',
    '10000000-0000-4000-8000-000000000703',
    'AFTER',
    clock_timestamp() - interval '5 minutes',
    array['CALM'],
    'NORMALIZED_TEXT',
    '10000000-0000-4000-8000-000000000002',
    '10000000-0000-4000-8000-000000000002',
    1
);

insert into baby_data.reminder_settings (
    reminder_setting_id,
    baby_id,
    recipient_user_id,
    kind,
    enabled,
    options,
    version
)
values
    (
        '10000000-0000-4000-8000-000000000801',
        '10000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000001',
        'FEEDING',
        true,
        '{"lead_minutes":10}',
        1
    ),
    (
        '10000000-0000-4000-8000-000000000802',
        '10000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000002',
        'FEEDING',
        false,
        '{}',
        1
    );

insert into baby_data.deletion_jobs (
    deletion_job_id,
    requester_user_id,
    baby_id,
    scope,
    resource_id,
    status,
    pending_categories,
    attempt_no,
    version
)
values (
    '10000000-0000-4000-8000-000000000901',
    '10000000-0000-4000-8000-000000000005',
    '10000000-0000-4000-8000-000000000101',
    'MY_CONTRIBUTIONS',
    '10000000-0000-4000-8000-000000000005',
    'PENDING',
    array['RECORDS'],
    1,
    1
);

insert into baby_data.revoked_sessions (
    session_id,
    user_id,
    expires_at,
    reason
)
values (
    '10000000-0000-4000-8000-000000000015',
    '10000000-0000-4000-8000-000000000005',
    clock_timestamp() + interval '1 hour',
    'MEMBERSHIP_REVOKED'
);

commit;
