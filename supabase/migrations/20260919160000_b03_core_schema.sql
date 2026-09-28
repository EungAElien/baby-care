-- B-03 core data model.
-- Business data lives outside the Data API exposed schema on purpose.

create schema if not exists baby_data;
create schema if not exists baby_private;

revoke all on schema baby_data from public;
revoke all on schema baby_private from public;

create type baby_data.baby_status as enum ('ACTIVE', 'DELETING', 'DELETED');
create type baby_data.membership_role as enum ('OWNER', 'CAREGIVER');
create type baby_data.membership_status as enum ('ACTIVE', 'LEFT', 'REVOKED');
create type baby_data.invitation_status as enum ('PENDING', 'ACCEPTED', 'EXPIRED', 'REVOKED');
create type baby_data.consent_scope as enum (
    'SERVICE_PROCESSING',
    'AUDIO_RETENTION',
    'BABY_TRAINING',
    'CONTRIBUTOR_TRAINING',
    'SHARED_USE'
);
create type baby_data.consent_status as enum ('NOT_GRANTED', 'GRANTED', 'REVOKED');
create type baby_data.data_origin as enum ('USER', 'DEMO');
create type baby_data.episode_status as enum ('OPEN', 'CLOSED');
create type baby_data.episode_source as enum ('AUTO', 'MANUAL', 'FILE');
create type baby_data.timing_status as enum ('KNOWN', 'UNKNOWN');
create type baby_data.observation_session_status as enum ('ACTIVE', 'STOPPED', 'EXPIRED');
create type baby_data.audio_asset_status as enum (
    'ALLOCATED',
    'VERIFYING',
    'READY',
    'REJECTED',
    'DELETING',
    'DELETED'
);
create type baby_data.upload_method as enum ('STANDARD', 'TUS');
create type baby_data.analysis_status as enum ('READY', 'RUNNING', 'COMPLETE', 'ABSTAIN', 'FAILED');
create type baby_data.analysis_stage as enum (
    'READY',
    'QUALITY_CHECK',
    'INFERENCE',
    'CONTEXT',
    'PERSISTING',
    'FINISHED'
);
create type baby_data.quality_status as enum ('PENDING', 'PASS', 'INSUFFICIENT', 'INVALID');
create type baby_data.execution_mode as enum ('REAL', 'STUB');
create type baby_data.mutable_record_status as enum ('ACTIVE', 'DELETING', 'DELETED');
create type baby_data.care_event_type as enum ('FEEDING', 'SLEEP', 'DIAPER', 'OTHER');
create type baby_data.raw_entry_status as enum (
    'DRAFT',
    'NORMALIZING',
    'REVIEW_READY',
    'NEEDS_MANUAL_REVIEW',
    'CONFIRMED',
    'DELETING',
    'DELETED'
);
create type baby_data.input_mode as enum ('CHOICE', 'TEXT', 'MIXED');
create type baby_data.time_precision as enum ('EXACT', 'RELATIVE', 'UNKNOWN');
create type baby_data.normalization_status as enum ('RUNNING', 'COMPLETE', 'FAILED', 'STALE');
create type baby_data.observation_phase as enum ('BEFORE', 'AFTER', 'UNRELATED', 'UNKNOWN');
create type baby_data.followup_status as enum ('PENDING', 'RECORDED', 'UNCONFIRMED');
create type baby_data.reminder_kind as enum ('FEEDING', 'SLEEP_PREPARATION', 'DIAPER');
create type baby_data.reminder_state as enum ('SCHEDULED', 'DUE', 'SNOOZED', 'DISMISSED', 'EXPIRED');
create type baby_data.deletion_scope as enum ('ALL', 'CARE_EVENT', 'CARE_ENTRY', 'MY_CONTRIBUTIONS');
create type baby_data.deletion_status as enum ('PENDING', 'RUNNING', 'COMPLETE', 'FAILED');

create table baby_data.babies (
    baby_id uuid primary key default extensions.gen_random_uuid(),
    owner_user_id uuid not null references auth.users(id) on delete restrict,
    alias text not null check (char_length(alias) between 1 and 40),
    birth_date date not null check (birth_date <= current_date),
    feeding_mode text not null check (feeding_mode in ('BREAST', 'FORMULA', 'MIXED', 'UNSPECIFIED')),
    timezone text not null check (char_length(timezone) between 1 and 64),
    status baby_data.baby_status not null default 'ACTIVE',
    context_revision bigint not null default 0 check (context_revision >= 0),
    version bigint not null default 1 check (version >= 1),
    recorded_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    unique (baby_id, owner_user_id)
);

