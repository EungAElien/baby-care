create extension if not exists pgtap with schema extensions;

begin;
set local search_path = extensions, public;
-- Supabase's local postgres role is a NOINHERIT member without SET OPTION.
-- Enable SET ROLE only inside this rolled-back test transaction.
grant baby_app to postgres with set true;
grant usage on schema extensions to baby_app;

select plan(51);

select is(has_schema_privilege('anon', 'baby_data', 'USAGE'), false,
    'anon has no business schema access');
select is(has_schema_privilege('authenticated', 'baby_data', 'USAGE'), false,
    'authenticated has no business schema access');
select is(has_table_privilege('anon', 'baby_data.babies', 'SELECT'), false,
    'anon has no direct business table read');
select is(has_table_privilege('authenticated', 'baby_data.babies', 'SELECT'), false,
    'authenticated has no direct business table read');
select is(has_table_privilege('service_role', 'baby_data.babies', 'SELECT'), false,
    'service_role is not the application database role');
select is(has_table_privilege('anon', 'baby_data.shared_change_feed_state', 'SELECT'), false,
    'anon cannot inspect feed revisions');
select is(has_table_privilege('authenticated', 'baby_data.shared_changes', 'SELECT'), false,
    'browser authenticated role cannot inspect change identifiers');
select is(has_table_privilege('service_role', 'baby_data.shared_changes', 'SELECT'), false,
    'service role is not a shared-change data path');
select is(has_function_privilege(
    'baby_app', 'baby_private.record_shared_changes(uuid,jsonb)', 'EXECUTE'
), true, 'application role can invoke only the checked feed writer');
select is(has_function_privilege(
    'baby_app', 'baby_private.record_shared_changes_unchecked(uuid,jsonb)', 'EXECUTE'
), false, 'application role cannot bypass active membership in the feed writer');
select is(has_function_privilege(
    'baby_app', 'baby_private.prune_shared_change_history(timestamp with time zone)', 'EXECUTE'
), false, 'application requests cannot move the retained-history floor');
select is(has_function_privilege(
    'authenticated', 'baby_private.current_request_user_id()', 'EXECUTE'
), false, 'authenticated cannot invoke server request-context helpers');
select is(has_function_privilege(
    'anon', 'baby_private.can_upload_audio_object(text,text)', 'EXECUTE'
), false, 'anon cannot invoke the Storage upload gate');
select is(has_function_privilege(
    'authenticated', 'baby_private.can_upload_audio_object(text,text)', 'EXECUTE'
), true, 'authenticated can invoke the boolean Storage upload gate');
select is(has_function_privilege(
    'authenticated', 'baby_private.deny_browser_storage_read()', 'EXECUTE'
), true, 'authenticated read attempts reach only the explicit Storage denial guard');
select is(
    (
        select count(*)::integer
          from pg_views
         where schemaname in ('public', 'graphql_public')
           and definition like '%baby_data.%'
    ),
    0,
    'no exposed view bypasses the private business schema'
);

set local role baby_app;
select set_config('baby.request_user_id', '10000000-0000-4000-8000-000000000001', true);
select set_config('baby.request_session_id', '10000000-0000-4000-8000-000000000011', true);

select is((select count(*)::integer from baby_data.babies), 2,
    'owner sees both babies for which current DB membership is active');
select is((select count(*)::integer from baby_data.raw_care_entries), 2,
    'owner sees own draft and confirmed shared entry only');
select is((select count(*)::integer from baby_data.raw_care_entries
           where entry_id = '10000000-0000-4000-8000-000000000702'), 0,
    'owner cannot see another caregiver unconfirmed draft');
select is((select count(*)::integer from baby_data.reminder_settings), 1,
    'owner sees only personal reminder settings');
select is((select count(*)::integer from baby_data.normalization_runs), 0,
    'owner cannot see another caregiver normalization draft');
select is((select count(*)::integer from baby_data.shared_change_feed_state), 2,
    'owner sees feed state only for the two active baby memberships');
select is((select count(*)::integer from baby_data.shared_changes), 0,
    'an empty retained history reveals no synthetic resource identifiers');
select throws_ok(
    $$
    update baby_data.shared_change_feed_state
       set current_revision = current_revision + 100
    $$,
    '42501',
    null,
    'lock-only UPDATE policy rejects a direct feed counter mutation'
);
select is(
    (select max(current_revision)::integer from baby_data.shared_change_feed_state),
    1,
    'row-lock privilege cannot mutate the feed counter directly'
);

reset role;
set local role baby_app;
select set_config('baby.request_user_id', '10000000-0000-4000-8000-000000000002', true);
select set_config('baby.request_session_id', '10000000-0000-4000-8000-000000000012', true);

select is((select count(*)::integer from baby_data.babies), 1,
    'caregiver sees the active baby membership permits');
select is((select count(*)::integer from baby_data.babies
           where baby_id = '10000000-0000-4000-8000-000000000102'), 0,
    'caregiver cannot see another baby');
select is((select count(*)::integer from baby_data.raw_care_entries), 2,
    'caregiver sees own draft and confirmed shared entry');
select is((select count(*)::integer from baby_data.raw_care_entries
           where entry_id = '10000000-0000-4000-8000-000000000701'), 0,
    'caregiver cannot see owner unconfirmed draft');
select is((select count(*)::integer from baby_data.reminder_settings), 1,
    'caregiver sees only personal reminder settings');
select is((select count(*)::integer from baby_data.normalization_runs), 1,
    'caregiver sees normalization tied to own entry');
select is((select count(*)::integer from baby_data.shared_change_feed_state), 1,
    'caregiver sees only the authorized baby feed boundary');
