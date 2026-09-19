create extension if not exists pgtap with schema extensions;

begin;
set local search_path = extensions, public;

select plan(41);

select has_schema('baby_data', 'private business schema exists');
select has_schema('baby_private', 'private helper schema exists');
select has_table('baby_data', 'babies', 'babies table exists');
select has_table('baby_data', 'baby_memberships', 'membership table exists');
select has_table('baby_data', 'invitations', 'invitation table exists');
select has_table('baby_data', 'consents', 'consent history table exists');
select has_table('baby_data', 'episodes', 'episode table exists');
select has_table('baby_data', 'audio_assets', 'audio asset table exists');
select has_table('baby_data', 'audio_upload_grants', 'upload grant table exists');
select has_table('baby_data', 'analyses', 'analysis table exists');
select has_table('baby_data', 'care_events', 'care event table exists');
select has_table('baby_data', 'action_attempts', 'action attempt table exists');
select has_table('baby_data', 'outcomes', 'outcome table exists');
select has_table('baby_data', 'raw_care_entries', 'raw entry table exists');
select has_table('baby_data', 'normalization_runs', 'normalization run table exists');
select has_table('baby_data', 'state_observations', 'state observation table exists');
select has_table('baby_data', 'reminder_settings', 'personal reminder settings table exists');
select has_table('baby_data', 'deletion_jobs', 'deletion job table exists');
select has_table('baby_data', 'idempotency_records', 'durable idempotency table exists');
select has_table('baby_data', 'security_attempts', 'security rate-limit table exists');
select has_table('baby_data', 'reauthentication_challenges', 'reauthentication challenge table exists');
select has_table('baby_data', 'reauthentication_proofs', 'one-time reauthentication proof table exists');
select has_table('baby_data', 'session_revocation_jobs', 'provider revocation status table exists');
select has_table('baby_data', 'observed_auth_sessions', 'least-privilege session registry exists');
select has_table('baby_data', 'session_revocation_rules', 'session cutoff rule table exists');
select has_table('baby_data', 'guardian_verifications', 'child-data gate table exists');
select has_table('baby_data', 'resource_invalidations', 'derived-resource invalidation table exists');

select is(
    (
        select rolcanlogin::text || ':' || rolbypassrls::text
          from pg_roles
         where rolname = 'baby_app'
    ),
    'false:false',
    'baby_app cannot login directly or bypass RLS'
);

select is(
    (
        select rolcanlogin::text || ':' || rolbypassrls::text
          from pg_roles
         where rolname = 'baby_policy_owner'
    ),
    'false:true',
    'policy owner cannot login and is isolated from runtime membership'
);

select is(
    (
        select count(*)::integer
          from pg_class c
          join pg_namespace n on n.oid = c.relnamespace
         where n.nspname = 'baby_data'
           and c.relkind = 'r'
           and c.relrowsecurity
           and c.relforcerowsecurity
    ),
    37,
    'every business table has FORCE ROW LEVEL SECURITY'
);

select is(
    (select public from storage.buckets where id = 'baby-audio'),
    false,
    'audio bucket is private'
);

select is(
    (select file_size_limit::bigint from storage.buckets where id = 'baby-audio'),
    25000000::bigint,
    'audio bucket uses the exact 25,000,000-byte ceiling'
);

select throws_ok(
    $$
    insert into baby_data.baby_memberships (
        membership_id, baby_id, user_id, role, relationship, display_name, status, version
    ) values (
        '10000000-0000-4000-8000-000000009001',
        '10000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000002',
        'CAREGIVER', 'OTHER', 'duplicate', 'ACTIVE', 1
    )
    $$,
    '23505',
    null,
    'a user cannot have duplicate active membership for one baby'
);

select throws_ok(
    $$
    insert into baby_data.baby_memberships (
        membership_id, baby_id, user_id, role, relationship, display_name, status, version
    ) values (
        '10000000-0000-4000-8000-000000009002',
        '10000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000004',
        'OWNER', 'OTHER', 'second owner', 'ACTIVE', 1
    )
    $$,
    '23505',
    null,
    'a baby cannot have two active owners'
);