create table baby_data.baby_memberships (
    membership_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null references baby_data.babies(baby_id) on delete restrict,
    user_id uuid not null references auth.users(id) on delete restrict,
    role baby_data.membership_role not null,
    relationship text not null check (relationship in ('MOTHER', 'FATHER', 'GRANDPARENT', 'OTHER')),
    display_name text not null check (char_length(display_name) between 1 and 80),
    status baby_data.membership_status not null default 'ACTIVE',
    version bigint not null default 1 check (version >= 1),
    recorded_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    unique (baby_id, membership_id)
);

create unique index baby_memberships_one_active_user_per_baby
    on baby_data.baby_memberships (baby_id, user_id)
    where status = 'ACTIVE';

create unique index baby_memberships_one_active_owner_per_baby
    on baby_data.baby_memberships (baby_id)
    where role = 'OWNER' and status = 'ACTIVE';

create unique index baby_memberships_one_active_owned_baby_per_user
    on baby_data.baby_memberships (user_id)
    where role = 'OWNER' and status = 'ACTIVE';

create table baby_data.invitations (
    invite_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null references baby_data.babies(baby_id) on delete restrict,
    inviter_user_id uuid not null references auth.users(id) on delete restrict,
    email text not null check (position('@' in email) > 1),
    token_sha256 bytea not null check (octet_length(token_sha256) = 32),
    status baby_data.invitation_status not null default 'PENDING',
    expires_at timestamptz not null,
    accepted_by_user_id uuid references auth.users(id) on delete restrict,
    accepted_at timestamptz,
    version bigint not null default 1 check (version >= 1),
    recorded_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    unique (baby_id, invite_id),
    check (expires_at > recorded_at),
    check (
        (status = 'ACCEPTED' and accepted_by_user_id is not null and accepted_at is not null)
        or (status <> 'ACCEPTED' and accepted_by_user_id is null and accepted_at is null)
    )
);

create unique index invitations_one_pending_per_email
    on baby_data.invitations (baby_id, lower(email))
    where status = 'PENDING';

create table baby_data.consents (
    consent_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null references baby_data.babies(baby_id) on delete restrict,
    actor_user_id uuid not null references auth.users(id) on delete restrict,
    scope baby_data.consent_scope not null,
    status baby_data.consent_status not null,
    policy_version text not null check (char_length(policy_version) between 1 and 80),
    granted_at timestamptz,
    revoked_at timestamptz,
    supersedes_consent_id uuid,
    version bigint not null default 1 check (version >= 1),
    recorded_at timestamptz not null default clock_timestamp(),
    unique (baby_id, consent_id),
    foreign key (baby_id, supersedes_consent_id)
        references baby_data.consents (baby_id, consent_id) on delete restrict,
    check (
        (status = 'NOT_GRANTED' and granted_at is null and revoked_at is null)
        or (status = 'GRANTED' and granted_at is not null and revoked_at is null)
        or (status = 'REVOKED' and revoked_at is not null)
    )
);

create unique index consents_one_root_history
    on baby_data.consents (baby_id, actor_user_id, scope)
    where supersedes_consent_id is null;

create table baby_data.user_preferences (
    user_id uuid primary key references auth.users(id) on delete cascade,
    active_baby_id uuid references baby_data.babies(baby_id) on delete set null,
    version bigint not null default 1 check (version >= 1),
    updated_at timestamptz not null default clock_timestamp()
);

create table baby_data.revoked_sessions (
    session_id uuid primary key,
    user_id uuid not null references auth.users(id) on delete cascade,
    revoked_at timestamptz not null default clock_timestamp(),
    expires_at timestamptz not null,
    reason text not null check (reason in ('LOGOUT', 'MEMBERSHIP_REVOKED', 'ACCOUNT_BLOCKED', 'ALL_SESSIONS')),
    check (expires_at > revoked_at)
);

create index revoked_sessions_user_expiry_idx
    on baby_data.revoked_sessions (user_id, expires_at);

create table baby_data.observation_sessions (
    session_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null references baby_data.babies(baby_id) on delete restrict,
    created_by_user_id uuid not null references auth.users(id) on delete restrict,
    source_id uuid not null,
    status baby_data.observation_session_status not null default 'ACTIVE',
    last_heartbeat_at timestamptz not null default clock_timestamp(),
    stopped_at timestamptz,
    stop_reason text,
    version bigint not null default 1 check (version >= 1),
    recorded_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    unique (baby_id, session_id),
    check (
        (status = 'ACTIVE' and stopped_at is null)
        or (status <> 'ACTIVE' and stopped_at is not null)
    )
);

create unique index observation_sessions_one_active_per_baby
    on baby_data.observation_sessions (baby_id)
    where status = 'ACTIVE';