select lives_ok(
    $$
    insert into baby_data.raw_care_entries (
        entry_id, baby_id, episode_id, author_user_id, input_mode, choices,
        raw_text, time_precision, status, confirmed_by_user_id, confirmed_at, version
    ) values (
        '10000000-0000-4000-8000-000000009101',
        '10000000-0000-4000-8000-000000000101',
        '10000000-0000-4000-8000-000000000301',
        '10000000-0000-4000-8000-000000000001',
        'TEXT', '[]', 'actor fields are stamped', 'UNKNOWN', 'CONFIRMED',
        '10000000-0000-4000-8000-000000000001', clock_timestamp(), 1
    )
    $$,
    'caregiver can create a confirmed entry through the scoped server role'
);
select is(
    (
        select author_user_id::text || ':' || confirmed_by_user_id::text
          from baby_data.raw_care_entries
         where entry_id = '10000000-0000-4000-8000-000000009101'
    ),
    '10000000-0000-4000-8000-000000000002:10000000-0000-4000-8000-000000000002',
    'server triggers replace spoofed author and confirmer fields'
);
select throws_ok(
    $$
    update baby_data.raw_care_entries
       set confirmed_by_user_id = '10000000-0000-4000-8000-000000000001',
           version = version + 1
     where entry_id = '10000000-0000-4000-8000-000000009101'
    $$,
    '23514',
    null,
    'confirmed actor is immutable after persistence'
);
select throws_ok(
    $$
    update baby_data.care_events
       set created_by_user_id = '10000000-0000-4000-8000-000000000001',
           version = version + 1
     where care_event_id = '10000000-0000-4000-8000-000000000602'
    $$,
    '23514',
    null,
    'original record author is immutable'
);
select lives_ok(
    $$
    update baby_data.care_events
       set payload = '{"condition":"WET","changed":true}',
           version = version + 1
     where care_event_id = '10000000-0000-4000-8000-000000000602'
    $$,
    'caregiver can update own shared record'
);
select is((select updated_by_user_id from baby_data.care_events
           where care_event_id = '10000000-0000-4000-8000-000000000602'),
          '10000000-0000-4000-8000-000000000002'::uuid,
          'server trigger preserves the actual caregiver updater');
select lives_ok(
    $$
    update baby_data.care_events
       set payload = '{"amount_ml":90}', version = version + 1
     where care_event_id = '10000000-0000-4000-8000-000000000601'
    $$,
    'forbidden update is filtered without leaking another author row'
);
select is((select version from baby_data.care_events
           where care_event_id = '10000000-0000-4000-8000-000000000601'),
          1::bigint,
          'caregiver cannot update another author shared record');

reset role;
set local role baby_app;
select set_config('baby.request_user_id', '10000000-0000-4000-8000-000000000001', true);
select set_config('baby.request_session_id', '10000000-0000-4000-8000-000000000011', true);

select lives_ok(
    $$
    update baby_data.care_events
       set payload = '{"condition":"DRY","owner_corrected":true}',
           version = version + 1
     where care_event_id = '10000000-0000-4000-8000-000000000602'
    $$,
    'owner can update another caregiver confirmed shared record'
);
select is((select updated_by_user_id from baby_data.care_events
           where care_event_id = '10000000-0000-4000-8000-000000000602'),
          '10000000-0000-4000-8000-000000000001'::uuid,
          'server trigger preserves the actual owner updater');

reset role;
set local role baby_app;
select set_config('baby.request_user_id', '10000000-0000-4000-8000-000000000005', true);
select set_config('baby.request_session_id', '10000000-0000-4000-8000-000000000015', true);

select is((select count(*)::integer from baby_data.consents), 0,
    'revoked session cannot use the former-member rights path');

reset role;
set local role baby_app;
select set_config('baby.request_user_id', '10000000-0000-4000-8000-000000000005', true);
select set_config('baby.request_session_id', '10000000-0000-4000-8000-000000000016', true);

select is((select count(*)::integer from baby_data.babies), 0,
    'removed member cannot read ordinary baby data');
select is((select count(*)::integer from baby_data.consents
           where actor_user_id = '10000000-0000-4000-8000-000000000005'), 1,
    'removed member retains access to own consent history');
select is((select count(*)::integer from baby_data.deletion_jobs), 1,
    'removed member retains access to own deletion progress');
select is((select count(*)::integer from baby_data.shared_change_feed_state), 0,
    'removed member cannot inspect shared feed revisions');
select is((select count(*)::integer from baby_data.shared_changes), 0,
    'removed member cannot inspect shared resource identifiers');

reset role;
set local role baby_app;
select set_config('baby.request_user_id', '', true);
select set_config('baby.request_session_id', '', true);
select is((select count(*)::integer from baby_data.babies), 0,
    'missing request context fails closed');

reset role;
update baby_data.baby_memberships
   set status = 'REVOKED', version = version + 1
 where membership_id = '10000000-0000-4000-8000-000000000202';

set local role baby_app;
select set_config('baby.request_user_id', '10000000-0000-4000-8000-000000000002', true);
select set_config('baby.request_session_id', '10000000-0000-4000-8000-000000000012', true);
select is((select count(*)::integer from baby_data.babies), 0,
    'membership revocation blocks the next query in the existing session');

reset role;
update baby_data.babies
   set status = 'DELETING', version = version + 1
 where baby_id = '10000000-0000-4000-8000-000000000101';

set local role baby_app;
select set_config('baby.request_user_id', '10000000-0000-4000-8000-000000000001', true);
select set_config('baby.request_session_id', '10000000-0000-4000-8000-000000000011', true);
select is((select count(*)::integer from baby_data.babies
           where baby_id = '10000000-0000-4000-8000-000000000101'), 0,
    'baby deletion state blocks the next query in the existing session');

reset role;
select * from finish();
rollback;