select throws_ok(
    $$
    insert into baby_data.action_attempts (
        action_id,
        baby_id,
        episode_id,
        care_event_id,
        performed_at,
        sequence_no,
        created_by_user_id,
        updated_by_user_id,
        version
    ) values (
        '10000000-0000-4000-8000-000000009003',
        '10000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000301',
        '10000000-0000-4000-8000-000000000603',
        clock_timestamp(),
        1,
        '10000000-0000-4000-8000-000000000001',
        '10000000-0000-4000-8000-000000000001',
        1
    )
    $$,
    '23503',
    null,
    'an action cannot link a care event from another baby'
);

select throws_ok(
    $$
    with new_audio as (
        insert into baby_data.audio_assets (
            audio_id, baby_id, episode_id, created_by_user_id, object_key,
            mime_type, bytes, status, data_origin, version
        ) values (
            '10000000-0000-4000-8000-000000009004',
            '10000000-0000-4000-8000-000000000101',
            '10000000-0000-4000-8000-000000000301',
            '10000000-0000-4000-8000-000000000001',
            'test/oversize/10000000-0000-4000-8000-000000009004',
            'audio/wav', 1, 'ALLOCATED', 'DEMO', 1
        ) returning 1
    )
    insert into baby_data.audio_upload_grants (
        upload_id, baby_id, episode_id, audio_id, uploader_user_id, auth_session_id,
        object_key, method, max_bytes, expires_at
    )
    select
        '10000000-0000-4000-8000-000000009005',
        '10000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000301',
        '10000000-0000-4000-8000-000000009004',
        '10000000-0000-4000-8000-000000000001',
        '10000000-0000-4000-8000-000000000011',
        'test/oversize/10000000-0000-4000-8000-000000009004',
        'STANDARD',
        25000001,
        clock_timestamp() + interval '15 minutes'
    from new_audio
    $$,
    '23514',
    null,
    'upload grants cannot exceed 25,000,000 bytes'
);

select throws_ok(
    $$
    with new_audio as (
        insert into baby_data.audio_assets (
            audio_id, baby_id, episode_id, created_by_user_id, object_key,
            mime_type, bytes, status, data_origin, version
        ) values (
            '10000000-0000-4000-8000-000000009006',
            '10000000-0000-4000-8000-000000000101',
            '10000000-0000-4000-8000-000000000301',
            '10000000-0000-4000-8000-000000000001',
            'test/expired/10000000-0000-4000-8000-000000009006',
            'audio/wav', 1, 'ALLOCATED', 'DEMO', 1
        ) returning 1
    )
    insert into baby_data.audio_upload_grants (
        upload_id, baby_id, episode_id, audio_id, uploader_user_id, auth_session_id,
        object_key, method, max_bytes, expires_at
    )
    select
        '10000000-0000-4000-8000-000000009007',
        '10000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000301',
        '10000000-0000-4000-8000-000000009006',
        '10000000-0000-4000-8000-000000000001',
        '10000000-0000-4000-8000-000000000011',
        'test/expired/10000000-0000-4000-8000-000000009006',
        'STANDARD',
        25000000,
        clock_timestamp() + interval '15 minutes 1 second'
    from new_audio
    $$,
    '23514',
    null,
    'upload grants cannot outlive the 15-minute allowance'
);

select throws_ok(
    $$
    update baby_data.babies
       set status = 'DELETED', version = version + 1
     where baby_id = '10000000-0000-4000-8000-000000000101'
    $$,
    '23514',
    null,
    'baby deletion cannot skip DELETING'
);

set constraints all immediate;

select throws_ok(
    $$
    update baby_data.baby_memberships
       set status = 'REVOKED', version = version + 1
     where membership_id = '10000000-0000-4000-8000-000000000201'
    $$,
    '23514',
    null,
    'an ACTIVE baby cannot lose its matching ACTIVE owner'
);

select is(
    (
        select count(*)::integer
          from baby_data.baby_memberships
         where status = 'ACTIVE' and role = 'OWNER'
    ),
    2,
    'the seed has one active owner for each active baby'
);

select ok(
    not exists (
        select 1
          from baby_data.babies b
          left join baby_data.baby_memberships m
            on m.baby_id = b.baby_id
           and m.user_id = b.owner_user_id
           and m.role = 'OWNER'
           and m.status = 'ACTIVE'
         where b.status = 'ACTIVE'
           and m.membership_id is null
    ),
    'each active baby owner_user_id matches its active owner membership'
);

select * from finish();
rollback;