create table baby_data.observation_windows (
    observation_window_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null,
    session_id uuid not null,
    input_started_at timestamptz not null,
    input_ended_at timestamptz,
    health text not null check (health in ('HEALTHY', 'PAUSED', 'INTERRUPTED')),
    pause_reason text,
    recorded_at timestamptz not null default clock_timestamp(),
    unique (baby_id, observation_window_id),
    foreign key (baby_id, session_id)
        references baby_data.observation_sessions (baby_id, session_id) on delete restrict,
    check (input_ended_at is null or input_ended_at >= input_started_at)
);

create table baby_data.episodes (
    episode_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null references baby_data.babies(baby_id) on delete restrict,
    created_by_user_id uuid not null references auth.users(id) on delete restrict,
    status baby_data.episode_status not null default 'OPEN',
    source baby_data.episode_source not null,
    timing_status baby_data.timing_status not null,
    started_at timestamptz,
    ended_at timestamptz,
    closed_reason text check (closed_reason is null or closed_reason in ('NO_CRY', 'USER_STOP', 'INPUT_GAP', 'FILE_IMPORT')),
    observation_session_id uuid,
    data_origin baby_data.data_origin not null,
    version bigint not null default 1 check (version >= 1),
    recorded_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    unique (baby_id, episode_id),
    foreign key (baby_id, observation_session_id)
        references baby_data.observation_sessions (baby_id, session_id) on delete restrict,
    check ((timing_status = 'KNOWN' and started_at is not null) or timing_status = 'UNKNOWN'),
    check (ended_at is null or started_at is null or ended_at >= started_at),
    check (
        (status = 'OPEN' and ended_at is null and closed_reason is null)
        or (status = 'CLOSED' and closed_reason is not null)
    )
);

create table baby_data.audio_assets (
    audio_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null,
    episode_id uuid not null,
    created_by_user_id uuid not null references auth.users(id) on delete restrict,
    bucket_id text not null default 'baby-audio' check (bucket_id = 'baby-audio'),
    object_key text not null check (object_key !~ '(^|/)\.\.(/|$)' and char_length(object_key) between 1 and 512),
    mime_type text not null check (char_length(mime_type) between 1 and 127),
    bytes bigint not null check (bytes between 0 and 25000000),
    duration_seconds numeric(8,3) check (duration_seconds is null or duration_seconds between 0 and 60),
    checksum_sha256 text check (checksum_sha256 is null or checksum_sha256 ~ '^[0-9a-f]{64}$'),
    status baby_data.audio_asset_status not null default 'ALLOCATED',
    retention_until timestamptz,
    rejection_code text,
    quality_reasons text[] not null default '{}',
    data_origin baby_data.data_origin not null,
    version bigint not null default 1 check (version >= 1),
    recorded_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    unique (bucket_id, object_key),
    unique (baby_id, audio_id),
    unique (baby_id, episode_id, audio_id),
    foreign key (baby_id, episode_id)
        references baby_data.episodes (baby_id, episode_id) on delete restrict,
    check (status <> 'READY' or (bytes > 0 and checksum_sha256 is not null)),
    check (status <> 'REJECTED' or rejection_code is not null)
);

create table baby_data.audio_upload_grants (
    upload_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null,
    episode_id uuid not null,
    audio_id uuid not null,
    uploader_user_id uuid not null references auth.users(id) on delete restrict,
    auth_session_id uuid not null,
    bucket_id text not null default 'baby-audio' check (bucket_id = 'baby-audio'),
    object_key text not null,
    method baby_data.upload_method not null,
    max_bytes integer not null default 25000000 check (max_bytes between 1 and 25000000),
    expires_at timestamptz not null,
    canceled_at timestamptz,
    completed_at timestamptz,
    recorded_at timestamptz not null default clock_timestamp(),
    unique (baby_id, upload_id),
    unique (bucket_id, object_key),
    foreign key (baby_id, episode_id, audio_id)
        references baby_data.audio_assets (baby_id, episode_id, audio_id) on delete restrict,
    foreign key (bucket_id, object_key)
        references baby_data.audio_assets (bucket_id, object_key) on delete restrict,
    check (expires_at > recorded_at),
    check (expires_at <= recorded_at + interval '15 minutes'),
    check (canceled_at is null or completed_at is null)
);

create index audio_upload_grants_active_lookup_idx
    on baby_data.audio_upload_grants (bucket_id, object_key, uploader_user_id, expires_at)
    where canceled_at is null and completed_at is null;

create table baby_data.analyses (
    analysis_id uuid primary key,
    baby_id uuid not null,
    episode_id uuid not null,
    audio_id uuid not null,
    created_by_user_id uuid not null references auth.users(id) on delete restrict,
    status baby_data.analysis_status not null default 'READY',
    stage baby_data.analysis_stage not null default 'READY',
    attempt_no integer not null default 1 check (attempt_no >= 1),
    execution_token uuid,
    lease_expires_at timestamptz,
    quality_status baby_data.quality_status not null default 'PENDING',
    quality_reasons text[] not null default '{}',
    cry_detected boolean,
    audio_candidates jsonb not null default '[]'::jsonb check (jsonb_typeof(audio_candidates) = 'array'),
    abstain_reason text check (abstain_reason is null or abstain_reason in ('NO_CRY', 'LOW_QUALITY', 'INSUFFICIENT_AUDIO', 'LOW_CONFIDENCE', 'UNSUPPORTED_SCOPE')),
    failure jsonb,
    model_version text,
    preprocess_version text,
    label_mapping_version text,
    inference_mode baby_data.execution_mode not null,
    inference_executed boolean not null default false,
    data_origin baby_data.data_origin not null,
    recorded_at timestamptz not null default clock_timestamp(),
    completed_at timestamptz,
    unique (baby_id, analysis_id),
    foreign key (baby_id, episode_id, audio_id)
        references baby_data.audio_assets (baby_id, episode_id, audio_id) on delete restrict,
    check (execution_token is null or status = 'RUNNING'),
    check ((status = 'RUNNING') = (lease_expires_at is not null)),
    check ((status in ('COMPLETE', 'ABSTAIN', 'FAILED')) = (completed_at is not null)),
    check ((status = 'ABSTAIN') = (abstain_reason is not null)),
    check ((status = 'FAILED') = (failure is not null)),
    check (inference_mode <> 'STUB' or inference_executed = false)
);

create table baby_data.context_snapshots (
    context_snapshot_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null,
    analysis_id uuid not null,
    as_of timestamptz not null,
    known_at timestamptz not null,
    record_refs jsonb not null default '[]'::jsonb check (jsonb_typeof(record_refs) = 'array'),
    features jsonb not null default '{}'::jsonb check (jsonb_typeof(features) = 'object'),
    missing_fields text[] not null default '{}',
    context_revision bigint not null check (context_revision >= 0),
    recorded_at timestamptz not null default clock_timestamp(),
    unique (baby_id, context_snapshot_id),
    foreign key (baby_id, analysis_id)
        references baby_data.analyses (baby_id, analysis_id) on delete restrict
);

create table baby_data.recommendations (
    recommendation_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null,
    analysis_id uuid not null,
    context_snapshot_id uuid not null,
    action_order jsonb not null check (jsonb_typeof(action_order) = 'array'),
    evidence_refs jsonb not null default '[]'::jsonb check (jsonb_typeof(evidence_refs) = 'array'),
    policy_version text not null,
    supersedes_recommendation_id uuid,
    recorded_at timestamptz not null default clock_timestamp(),
    unique (baby_id, recommendation_id),
    foreign key (baby_id, analysis_id)
        references baby_data.analyses (baby_id, analysis_id) on delete restrict,
    foreign key (baby_id, context_snapshot_id)
        references baby_data.context_snapshots (baby_id, context_snapshot_id) on delete restrict,
    foreign key (baby_id, supersedes_recommendation_id)
        references baby_data.recommendations (baby_id, recommendation_id) on delete restrict
);

create table baby_data.care_events (
    care_event_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null references baby_data.babies(baby_id) on delete restrict,
    event_type baby_data.care_event_type not null,
    occurred_at timestamptz,
    ended_at timestamptz,
    payload jsonb not null default '{}'::jsonb check (jsonb_typeof(payload) = 'object'),
    source_entry_id uuid,
    status baby_data.mutable_record_status not null default 'ACTIVE',
    created_by_user_id uuid not null references auth.users(id) on delete restrict,
    updated_by_user_id uuid not null references auth.users(id) on delete restrict,
    version bigint not null default 1 check (version >= 1),
    recorded_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    unique (baby_id, care_event_id),
    check (ended_at is null or occurred_at is null or ended_at >= occurred_at)
);

create unique index care_events_one_active_sleep_per_baby
    on baby_data.care_events (baby_id)
    where event_type = 'SLEEP' and status = 'ACTIVE' and ended_at is null;

create table baby_data.caregiver_observations (
    observation_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null,
    episode_id uuid not null,
    observed_at timestamptz,
    kind text not null check (char_length(kind) between 1 and 80),
    value jsonb not null,
    created_by_user_id uuid not null references auth.users(id) on delete restrict,
    version bigint not null default 1 check (version >= 1),
    recorded_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    unique (baby_id, observation_id),
    foreign key (baby_id, episode_id)
        references baby_data.episodes (baby_id, episode_id) on delete restrict
);

create table baby_data.action_attempts (
    action_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null,
    episode_id uuid not null,
    care_event_id uuid,
    recommendation_id uuid,
    performed_at timestamptz,
    performed_by_user_id uuid references auth.users(id) on delete restrict,
    sequence_no integer not null check (sequence_no >= 1),
    status baby_data.mutable_record_status not null default 'ACTIVE',
    followup_status baby_data.followup_status not null default 'PENDING',
    created_by_user_id uuid not null references auth.users(id) on delete restrict,
    updated_by_user_id uuid not null references auth.users(id) on delete restrict,
    version bigint not null default 1 check (version >= 1),
    recorded_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    unique (baby_id, action_id),
    unique (baby_id, episode_id, action_id),
    unique (episode_id, care_event_id),
    foreign key (baby_id, episode_id)
        references baby_data.episodes (baby_id, episode_id) on delete restrict,
    foreign key (baby_id, care_event_id)
        references baby_data.care_events (baby_id, care_event_id) on delete restrict,
    foreign key (baby_id, recommendation_id)
        references baby_data.recommendations (baby_id, recommendation_id) on delete restrict
);

create table baby_data.action_groups (
    action_group_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null,
    episode_id uuid not null,
    source_entry_id uuid,
    created_by_user_id uuid not null references auth.users(id) on delete restrict,
    version bigint not null default 1 check (version >= 1),
    recorded_at timestamptz not null default clock_timestamp(),
    unique (baby_id, action_group_id),
    unique (baby_id, episode_id, action_group_id),
    foreign key (baby_id, episode_id)
        references baby_data.episodes (baby_id, episode_id) on delete restrict
);

create table baby_data.action_group_members (
    baby_id uuid not null,
    episode_id uuid not null,
    action_group_id uuid not null,
    action_id uuid not null,
    sequence_no integer not null check (sequence_no >= 1),
    primary key (action_group_id, action_id),
    unique (action_group_id, sequence_no),
    foreign key (baby_id, episode_id, action_group_id)
        references baby_data.action_groups (baby_id, episode_id, action_group_id) on delete restrict,
    foreign key (baby_id, episode_id, action_id)
        references baby_data.action_attempts (baby_id, episode_id, action_id) on delete restrict
);

create table baby_data.outcomes (
    outcome_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null,
    action_id uuid,
    action_group_id uuid,
    observed_at timestamptz,
    response text not null check (response in ('CALMED', 'PARTLY_CALMED', 'NO_CHANGE', 'CRIED_AGAIN', 'UNKNOWN')),
    caregiver_interpretation text,
    created_by_user_id uuid not null references auth.users(id) on delete restrict,
    updated_by_user_id uuid not null references auth.users(id) on delete restrict,
    version bigint not null default 1 check (version >= 1),
    recorded_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    unique (baby_id, outcome_id),
    foreign key (baby_id, action_id)
        references baby_data.action_attempts (baby_id, action_id) on delete restrict,
    foreign key (baby_id, action_group_id)
        references baby_data.action_groups (baby_id, action_group_id) on delete restrict,
    check ((action_id is not null)::integer + (action_group_id is not null)::integer = 1)
);

create table baby_data.raw_care_entries (
    entry_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null references baby_data.babies(baby_id) on delete restrict,
    episode_id uuid,
    author_user_id uuid not null references auth.users(id) on delete restrict,
    input_mode baby_data.input_mode not null,
    choices jsonb not null default '[]'::jsonb check (jsonb_typeof(choices) = 'array'),
    raw_text text check (raw_text is null or char_length(raw_text) <= 2000),
    occurred_at timestamptz,
    time_precision baby_data.time_precision not null,
    input_revision bigint not null default 1 check (input_revision >= 1),
    status baby_data.raw_entry_status not null default 'DRAFT',
    supersedes_entry_id uuid,
    confirmed_by_user_id uuid references auth.users(id) on delete restrict,
    confirmed_at timestamptz,
    version bigint not null default 1 check (version >= 1),
    recorded_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    unique (baby_id, entry_id),
    foreign key (baby_id, episode_id)
        references baby_data.episodes (baby_id, episode_id) on delete restrict,
    foreign key (baby_id, supersedes_entry_id)
        references baby_data.raw_care_entries (baby_id, entry_id) on delete restrict,
    check (
        (input_mode = 'CHOICE' and raw_text is null and jsonb_array_length(choices) > 0)
        or (input_mode = 'TEXT' and raw_text is not null and jsonb_array_length(choices) = 0)
        or (input_mode = 'MIXED' and raw_text is not null and jsonb_array_length(choices) > 0)
    ),
    check ((status = 'CONFIRMED') = (confirmed_by_user_id is not null and confirmed_at is not null))
);

alter table baby_data.care_events
    add constraint care_events_source_entry_same_baby_fk
    foreign key (baby_id, source_entry_id)
    references baby_data.raw_care_entries (baby_id, entry_id) on delete restrict;

alter table baby_data.action_groups
    add constraint action_groups_source_entry_same_baby_fk
    foreign key (baby_id, source_entry_id)
    references baby_data.raw_care_entries (baby_id, entry_id) on delete restrict;

create table baby_data.normalization_runs (
    run_id uuid primary key,
    baby_id uuid not null,
    entry_id uuid not null,
    input_revision bigint not null check (input_revision >= 1),
    status baby_data.normalization_status not null default 'RUNNING',
    execution_token uuid,
    lease_expires_at timestamptz,
    execution_mode baby_data.execution_mode not null,
    provider text,
    model text,
    prompt_version text not null,
    schema_version text not null,
    ontology_version text not null,
    result jsonb,
    failure jsonb,
    recorded_at timestamptz not null default clock_timestamp(),
    completed_at timestamptz,
    unique (baby_id, run_id),
    foreign key (baby_id, entry_id)
        references baby_data.raw_care_entries (baby_id, entry_id) on delete restrict,
    check ((status = 'RUNNING') = (lease_expires_at is not null)),
    check ((status in ('COMPLETE', 'FAILED', 'STALE')) = (completed_at is not null)),
    check (status <> 'COMPLETE' or (result is not null and jsonb_typeof(result) = 'object')),
    check (status <> 'FAILED' or failure is not null),
    check (execution_mode <> 'STUB' or provider is null)
);

create table baby_data.state_observations (
    state_observation_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null references baby_data.babies(baby_id) on delete restrict,
    episode_id uuid,
    action_id uuid,
    source_entry_id uuid,
    phase baby_data.observation_phase not null,
    observed_at timestamptz,
    state_codes text[] not null check (cardinality(state_codes) > 0),
    source text not null check (source in ('DIRECT', 'REPORTED_BY_OTHER', 'NORMALIZED_TEXT')),
    created_by_user_id uuid not null references auth.users(id) on delete restrict,
    confirmed_by_user_id uuid not null references auth.users(id) on delete restrict,
    version bigint not null default 1 check (version >= 1),
    recorded_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    unique (baby_id, state_observation_id),
    foreign key (baby_id, episode_id)
        references baby_data.episodes (baby_id, episode_id) on delete restrict,
    foreign key (baby_id, action_id)
        references baby_data.action_attempts (baby_id, action_id) on delete restrict,
    foreign key (baby_id, source_entry_id)
        references baby_data.raw_care_entries (baby_id, entry_id) on delete restrict
);

create table baby_data.label_annotations (
    annotation_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null,
    entry_id uuid not null,
    run_id uuid,
    code text not null,
    value jsonb not null,
    source text not null check (source in ('CHOICE', 'EXTRACTED', 'EDITED', 'MANUAL')),
    evidence jsonb not null default '[]'::jsonb check (jsonb_typeof(evidence) = 'array'),
    confirmed_by_user_id uuid not null references auth.users(id) on delete restrict,
    revision bigint not null default 1 check (revision >= 1),
    status text not null check (status in ('ACTIVE', 'SUPERSEDED', 'DELETING', 'DELETED')),
    recorded_at timestamptz not null default clock_timestamp(),
    unique (baby_id, annotation_id),
    foreign key (baby_id, entry_id)
        references baby_data.raw_care_entries (baby_id, entry_id) on delete restrict,
    foreign key (baby_id, run_id)
        references baby_data.normalization_runs (baby_id, run_id) on delete restrict
);

create table baby_data.reminder_settings (
    reminder_setting_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null references baby_data.babies(baby_id) on delete restrict,
    recipient_user_id uuid not null references auth.users(id) on delete restrict,
    kind baby_data.reminder_kind not null,
    enabled boolean not null default false,
    options jsonb not null default '{}'::jsonb check (jsonb_typeof(options) = 'object'),
    version bigint not null default 1 check (version >= 1),
    recorded_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    unique (baby_id, recipient_user_id, kind),
    unique (baby_id, reminder_setting_id)
);

create table baby_data.reminders (
    reminder_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null references baby_data.babies(baby_id) on delete restrict,
    recipient_user_id uuid not null references auth.users(id) on delete restrict,
    kind baby_data.reminder_kind not null,
    anchor_event_id uuid,
    state baby_data.reminder_state not null,
    due_at timestamptz not null,
    snoozed_until timestamptz,
    seen_at timestamptz,
    evidence_refs jsonb not null default '[]'::jsonb check (jsonb_typeof(evidence_refs) = 'array'),
    policy_version text not null,
    version bigint not null default 1 check (version >= 1),
    recorded_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    unique (baby_id, reminder_id),
    foreign key (baby_id, anchor_event_id)
        references baby_data.care_events (baby_id, care_event_id) on delete restrict
);

create unique index reminders_no_duplicate_active_delivery
    on baby_data.reminders (baby_id, recipient_user_id, kind, anchor_event_id)
    where state in ('SCHEDULED', 'DUE', 'SNOOZED');

create table baby_data.record_coverage (
    record_coverage_id uuid primary key default extensions.gen_random_uuid(),
    baby_id uuid not null references baby_data.babies(baby_id) on delete restrict,
    date date not null,
    kind baby_data.reminder_kind not null,
    confirmed boolean not null,
    created_by_user_id uuid not null references auth.users(id) on delete restrict,
    version bigint not null default 1 check (version >= 1),
    recorded_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    unique (baby_id, date, kind, created_by_user_id),
    unique (baby_id, record_coverage_id)
);

create table baby_data.deletion_jobs (
    deletion_job_id uuid primary key default extensions.gen_random_uuid(),
    requester_user_id uuid not null references auth.users(id) on delete restrict,
    baby_id uuid not null references baby_data.babies(baby_id) on delete restrict,
    scope baby_data.deletion_scope not null,
    resource_id uuid,
    status baby_data.deletion_status not null default 'PENDING',
    access_blocked boolean not null default true check (access_blocked = true),
    requested_at timestamptz not null default clock_timestamp(),
    completed_at timestamptz,
    failure jsonb,
    pending_categories text[] not null default '{}',
    attempt_no integer not null default 1 check (attempt_no >= 1),
    version bigint not null default 1 check (version >= 1),
    unique (baby_id, deletion_job_id),
    check ((status = 'COMPLETE') = (completed_at is not null)),
    check (status <> 'FAILED' or failure is not null),
    check ((scope = 'ALL' and resource_id is null) or (scope <> 'ALL' and resource_id is not null))
);

create index care_events_timeline_idx on baby_data.care_events (baby_id, occurred_at desc, recorded_at desc);
create index episodes_timeline_idx on baby_data.episodes (baby_id, started_at desc nulls last, recorded_at desc);
create index analyses_episode_idx on baby_data.analyses (baby_id, episode_id, recorded_at desc);
create index raw_care_entries_author_idx on baby_data.raw_care_entries (baby_id, author_user_id, updated_at desc);
create index deletion_jobs_requester_idx on baby_data.deletion_jobs (requester_user_id, requested_at desc);

create or replace function baby_private.enforce_status_transition()
returns trigger
language plpgsql
set search_path = pg_catalog, baby_data
as $$
declare
    old_status text := old.status::text;
    new_status text := new.status::text;
    allowed boolean := false;
begin
    if old_status = new_status then
        return new;
    end if;

    allowed := case tg_table_name
        when 'babies' then old_status = 'ACTIVE' and new_status = 'DELETING'
            or old_status = 'DELETING' and new_status = 'DELETED'
        when 'baby_memberships' then old_status = 'ACTIVE' and new_status in ('LEFT', 'REVOKED')
        when 'invitations' then old_status = 'PENDING' and new_status in ('ACCEPTED', 'EXPIRED', 'REVOKED')
        when 'observation_sessions' then old_status = 'ACTIVE' and new_status in ('STOPPED', 'EXPIRED')
        when 'episodes' then old_status = 'OPEN' and new_status = 'CLOSED'
        when 'audio_assets' then
            old_status in ('ALLOCATED', 'VERIFYING', 'READY', 'REJECTED') and new_status = 'DELETING'
            or old_status = 'ALLOCATED' and new_status = 'VERIFYING'
            or old_status = 'VERIFYING' and new_status in ('READY', 'REJECTED')
            or old_status = 'DELETING' and new_status = 'DELETED'
        when 'analyses' then
            old_status = 'READY' and new_status = 'RUNNING'
            or old_status = 'RUNNING' and new_status in ('COMPLETE', 'ABSTAIN', 'FAILED')
            or old_status = 'FAILED' and new_status = 'RUNNING'
        when 'care_events' then old_status = 'ACTIVE' and new_status = 'DELETING'
            or old_status = 'DELETING' and new_status = 'DELETED'
        when 'action_attempts' then old_status = 'ACTIVE' and new_status = 'DELETING'
            or old_status = 'DELETING' and new_status = 'DELETED'
        when 'raw_care_entries' then
            old_status = 'DRAFT' and new_status in ('NORMALIZING', 'CONFIRMED', 'DELETING')
            or old_status = 'NORMALIZING' and new_status in ('REVIEW_READY', 'NEEDS_MANUAL_REVIEW', 'DRAFT', 'DELETING')
            or old_status in ('REVIEW_READY', 'NEEDS_MANUAL_REVIEW') and new_status in ('DRAFT', 'NORMALIZING', 'CONFIRMED', 'DELETING')
            or old_status = 'CONFIRMED' and new_status = 'DELETING'
            or old_status = 'DELETING' and new_status = 'DELETED'
        when 'normalization_runs' then old_status = 'RUNNING' and new_status in ('COMPLETE', 'FAILED', 'STALE')
            or old_status in ('COMPLETE', 'FAILED') and new_status = 'STALE'
        when 'deletion_jobs' then
            old_status = 'PENDING' and new_status = 'RUNNING'
            or old_status = 'RUNNING' and new_status in ('COMPLETE', 'FAILED')
            or old_status = 'FAILED' and new_status = 'RUNNING'
        else false
    end;

    if not allowed then
        raise exception using
            errcode = '23514',
            message = format('invalid %s status transition: %s -> %s', tg_table_name, old_status, new_status);
    end if;
    return new;
end;
$$;

create trigger babies_status_transition before update of status on baby_data.babies
    for each row execute function baby_private.enforce_status_transition();
create trigger memberships_status_transition before update of status on baby_data.baby_memberships
    for each row execute function baby_private.enforce_status_transition();
create trigger invitations_status_transition before update of status on baby_data.invitations
    for each row execute function baby_private.enforce_status_transition();
create trigger observation_sessions_status_transition before update of status on baby_data.observation_sessions
    for each row execute function baby_private.enforce_status_transition();
create trigger episodes_status_transition before update of status on baby_data.episodes
    for each row execute function baby_private.enforce_status_transition();
create trigger audio_assets_status_transition before update of status on baby_data.audio_assets
    for each row execute function baby_private.enforce_status_transition();
create trigger analyses_status_transition before update of status on baby_data.analyses
    for each row execute function baby_private.enforce_status_transition();
create trigger care_events_status_transition before update of status on baby_data.care_events
    for each row execute function baby_private.enforce_status_transition();
create trigger action_attempts_status_transition before update of status on baby_data.action_attempts
    for each row execute function baby_private.enforce_status_transition();
create trigger raw_entries_status_transition before update of status on baby_data.raw_care_entries
    for each row execute function baby_private.enforce_status_transition();
create trigger normalization_runs_status_transition before update of status on baby_data.normalization_runs
    for each row execute function baby_private.enforce_status_transition();
create trigger deletion_jobs_status_transition before update of status on baby_data.deletion_jobs
    for each row execute function baby_private.enforce_status_transition();

create or replace function baby_private.assert_active_owner()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, baby_data
as $$
declare
    target_baby_id uuid;
    expected_owner_id uuid;
    active_owner_count integer;
    matching_owner_count integer;
begin
    if tg_table_name = 'babies' then
        target_baby_id := case when tg_op = 'DELETE' then old.baby_id else new.baby_id end;
    else
        target_baby_id := case when tg_op = 'DELETE' then old.baby_id else new.baby_id end;
    end if;

    select owner_user_id
      into expected_owner_id
      from baby_data.babies
     where baby_id = target_baby_id
       and status = 'ACTIVE';

    if expected_owner_id is null then
        return null;
    end if;

    select count(*), count(*) filter (where user_id = expected_owner_id)
      into active_owner_count, matching_owner_count
      from baby_data.baby_memberships
     where baby_id = target_baby_id
       and role = 'OWNER'
       and status = 'ACTIVE';

    if active_owner_count <> 1 or matching_owner_count <> 1 then
        raise exception using
            errcode = '23514',
            message = format('ACTIVE baby %s must have exactly one matching ACTIVE OWNER', target_baby_id);
    end if;
    return null;
end;
$$;

create constraint trigger babies_require_active_owner
after insert or update of owner_user_id, status on baby_data.babies
deferrable initially deferred
for each row execute function baby_private.assert_active_owner();

create constraint trigger memberships_require_active_owner
after insert or update of baby_id, user_id, role, status or delete on baby_data.baby_memberships
deferrable initially deferred
for each row execute function baby_private.assert_active_owner();

comment on schema baby_data is 'B-03 private business data. Not exposed through the browser Data API.';
comment on schema baby_private is 'B-03 internal authorization helpers. Never expose as a Data API schema.';
comment on table baby_data.audio_upload_grants is 'Exact, short-lived Storage upload authorization; B-05 completes and verifies the uploaded object.';
comment on table baby_data.revoked_sessions is 'Application-side session revocation gate. Token lifetime and logout API remain separate contract work.';
comment on table baby_data.raw_care_entries is 'Author-only, unconfirmed source input. Confirmed shared records are separate rows.';
