from __future__ import annotations

import copy
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
SCHEMAS = {}
PATHS = {}
REGISTRY = {}

def ref(name): return {'$ref': f'#/components/schemas/{name}'}
def enum(*values): return {'type': 'string', 'enum': list(values)}
def arr(value, **kwargs): return {'type': 'array', 'items': value, **kwargs}
def nullable(value): return {'anyOf': [value, {'type': 'null'}]}
def obj(properties, optional=(), **kwargs):
    return {'type': 'object', 'properties': properties,
            'required': [x for x in properties if x not in optional],
            'additionalProperties': False, **kwargs}
def schema(name, properties, optional=(), **kwargs):
    SCHEMAS[name] = obj(properties, optional, **kwargs)
    return ref(name)
def mutation(name, properties, optional=(), description=None):
    return schema(name, {'client_request_id': UUID, **properties}, optional,
                  **({'description': description} if description else {}))
def page(name): return schema(name+'Page', {'items': arr(ref(name)), 'next_cursor': nullable(STR)})
def body(schema_name): return {'required': True, 'content': {'application/json': {'schema': ref(schema_name)}}}

STR = {'type': 'string'}
TEXT = {'type': 'string', 'minLength': 1}
UUID = {'type': 'string', 'format': 'uuid'}
TIME = {'type': 'string', 'format': 'date-time'}
DATE = {'type': 'string', 'format': 'date'}
BOOL = {'type': 'boolean'}
INT = {'type': 'integer', 'minimum': 1}
NONNEG = {'type': 'integer', 'minimum': 0}
NUM = {'type': 'number', 'minimum': 0}
URI = {'type': 'string', 'format': 'uri-reference'}
ORIGIN = enum('USER','DEMO')
LIFECYCLE = enum('ACTIVE','DELETING','DELETED')
FEEDING = enum('BREAST','FORMULA','MIXED','UNSPECIFIED')
ACTION = enum('FEEDING','DIAPER_CHECK','DIAPER_CHANGE','HOLDING','BURPING','SLEEP_PREPARATION','ENVIRONMENT_ADJUSTMENT','OTHER')
STATE = enum('CRYING','FUSSING','CALM','SLEEPY_APPEARING','ASLEEP','AWAKE','CHEERFUL_APPEARING','UNKNOWN')
RESPONSE = enum('CALMED','PARTIALLY_CALMED','NO_CHANGE','CRYING_AGAIN','UNKNOWN')
PRECISION = enum('EXACT','RELATIVE','UNKNOWN')
PHASE = enum('BEFORE','AFTER','UNRELATED','UNKNOWN')
QUALITY_REASONS=enum('TOO_SHORT','SILENCE','CLIPPING','HIGH_NOISE','NO_CRY','UNSUPPORTED_CODEC','DECODE_ERROR','TOO_LONG','TOO_LARGE')
VERSION_FIELDS = {'version': INT, 'recorded_at': TIME, 'updated_at': TIME}

schema('SourceRef', {'kind': enum('AUDIO','CARE_EVENT','ANALYSIS','OBSERVATION','PRIOR_CASE'), 'resource_id': UUID, 'version': nullable(INT)})
schema('RecordVersion', {'resource_type': enum('CARE_EVENT','OUTCOME','STATE_OBSERVATION','ACTION'), 'resource_id': UUID, 'version': INT})
schema('ModelInfo', {'available': BOOL, 'model_version': nullable(STR), 'preprocess_version': nullable(STR), 'label_mapping_version': nullable(STR), 'supported_labels': arr(STR), 'inference_mode': enum('REAL','STUB')})
schema('Capabilities', {'contract_version': {'const':'1.1.1','type':'string'}, 'audio_model': ref('ModelInfo'), 'supported_mime_types': arr(STR), 'upload_max_bytes': {'const':25000000,'type':'integer'}, 'upload_max_seconds': {'const':60,'type':'integer'}, 'normalizer_available': BOOL, 'automatic_detection_supported': BOOL})
schema('BrowserSupport', {'os':STR,'browser':STR,'tested_version':STR,'automatic_detection_supported':BOOL,'recording_supported':BOOL,'verified_at':TIME,'evidence_ref':STR})
schema('DetectorInfo', {'available':BOOL,'execution_mode':enum('REAL','STUB'),'model_version':nullable(STR),'model_asset_url':nullable(URI),'weights_sha256':nullable({'type':'string','pattern':'^[0-9a-f]{64}$'}),'input_sample_rate_hz':nullable(INT),'policy_version':nullable(STR),'supported_clients':arr(ref('BrowserSupport'))})
SCHEMAS['Capabilities']['properties']['detector']=ref('DetectorInfo')
SCHEMAS['Capabilities']['required'].append('detector')

schema('Baby', {'baby_id': UUID, 'owner_user_id': UUID, 'alias': {'type':'string','minLength':1,'maxLength':40}, 'birth_date': DATE, 'feeding_mode': FEEDING, 'timezone': STR, 'status': LIFECYCLE, 'context_revision': NONNEG, **VERSION_FIELDS})
schema('Membership', {'membership_id':UUID,'baby_id':UUID,'user_id':UUID,'role':enum('OWNER','CAREGIVER'),'relationship':enum('MOTHER','FATHER','GRANDPARENT','OTHER'),'display_name':STR,'status':enum('ACTIVE','LEFT','REVOKED'), **VERSION_FIELDS})
schema('BabyAccess', {'baby': ref('Baby'), 'membership':ref('Membership')})
schema('BabyList', {'items': arr(ref('BabyAccess'))})
schema('ActiveBaby', {'baby_id': nullable(UUID)})
mutation('CreateBaby', {'alias': {'type':'string','minLength':1,'maxLength':40},'birth_date':DATE,'feeding_mode':FEEDING,'timezone':STR})
mutation('PatchBaby', {'version':INT,'alias':{'type':'string','minLength':1,'maxLength':40},'birth_date':DATE,'feeding_mode':FEEDING,'timezone':STR}, ('alias','birth_date','feeding_mode','timezone'))
mutation('SetActiveBaby', {'baby_id':UUID})
mutation('PatchMembership', {'version':INT,'relationship':enum('MOTHER','FATHER','GRANDPARENT','OTHER')})
page('Membership')

schema('Invite', {'invite_id':UUID,'baby_id':UUID,'inviter_user_id':UUID,'email':{'type':'string','format':'email'},'status':enum('PENDING','ACCEPTED','EXPIRED','REVOKED'),'expires_at':TIME,**VERSION_FIELDS})
schema('IssuedInvite', {'invite':ref('Invite'),'invite_url':nullable(URI),'link_reissue_required':BOOL}, description='Plaintext token is returned only on initial creation. An idempotent replay returns invite_url=null and link_reissue_required=true; explicitly reissue to obtain a new link.')
mutation('CreateInvite', {'email':{'type':'string','format':'email'}})
mutation('ReissueInvite', {})
mutation('AcceptInvite', {'token':{'type':'string','minLength':32},'accept_shared_use':{'const':True,'type':'boolean'},'policy_version':TEXT,'relationship':enum('MOTHER','FATHER','GRANDPARENT','OTHER')})
page('Invite')

REAUTH_OPERATION = enum('CREATE_INVITE','DELETE_BABY','ENABLE_BABY_TRAINING')
schema('ReauthenticationChallenge', {'challenge_id':UUID,'user_id':UUID,'requested_session_id':UUID,'operation':REAUTH_OPERATION,'baby_id':UUID,'auth_method':{'const':'SUPABASE_OTP','type':'string'},'status':enum('PENDING','PROVED','EXPIRED'),'created_at':TIME,'expires_at':TIME}, description='A challenge is valid for 10 minutes. Complete a fresh Supabase email OTP sign-in; token_refresh alone is never proof.')
mutation('CreateReauthenticationChallenge', {'operation':REAUTH_OPERATION,'baby_id':UUID})
schema('ReauthenticationProof', {'proof_id':UUID,'challenge_id':UUID,'user_id':UUID,'session_id':UUID,'operation':REAUTH_OPERATION,'baby_id':UUID,'proof_token':nullable(STR),'token_reissue_required':BOOL,'issued_at':TIME,'expires_at':TIME}, description='The 5-minute proof is bound to user, OTP-authenticated session, operation, and baby. It is consumed atomically with the protected mutation. Idempotent replay recovers the prior mutation result without consuming it again.')
mutation('CreateReauthenticationProof', {'challenge_id':UUID})
schema('SessionRevocationFailure', {'code':enum('PROVIDER_REJECTED_REVOCATION','PROVIDER_UNREACHABLE'),'message':STR,'retryable':BOOL})
schema('SessionRevocation', {'revocation_id':UUID,'requester_user_id':UUID,'requester_session_id':UUID,'scope':enum('CURRENT','OTHERS','ALL'),'status':enum('PENDING','COMPLETE','FAILED'),'target_session_count':NONNEG,'provider_scope':enum('local','others','global'),'provider_http_status':nullable(NONNEG),'failure':nullable(ref('SessionRevocationFailure')),'access_blocked':{'const':True,'type':'boolean'},'requested_at':TIME,'completed_at':nullable(TIME)}, description='Local API and Storage access is blocked durably before provider refresh-session revocation. FAILED is not reported as logout success.')
mutation('RevokeSessions', {'scope':enum('CURRENT','OTHERS','ALL')})
schema('ChildDataVerification', {'baby_id':UUID,'subject_user_id':UUID,'status':enum('UNVERIFIED','SYNTHETIC_TEST_ONLY','VERIFIED'),'method':nullable(STR),'policy_version':nullable(STR),'verified_at':nullable(TIME),'production_processing_allowed':BOOL}, description='OWNER role and email OTP do not establish legal-guardian status. Production child-data processing remains disabled until an approved verification method and policy are configured.')

SCOPES = enum('SERVICE_PROCESSING','AUDIO_RETENTION','BABY_TRAINING','CONTRIBUTOR_TRAINING','SHARED_USE')
schema('Consent', {'consent_id':UUID,'baby_id':UUID,'actor_user_id':UUID,'scope':SCOPES,'status':enum('NOT_GRANTED','GRANTED','REVOKED'),'policy_version':STR,'granted_at':nullable(TIME),'revoked_at':nullable(TIME),'version':INT})
page('Consent')
mutation('SetBabyConsent', {'baby_id':UUID,'scope':enum('SERVICE_PROCESSING','AUDIO_RETENTION','BABY_TRAINING'),'granted':BOOL,'policy_version':TEXT,'version':NONNEG}, description='Initial version is 0. Only OWNER can set baby-scoped consent. Revocation can start cleanup.')
mutation('SetTrainingConsent', {'granted':BOOL,'policy_version':TEXT,'version':NONNEG}, description='Only the authenticated contributor. After membership ends, revocation is allowed but new opt-in is not.')

schema('Episode', {'episode_id':UUID,'baby_id':UUID,'created_by_user_id':UUID,'status':enum('OPEN','CLOSED'),'source':enum('AUTO','MANUAL','FILE'),'timing_status':enum('KNOWN','UNKNOWN'),'started_at':nullable(TIME),'ended_at':nullable(TIME),'closed_reason':nullable(enum('NO_CRY','USER_STOP','INPUT_GAP','FILE_IMPORT')),'observation_session_id':nullable(UUID),'data_origin':ORIGIN,**VERSION_FIELDS})
mutation('CreateEpisode', {'baby_id':UUID,'source':enum('AUTO','MANUAL','FILE'),'timing_status':enum('KNOWN','UNKNOWN'),'started_at':nullable(TIME),'observation_session_id':nullable(UUID),'data_origin':ORIGIN}, description='AUTO requires an active observation session. The server verifies origin against the environment; unknown file timing never implies current context.')
mutation('CloseEpisode', {'version':INT,'ended_at':nullable(TIME),'closed_reason':enum('NO_CRY','USER_STOP','INPUT_GAP','FILE_IMPORT')})

schema('AudioAsset', {'audio_id':UUID,'episode_id':UUID,'baby_id':UUID,'created_by_user_id':UUID,'mime_type':STR,'bytes':NONNEG,'duration_seconds':nullable(NUM),'checksum_sha256':nullable({'type':'string','pattern':'^[0-9a-f]{64}$'}),'status':enum('ALLOCATED','VERIFYING','READY','REJECTED','DELETING','DELETED'),'retention_until':nullable(TIME),'rejection_code':nullable(STR),'data_origin':ORIGIN,**VERSION_FIELDS})
SCHEMAS['AudioAsset']['properties']['quality_reasons']=arr(QUALITY_REASONS)
SCHEMAS['AudioAsset']['required'].append('quality_reasons')
schema('UploadGrant', {'upload_id':UUID,'audio_id':UUID,'bucket':STR,'object_key':STR,'method':enum('STANDARD','TUS'),'upload_endpoint':URI,'expires_at':TIME,'max_bytes':{'type':'integer','maximum':25000000,'minimum':1}})
schema('AudioUpload', {'audio':ref('AudioAsset'),'upload':ref('UploadGrant')})
mutation('CreateUpload', {'mime_type':TEXT,'bytes':{'type':'integer','minimum':1,'maximum':25000000},'duration_seconds':nullable({'type':'number','minimum':0,'maximum':60}),'checksum_sha256':nullable({'type':'string','pattern':'^[0-9a-f]{64}$'}),'prefer_resumable':BOOL})
mutation('ReissueUpload', {'version':INT})
mutation('CompleteUpload', {'checksum_sha256':nullable({'type':'string','pattern':'^[0-9a-f]{64}$'})})
mutation('CancelUpload', {})
schema('Playback', {'audio_id':UUID,'playback_url':URI,'expires_at':TIME})

schema('Failure', {'code':STR,'message':STR,'retryable':BOOL})
schema('AudioCandidate', {'code':STR,'label':STR,'rank':{'type':'integer','minimum':1,'maximum':3}})
schema('ContextValues', {'last_feeding_at':nullable(TIME),'last_sleep_started_at':nullable(TIME),'last_sleep_ended_at':nullable(TIME),'last_diaper_event_at':nullable(TIME),'minutes_since_last_feeding':nullable(NUM),'current_sleep':nullable(BOOL)})
schema('ContextSnapshot', {'context_snapshot_id':UUID,'baby_id':UUID,'as_of':nullable(TIME),'known_at':TIME,'record_refs':arr(ref('RecordVersion')),'values':ref('ContextValues'),'missing_fields':arr(STR),'reproduction_status':enum('AVAILABLE','SOURCE_DELETED')})
schema('RecommendedAction', {'action_type':ACTION,'text':STR,'evidence_refs':arr(ref('SourceRef'))})
schema('ObservationQuestion', {'kind':STR,'text':STR,'options':arr(enum('YES','NO','UNKNOWN'),minItems=1),'policy_version':STR})
schema('HelpAction', {'text':STR,'action_label':STR,'action_url':URI,'region':STR,'reviewed_template_id':STR,'evidence_refs':arr(ref('SourceRef'),minItems=1)})
schema('Recommendation', {'recommendation_id':UUID,'analysis_id':UUID,'context_snapshot_id':nullable(UUID),'status':enum('READY','GENERAL_CHECKLIST','NEEDS_OBSERVATION','HELP_REQUIRED'),'actions':arr(ref('RecommendedAction'),maxItems=3),'optional_questions':arr(ref('ObservationQuestion'),maxItems=2),'help_action':nullable(ref('HelpAction')),'policy_version':STR,'supersedes_id':nullable(UUID),'recorded_at':TIME})
schema('Analysis', {'analysis_id':UUID,'baby_id':UUID,'episode_id':UUID,'audio_id':UUID,'created_by_user_id':UUID,
    'status':enum('READY','RUNNING','COMPLETE','ABSTAIN','FAILED'),
    'stage':enum('READY','QUALITY_CHECK','INFERENCE','CONTEXT','PERSISTING','FINISHED'),
    'attempt_no':INT,'lease_expires_at':nullable(TIME),'quality_status':enum('PENDING','PASS','INSUFFICIENT','INVALID'),'quality_reasons':arr(QUALITY_REASONS),
    'cry_detected':nullable(BOOL),'audio_candidates':arr(ref('AudioCandidate'),maxItems=3),
    'abstain_reason':nullable(enum('NO_CRY','LOW_QUALITY','INSUFFICIENT_AUDIO','LOW_CONFIDENCE','UNSUPPORTED_SCOPE')),
    'failure':nullable(ref('Failure')),'model_version':nullable(STR),'preprocess_version':nullable(STR),'label_mapping_version':nullable(STR),
    'context_snapshot':nullable(ref('ContextSnapshot')),'recommendation':nullable(ref('Recommendation')),
    'inference_mode':enum('REAL','STUB'),'inference_executed':BOOL,'data_origin':ORIGIN,'recorded_at':TIME,'completed_at':nullable(TIME)},
    description='A stored FAILED or ABSTAIN resource is returned with HTTP 200 on successful retrieval. inference_executed means an actual audio model call, never a stub.')
mutation('CreateAnalysis', {'analysis_id':UUID,'audio_id':UUID})
mutation('RetryAnalysis', {'expected_attempt':INT})
mutation('RecomputeRecommendation', {'expected_context_revision':NONNEG})

schema('FeedingPayload', {'mode':FEEDING,'amount_ml':nullable(NUM),'duration_minutes':nullable(NUM)})
schema('SleepPayload', {})
schema('DiaperPayload', {'operation':enum('CHECK','CHANGE'),'condition':enum('WET','STOOL','BOTH','CLEAN','UNKNOWN')})
schema('SoothePayload', {'action_kind':enum('HOLDING','BURPING','SLEEP_PREPARATION','ENVIRONMENT_ADJUSTMENT','OTHER')})
for kind, pay in [('FEEDING','FeedingPayload'),('SLEEP','SleepPayload'),('DIAPER','DiaperPayload'),('SOOTHE','SoothePayload')]:
    schema(kind.title()+'EventValue', {'type':{'const':kind,'type':'string'},'occurred_at':nullable(TIME),'ended_at':nullable(TIME),'time_precision':PRECISION,'payload':ref(pay)})
SCHEMAS['SleepEventValue']['properties']['occurred_at']=TIME
SCHEMAS['SleepEventValue']['properties']['time_precision']={'const':'EXACT','type':'string'}
SCHEMAS['CareEventValue'] = {'oneOf':[ref(x+'EventValue') for x in ['Feeding','Sleep','Diaper','Soothe']], 'discriminator':{'propertyName':'type','mapping':{k:f'#/components/schemas/{v}EventValue' for k,v in [('FEEDING','Feeding'),('SLEEP','Sleep'),('DIAPER','Diaper'),('SOOTHE','Soothe')]}}}
schema('CareEvent', {'care_event_id':UUID,'baby_id':UUID,'created_by_user_id':UUID,'updated_by_user_id':UUID,'source_entry_id':nullable(UUID),'status':LIFECYCLE,'event':ref('CareEventValue'),'data_origin':ORIGIN,**VERSION_FIELDS})
mutation('CreateCareEvent', {'event':ref('CareEventValue')})
mutation('PatchCareEvent', {'version':INT,'event':ref('CareEventValue')}, description='Replace the confirmed structured event. Input with raw text must use a revision draft and confirm instead.')
schema('ActionAttempt', {'action_id':UUID,'baby_id':UUID,'episode_id':UUID,'care_event_id':UUID,'recommendation_id':nullable(UUID),'created_by_user_id':UUID,'performed_by_user_id':nullable(UUID),'performed_at':nullable(TIME),'sequence':INT,'status':LIFECYCLE,'followup_status':enum('PENDING','RECORDED','UNCONFIRMED'),'data_origin':ORIGIN,**VERSION_FIELDS})
schema('ActionGroup', {'action_group_id':UUID,'baby_id':UUID,'episode_id':UUID,'entry_id':UUID,'action_ids':arr(UUID,minItems=2,uniqueItems=True),'version':INT})
mutation('CreateAction', {'care_event_id':nullable(UUID),'new_care_event':nullable(ref('CareEventValue')),'recommendation_id':nullable(UUID),'performed_by_user_id':nullable(UUID),'sequence':INT}, description='Exactly one of care_event_id and new_care_event is non-null. Both belong to the path episode baby. Only actual PERFORMED actions.')
mutation('CloseActionFollowup', {'version':INT,'followup_status':{'const':'UNCONFIRMED','type':'string'}})
schema('Outcome', {'outcome_id':UUID,'baby_id':UUID,'action_id':nullable(UUID),'action_group_id':nullable(UUID),'observed_at':nullable(TIME),'time_precision':PRECISION,'response':RESPONSE,'caregiver_interpretation':nullable(STR),'created_by_user_id':UUID,'updated_by_user_id':UUID,'data_origin':ORIGIN,**VERSION_FIELDS})
mutation('CreateOutcome', {'observed_at':nullable(TIME),'time_precision':PRECISION,'response':RESPONSE,'caregiver_interpretation':nullable(STR)})
mutation('PatchOutcome', {'version':INT,'observed_at':nullable(TIME),'time_precision':PRECISION,'response':RESPONSE,'caregiver_interpretation':nullable(STR)})
schema('StateObservation', {'state_observation_id':UUID,'baby_id':UUID,'episode_id':nullable(UUID),'action_id':nullable(UUID),'source_entry_id':nullable(UUID),'phase':PHASE,'observed_at':nullable(TIME),'time_precision':PRECISION,'state_codes':arr(STATE,minItems=1,uniqueItems=True),'observation_source':enum('SELF_REPORTED','REPORTED_BY_OTHER'),'confirmation_status':enum('USER_CONFIRMED','USER_CORRECTED'),'visual_state_code':enum('CRYING','FUSSING','CALM','SLEEPY_APPEARING','ASLEEP','AWAKE','CHEERFUL_APPEARING','NEUTRAL'),'visual_mapping_version':STR,'created_by_user_id':UUID,'data_origin':ORIGIN,**VERSION_FIELDS})
mutation('CreateStateObservation', {'episode_id':nullable(UUID),'action_id':nullable(UUID),'phase':PHASE,'observed_at':nullable(TIME),'time_precision':PRECISION,'state_codes':arr(STATE,minItems=1,uniqueItems=True),'observation_source':enum('SELF_REPORTED','REPORTED_BY_OTHER')})
schema('CaregiverObservation', {'observation_id':UUID,'baby_id':UUID,'episode_id':UUID,'kind':STR,'value':enum('YES','NO','UNKNOWN'),'observed_at':TIME,'created_by_user_id':UUID,**VERSION_FIELDS})
mutation('CreateObservation', {'kind':TEXT,'value':enum('YES','NO','UNKNOWN'),'observed_at':TIME})

schema('Evidence', {'source':enum('CHOICE','TEXT','USER_CORRECTION'),'choice_id':nullable(STR),'span_start':nullable(NONNEG),'span_end':nullable(NONNEG),'quote':nullable(STR)}, description='Text offsets count Unicode code points: start inclusive, end exclusive. A matching source reference is mandatory for extracted assertions.')
schema('DraftAction', {'action_ref':STR,'action_code':ACTION,'assertion':enum('PERFORMED','PLANNED','NEGATED','UNCERTAIN'),'performed_by_user_id':nullable(UUID),'occurred_at':nullable(TIME),'relative_time':nullable(STR),'time_precision':PRECISION,'sequence':INT,'amount':nullable(NUM),'unit':nullable(enum('ML','MINUTES')),'feeding_mode':nullable(FEEDING),'evidence':arr(ref('Evidence'),minItems=1)})
schema('DraftState', {'state_codes':arr(STATE,minItems=1),'phase':PHASE,'observed_at':nullable(TIME),'time_precision':PRECISION,'linked_action_refs':arr(STR),'evidence':arr(ref('Evidence'),minItems=1)})
schema('DraftOutcome', {'response_code':RESPONSE,'observed_at':nullable(TIME),'time_precision':PRECISION,'linked_action_refs':arr(STR),'attribution':enum('SINGLE','MULTI','UNKNOWN'),'evidence':arr(ref('Evidence'),minItems=1)})
schema('Interpretation', {'text':STR,'certainty':{'const':'CAREGIVER_REPORTED','type':'string'},'evidence':arr(ref('Evidence'),minItems=1)})
schema('Unresolved', {'field':STR,'code':enum('CONFLICT','UNKNOWN_VALUE','UNKNOWN_TIME','UNSUPPORTED_CODE','MISSING_EVIDENCE'),'message':STR})
schema('NormalizedContent', {'actions':arr(ref('DraftAction')),'states':arr(ref('DraftState')),'outcomes':arr(ref('DraftOutcome')),'caregiver_interpretations':arr(ref('Interpretation')),'unresolved':arr(ref('Unresolved'))})
schema('Choice', {'choice_id':STR,'kind':enum('ACTION','STATE','RESPONSE'),'code':STR,'assertion':nullable(enum('PERFORMED','PLANNED','NEGATED','UNCERTAIN'))})
schema('ConfirmedResources', {'entry_id':UUID,'input_revision':INT,'care_event_ids':arr(UUID),'action_ids':arr(UUID),'action_group_ids':arr(UUID),'state_observation_ids':arr(UUID),'outcome_ids':arr(UUID),'label_annotation_ids':arr(UUID)})
schema('CareEntry', {'entry_id':UUID,'baby_id':UUID,'author_user_id':UUID,'original_author_user_id':UUID,'episode_id':nullable(UUID),'input_mode':enum('CHOICE','TEXT','MIXED'),'raw_text':nullable({'type':'string','maxLength':2000}),'choices':arr(ref('Choice')),'occurred_at':nullable(TIME),'time_precision':PRECISION,'input_revision':INT,'status':enum('DRAFT','NORMALIZING','REVIEW_READY','CONFIRMED','NEEDS_MANUAL_REVIEW','DELETING','DELETED'),'normalization_run_id':nullable(UUID),'normalized_content':nullable(ref('NormalizedContent')),'supersedes_entry_id':nullable(UUID),'base_record_versions':arr(ref('RecordVersion')),'confirmed_resources':nullable(ref('ConfirmedResources')),'confirmed_by_user_id':nullable(UUID),'confirmed_at':nullable(TIME),'data_origin':ORIGIN,**VERSION_FIELDS})
page('CareEntry')
ENTRY_INPUT = {'input_mode':enum('CHOICE','TEXT','MIXED'),'raw_text':nullable({'type':'string','maxLength':2000}),'choices':arr(ref('Choice')),'occurred_at':nullable(TIME),'time_precision':PRECISION}
mutation('CreateCareEntry', {'episode_id':nullable(UUID),**ENTRY_INPUT,'supersedes_entry_id':nullable(UUID),'base_record_versions':arr(ref('RecordVersion'))})
mutation('PatchCareEntry', {'input_revision':INT,**ENTRY_INPUT})
schema('NormalizationRun', {'run_id':UUID,'entry_id':UUID,'input_revision':INT,'status':enum('RUNNING','COMPLETE','FAILED','STALE'),'lease_expires_at':nullable(TIME),'execution_mode':enum('REAL','STUB'),'provider':nullable(STR),'model':nullable(STR),'prompt_version':STR,'schema_version':STR,'ontology_version':STR,'result':nullable(ref('NormalizedContent')),'failure':nullable(ref('Failure')),'recorded_at':TIME,'completed_at':nullable(TIME)})
mutation('CreateNormalization', {'run_id':UUID,'input_revision':INT})
mutation('ConfirmCareEntry', {'input_revision':INT,'run_id':nullable(UUID),'normalization_mode':enum('LLM','RULE','MANUAL'),'content':ref('NormalizedContent'),'base_record_versions':arr(ref('RecordVersion'))}, description='LLM requires current successful run_id. RULE and MANUAL require null run_id. Validation and final writes are atomic. Confirmed means caregiver-reported, not a verified cause.')

schema('EpisodeDetail', {'episode':ref('Episode'),'audio_assets':arr(ref('AudioAsset')),'analyses':arr(ref('Analysis')),'actions':arr(ref('ActionAttempt')),'action_groups':arr(ref('ActionGroup')),'outcomes':arr(ref('Outcome')),'state_observations':arr(ref('StateObservation'))})
schema('TimelineItem', {'kind':enum('CARE_EVENT','EPISODE','STATE_OBSERVATION'),'resource_id':UUID,'baby_id':UUID,'occurred_at':nullable(TIME),'version':INT,'created_by_user_id':UUID,'data_origin':ORIGIN,'resource':{'oneOf':[ref('CareEvent'),ref('Episode'),ref('StateObservation')]}})
page('TimelineItem')
schema('Change', {
    'resource_type':{**enum('BABY','MEMBERSHIP','CARE_EVENT','EPISODE','ANALYSIS','RECOMMENDATION','STATE_OBSERVATION','OUTCOME','DELETION'),'description':'Identifies the existing authorized FastAPI resource to refetch. DELETION never exposes a requester-private DeletionJob.'},
    'resource_id':{**UUID,'description':'Opaque identifier only. The change feed never contains resource bodies, caregiver text, audio URLs, or tokens.'},
    'version':{**INT,'description':'Actual version of the changed resource after the mutation, including the tombstone version when deleted=true.'},
    'deleted':{**BOOL,'description':'When true, remove this resource from the scoped cache. A later response with a lower version must not restore it.'},
})
schema('Changes', {
    'baby_id':UUID,
    'current_revision':{**NONNEG,'description':'A complete high-water mark for this response. When resync_required=false, every committed shared change in (since_revision, current_revision] is represented after same-resource coalescing.'},
    'changes':arr(ref('Change'),maxItems=500,description='At most one latest item per resource. One mutation may invalidate multiple resources, such as a CareEvent and the Baby whose public context_revision advanced. Ordering is not a client conflict-resolution rule; use resource version.'),
    'resync_required':{**BOOL,'description':'If true, changes is empty and the client must perform a full authorized refetch. This covers initial revision 0, pruned history, a future revision, and more than 500 distinct changed resources.'},
    'server_time':TIME,
}, description='Authorization-scoped invalidation feed. Save current_revision only after every required refetch or the documented full-resync flow succeeds.')

schema('SimilarCase', {'episode_id':UUID,'analysis_id':UUID,'occurred_at':nullable(TIME),'matching_fields':arr(STR,minItems=2),'performed_actions':arr(ACTION),'observed_responses':arr(RESPONSE),'evidence_refs':arr(ref('SourceRef'))})
schema('SimilarCases', {'baby_id':UUID,'analysis_id':UUID,'items':arr(ref('SimilarCase'),maxItems=5),'eligible_case_count':NONNEG,'reason':nullable(enum('NO_CASES','INSUFFICIENT_COMPARABLE_CONTEXT','SOURCE_DELETED')),'policy_version':STR})
schema('RecordCoverage', {'baby_id':UUID,'date':DATE,'type':enum('FEEDING','SLEEP','DIAPER'),'confirmed':BOOL,'updated_by_user_id':UUID,'version':INT})
mutation('SetRecordCoverage', {'date':DATE,'type':enum('FEEDING','SLEEP','DIAPER'),'confirmed':BOOL,'version':NONNEG})
schema('FeedingSummary', {'record_count':NONNEG,'known_amount_count':NONNEG,'unknown_amount_count':NONNEG,'total_recorded_ml':nullable(NUM),'breastfeeding_minutes':nullable(NUM)})
schema('SleepSummary', {'record_count':NONNEG,'recorded_minutes_in_day':NUM,'active_sleep_id':nullable(UUID),'has_unknown_duration':BOOL})
schema('DiaperSummary', {'check_count':NONNEG,'change_count':NONNEG})
schema('DailySummary', {'baby_id':UUID,'date':DATE,'timezone':STR,'as_of':TIME,'context_revision':NONNEG,'has_records':BOOL,'feeding':ref('FeedingSummary'),'sleep':ref('SleepSummary'),'diaper':ref('DiaperSummary'),'record_coverage':arr(ref('RecordCoverage')),'latest_confirmed_state':nullable(ref('StateObservation')),'missing_fields':arr(STR)})
REMINDER_KIND=enum('FEEDING','SLEEP_PREPARATION','DIAPER')
schema('Pattern', {'kind':REMINDER_KIND,'status':enum('READY','ON_HOLD'),'reason':nullable(enum('INSUFFICIENT_RECORDS','HIGH_VARIABILITY','UNCONFIRMED_DAYS','MANUAL_ONLY')),'valid_days':NONNEG,'interval_count':NONNEG,'median_minutes':nullable(NUM),'p25_minutes':nullable(NUM),'p75_minutes':nullable(NUM),'anchor_event_id':nullable(UUID),'estimated_due_at':nullable(TIME),'policy_version':STR})
schema('Patterns', {'baby_id':UUID,'range_days':{'const':7,'type':'integer'},'as_of':TIME,'items':arr(ref('Pattern'))})
schema('Reminder', {'reminder_id':UUID,'baby_id':UUID,'recipient_user_id':UUID,'kind':REMINDER_KIND,'anchor_event_id':nullable(UUID),'state':enum('SCHEDULED','DUE','SNOOZED','DISMISSED','EXPIRED'),'due_at':TIME,'snoozed_until':nullable(TIME),'seen_at':nullable(TIME),'evidence_refs':arr(ref('SourceRef')),'policy_version':STR,**VERSION_FIELDS})
page('Reminder')
mutation('PatchReminder', {'version':INT,'action':enum('SNOOZE_10_MIN','DISMISS_OCCURRENCE','MARK_SEEN')})
schema('ReminderSetting', {'baby_id':UUID,'user_id':UUID,'kind':REMINDER_KIND,'enabled':BOOL,'lead_minutes':{'const':10,'type':'integer'},'manual_interval_minutes':nullable({'type':'integer','minimum':1}),'version':INT})
schema('ReminderSettings', {'items':arr(ref('ReminderSetting'))})
mutation('SetReminderSetting', {'kind':REMINDER_KIND,'enabled':BOOL,'manual_interval_minutes':nullable({'type':'integer','minimum':1}),'version':NONNEG})

schema('ObservationSession', {'session_id':UUID,'baby_id':UUID,'user_id':UUID,'source_id':UUID,'status':enum('ACTIVE','STOPPED','EXPIRED'),'last_seq':NONNEG,'last_valid_input_end':nullable(TIME),'lease_expires_at':TIME,'stop_reason':nullable(enum('USER_STOP','BABY_SWITCH','HIDDEN','MIC_LOST','OFFLINE','LOGOUT','TAKEOVER','EXPIRED'))})
mutation('CreateSession', {'baby_id':UUID,'source_id':UUID})
mutation('Heartbeat', {'seq':INT,'input_start':TIME,'input_end':TIME,'health':enum('OK','INTERRUPTED')})
mutation('StopSession', {'input_end':nullable(TIME),'reason':enum('USER_STOP','BABY_SWITCH','HIDDEN','MIC_LOST','OFFLINE','LOGOUT','TAKEOVER')})
schema('DeletionJob', {'deletion_job_id':UUID,'requester_user_id':UUID,'baby_id':UUID,'scope':enum('ALL','CARE_EVENT','CARE_ENTRY','MY_CONTRIBUTIONS'),'resource_id':nullable(UUID),'status':enum('PENDING','RUNNING','COMPLETE','FAILED'),'access_blocked':{'const':True,'type':'boolean'},'requested_at':TIME,'completed_at':nullable(TIME),'failure':nullable(ref('Failure')),'pending_categories':arr(enum('AUDIO','RAW_TEXT','RECORDS','ANALYSES','DERIVED_FEATURES','TRAINING_COPIES')),'attempt_no':INT})
mutation('RetryDeletion', {'expected_attempt':INT})

schema('FieldError', {'field':STR,'code':STR,'message':STR})
ERROR_CODES = {
 401:['AUTH_REQUIRED','TOKEN_EXPIRED','INVALID_TOKEN','SESSION_REVOKED','REAUTH_REQUIRED','REAUTH_PROOF_INVALID'],
 403:['OWNER_ONLY','AUTHOR_ONLY','INVITE_EMAIL_MISMATCH','CONSENT_REQUIRED','CHILD_DATA_VERIFICATION_REQUIRED'],
 404:['RESOURCE_NOT_FOUND'],
 409:['VERSION_CONFLICT','SOURCE_REVISION_CHANGED','ANALYSIS_IN_PROGRESS','NORMALIZATION_IN_PROGRESS','OPERATION_IN_PROGRESS','IDEMPOTENCY_KEY_REUSED','OWNER_REQUIRED','ACTIVE_SESSION_EXISTS','SLEEP_ALREADY_ACTIVE','RESOURCE_DELETING','ALREADY_MEMBER','INVITE_ALREADY_USED','OWNER_BABY_LIMIT','ALREADY_CONFIRMED','INVALID_STATE'],
 410:['INVITE_EXPIRED','INVITE_REVOKED','RESOURCE_DELETED'],
 413:['FILE_TOO_LARGE'],415:['UNSUPPORTED_MEDIA_TYPE'],422:['VALIDATION_ERROR','INVALID_AUDIO'],429:['RATE_LIMITED'],500:['INTERNAL_ERROR'],503:['MODEL_NOT_READY','SERVICE_UNAVAILABLE','AUTH_PROVIDER_REVOCATION_FAILED']}
SCHEMAS['ErrorCode']=enum(*[c for codes in ERROR_CODES.values() for c in codes])
SCHEMAS['Failure']['properties']['code']=enum('ANALYSIS_TIMEOUT','ANALYSIS_LEASE_EXPIRED','INFERENCE_ERROR','NORMALIZATION_TIMEOUT','NORMALIZATION_LEASE_EXPIRED','NORMALIZATION_SCHEMA_INVALID','NORMALIZATION_PROVIDER_ERROR','CLEANUP_FAILED','SOURCE_DELETED','ACCESS_REVOKED')
schema('ErrorDetails', {'current_version':nullable(INT),'current_resource':nullable({'oneOf':[ref(n) for n in ['Baby','Membership','CareEvent','Outcome','CareEntry','ActionAttempt','Consent','Invite','Episode','StateObservation','AudioAsset','Reminder','ReminderSetting','RecordCoverage']]}),'resource_type':nullable(STR),'existing_analysis_id':nullable(UUID),'existing_run_id':nullable(UUID),'existing_session_id':nullable(UUID),'deletion_job_id':nullable(UUID),'session_revocation_id':nullable(UUID),'reauthentication_challenge_id':nullable(UUID),'status_url':nullable(URI),'retry_after_seconds':nullable(NUM)})
schema('ApiError', {'code':ref('ErrorCode'),'message':STR,'retryable':BOOL,'request_id':UUID,'field_errors':arr(ref('FieldError')),'details':ref('ErrorDetails')})

def when_status(name,status,properties):
    SCHEMAS[name].setdefault('allOf',[]).append({'if':{'properties':{'status':{'const':status}},'required':['status']},'then':{'properties':properties}})

for status in ['READY','RUNNING']:
    when_status('Analysis',status,{'audio_candidates':{'maxItems':0},'abstain_reason':{'type':'null'},'failure':{'type':'null'},'completed_at':{'type':'null'}})
for status in ['COMPLETE','ABSTAIN','FAILED']:
    when_status('Analysis',status,{'stage':{'const':'FINISHED'},'lease_expires_at':{'type':'null'},'completed_at':TIME})
when_status('Analysis','COMPLETE',{'audio_candidates':{'minItems':1},'quality_status':{'const':'PASS'},'cry_detected':{'const':True},'abstain_reason':{'type':'null'},'failure':{'type':'null'},'recommendation':ref('Recommendation')})
when_status('Analysis','ABSTAIN',{'audio_candidates':{'maxItems':0},'abstain_reason':{'type':'string'},'failure':{'type':'null'}})
when_status('Analysis','FAILED',{'audio_candidates':{'maxItems':0},'abstain_reason':{'type':'null'},'failure':ref('Failure')})
SCHEMAS['Analysis']['allOf'].append({'if':{'properties':{'inference_mode':{'const':'STUB'}}},'then':{'properties':{'inference_executed':{'const':False}}}})
when_status('NormalizationRun','COMPLETE',{'result':ref('NormalizedContent'),'failure':{'type':'null'},'completed_at':TIME,'lease_expires_at':{'type':'null'}})
when_status('NormalizationRun','FAILED',{'result':{'type':'null'},'failure':ref('Failure'),'completed_at':TIME,'lease_expires_at':{'type':'null'}})
when_status('NormalizationRun','RUNNING',{'result':{'type':'null'},'failure':{'type':'null'},'completed_at':{'type':'null'},'lease_expires_at':TIME})
when_status('DeletionJob','COMPLETE',{'failure':{'type':'null'},'pending_categories':{'maxItems':0},'completed_at':TIME})
when_status('DeletionJob','FAILED',{'failure':ref('Failure'),'completed_at':{'type':'null'}})
when_status('Recommendation','HELP_REQUIRED',{'actions':{'maxItems':0},'help_action':ref('HelpAction')})
when_status('Pattern','READY',{'reason':{'type':'null'},'valid_days':{'minimum':3},'interval_count':{'minimum':10},'median_minutes':NUM,'p25_minutes':NUM,'p75_minutes':NUM})
when_status('Pattern','ON_HOLD',{'reason':{'type':'string'},'estimated_due_at':{'type':'null'}})
SCHEMAS['CreateAction']['oneOf']=[{'properties':{'care_event_id':UUID,'new_care_event':{'type':'null'}}},{'properties':{'care_event_id':{'type':'null'},'new_care_event':ref('CareEventValue')}}]
SCHEMAS['Outcome']['oneOf']=[{'properties':{'action_id':UUID,'action_group_id':{'type':'null'}}},{'properties':{'action_id':{'type':'null'},'action_group_id':UUID}}]
SCHEMAS['ConfirmCareEntry']['allOf']=[{'if':{'properties':{'normalization_mode':{'const':'LLM'}}},'then':{'properties':{'run_id':UUID}},'else':{'properties':{'run_id':{'type':'null'}}}},{'properties':{'content':{'properties':{'unresolved':{'items':{'properties':{'code':enum('UNKNOWN_VALUE','UNKNOWN_TIME')}}}}}}}]

PARAMS = {
    'IdempotencyKey': {'name':'Idempotency-Key','in':'header','required':True,'schema':UUID,'description':'UUID per logical mutation. Must equal client_request_id in JSON bodies. Reuse after an uncertain response.'},
    'Version': {'name':'version','in':'query','required':True,'schema':INT,'description':'Current resource version for destructive changes.'},
    'ReauthenticationProof': {'name':'X-Reauthentication-Proof','in':'header','required':True,'schema':STR,'description':'Single-use 5-minute proof bound to this user, OTP-authenticated session, operation, and baby.'},
}
ERRORS = {
    401:'Authentication required or expired token.',403:'Authenticated but insufficient role or consent.',
    404:'Not found or not accessible, including another author private draft.',
    409:'Version, state, duplicate-key payload, or active-operation conflict. Inspect code.',
    410:'Expired/revoked invite or a deleted resource visible to its requester.',
    413:'File exceeds the declared limit.',415:'Unsupported or unverified media type.',
    422:'Request validation failed; always uses ApiError.',429:'Rate limit; observe Retry-After.',
    500:'Unexpected failure; verify saved state before retry.',503:'Model not ready or service temporarily unavailable.'}

def query(name, value, required=False): return {'name':name,'in':'query','required':required,'schema':value}
def op(method,path,name,response,status=200,request=None,description='',tag='Core',params=(),errors=None):
    parameters = []
    for segment in path.split('/'):
        if segment.startswith('{'):
            parameters.append({'name':segment[1:-1],'in':'path','required':True,'schema':UUID})
    if method != 'get': parameters.append({'$ref':'#/components/parameters/IdempotencyKey'})
    parameters.extend(params)
    replies = {str(status):{'description':'Success. '+description,'headers':{'X-Request-ID':{'schema':UUID}},'content':{'application/json':{'schema':ref(response)}}}}
    for code in (errors if errors is not None else [401,403,404,409,422,429,500,503]):
        replies[str(code)] = {'$ref':f'#/components/responses/Error{code}'}
    operation={'operationId':name,'summary':name,'description':description,'tags':[tag],'parameters':parameters,'responses':replies}
    if request: operation['requestBody']=body(request)
    PATHS.setdefault(path,{})[method]=operation
    REGISTRY[name]={'method':method.upper(),'path':path,'request':request,'response':response,'status':status}

op('get','/capabilities','getCapabilities','Capabilities',tag='Configuration')
op('post','/auth/reauthentication/challenges','createReauthenticationChallenge','ReauthenticationChallenge',201,'CreateReauthenticationChallenge',tag='Authentication',description='Creates a 10-minute challenge; the client then performs a fresh Supabase email OTP sign-in. Access-token refresh is insufficient.')
op('post','/auth/reauthentication/proofs','createReauthenticationProof','ReauthenticationProof',201,'CreateReauthenticationProof',tag='Authentication',description='Requires a JWT whose amr contains a fresh otp or magiclink authentication after challenge creation. The proof is returned once and lasts 5 minutes.')
op('post','/auth/session-revocations','revokeSessions','SessionRevocation',request='RevokeSessions',tag='Authentication',errors=[401,409,422,429,500,503],description='Durably blocks selected sessions locally, then invokes the matching Supabase local/others/global sign-out scope. Provider failure returns 503 with the durable revocation id.')
op('get','/auth/session-revocations/{revocation_id}','getSessionRevocation','SessionRevocation',tag='Authentication',errors=[401,404,500,503],description='Requester only. Use another live session or a new login when the current session was revoked.')
op('get','/babies','listBabies','BabyList',tag='Baby')
op('get','/babies/current','getActiveBaby','ActiveBaby',tag='Baby')
op('put','/me/active-baby','setActiveBaby','ActiveBaby',request='SetActiveBaby',tag='Baby',description='Stores the next-login preference. Other tabs and devices do not switch automatically.')
op('post','/babies','createBaby','BabyAccess',201,'CreateBaby',tag='Baby',description='Creates baby and OWNER membership atomically. One active owned baby per user.')
op('patch','/babies/{baby_id}','patchBaby','Baby',request='PatchBaby',tag='Baby',description='OWNER only.')
op('get','/babies/{baby_id}/members','listMembers','MembershipPage',tag='Membership')
op('patch','/babies/{baby_id}/members/me','patchMyRelationship','Membership',request='PatchMembership',tag='Membership')
op('delete','/babies/{baby_id}/members/{user_id}','removeMembership','Membership',tag='Membership',params=[{'$ref':'#/components/parameters/Version'}],description='OWNER removes a caregiver; caregiver may leave. OWNER cannot leave. New access stops immediately.')
op('get','/babies/{baby_id}/invites','listInvites','InvitePage',tag='Invite',description='OWNER only; tokens are never listed.')
op('post','/babies/{baby_id}/invites','createInvite','IssuedInvite',201,'CreateInvite',tag='Invite',params=[{'$ref':'#/components/parameters/ReauthenticationProof'}],description='OWNER only. Requires a CREATE_INVITE reauthentication proof. 24-hour single-use token; old pending invite for the same target is revoked.')
op('post','/invites/accept','acceptInvite','BabyAccess',request='AcceptInvite',tag='Invite',errors=[401,403,404,409,410,422,429,500,503],description='Verified email must match. Atomic membership creation and shared-use consent.')
op('post','/invites/{invite_id}/reissue','reissueInvite','IssuedInvite',201,'ReissueInvite',tag='Invite',params=[{'$ref':'#/components/parameters/ReauthenticationProof'}],description='OWNER only. Requires a CREATE_INVITE reauthentication proof, revokes the selected pending link, and returns a new invite id and one-time plaintext link. The old link remains unusable.')
op('delete','/invites/{invite_id}','revokeInvite','Invite',tag='Invite',params=[{'$ref':'#/components/parameters/Version'}])
op('get','/consents','listConsents','ConsentPage',tag='Consent',params=[query('baby_id',UUID,True)],description='Current own consent history and authorized baby settings. After leaving, only own consent history is returned.')
op('put','/consents','setBabyConsent','Consent',request='SetBabyConsent',tag='Consent',params=[{'name':'X-Reauthentication-Proof','in':'header','required':False,'schema':STR,'description':'Required only when granting BABY_TRAINING; must be bound to ENABLE_BABY_TRAINING and the target baby.'}])
op('get','/babies/{baby_id}/child-data-verification','getChildDataVerification','ChildDataVerification',tag='Consent',description='Current members may inspect the gate. UNVERIFIED and SYNTHETIC_TEST_ONLY never enable production child-data processing.')
op('put','/me/training-consents/{baby_id}','setMyTrainingConsent','Consent',request='SetTrainingConsent',tag='Consent')
op('post','/episodes','createEpisode','Episode',201,'CreateEpisode',tag='Episode')
op('get','/episodes/{episode_id}','getEpisode','EpisodeDetail',tag='Episode')
op('patch','/episodes/{episode_id}','closeEpisode','Episode',request='CloseEpisode',tag='Episode')
op('post','/episodes/{episode_id}/uploads','createUpload','AudioUpload',201,'CreateUpload',tag='Audio',errors=[401,403,404,409,413,415,422,429,500,503])
op('post','/audio-assets/{audio_id}/uploads','reissueUpload','AudioUpload',201,'ReissueUpload',tag='Audio',description='Renew an expired session for an unfinished audio. Same immutable object key; never overwrite an already verified asset.')
op('post','/uploads/{upload_id}/complete','completeUpload','AudioAsset',request='CompleteUpload',tag='Audio',errors=[401,403,404,409,413,415,422,429,500,503],description='Rechecks membership, actual object metadata, checksum and decoding. Invalid file returns 422 and stores REJECTED.')
op('post','/uploads/{upload_id}/cancel','cancelUpload','AudioAsset',request='CancelUpload',tag='Audio',description='Uploader only while unfinished. Revokes the upload session and starts cleanup. A later upload uses a new audio ID.')
op('get','/audio-assets/{audio_id}/playback','getPlayback','Playback',tag='Audio',description='Authorized current member, retained READY asset only. URL lifetime is at most 60 seconds.')
op('post','/episodes/{episode_id}/analyses','createAnalysis','Analysis',request='CreateAnalysis',tag='Analysis',description='Runs within this request for at most 45 seconds; saves before returning 200. A created failed analysis is a FAILED resource, while pre-admission failure is an ApiError.')
op('get','/analyses/{analysis_id}','getAnalysis','Analysis',tag='Analysis',description='Returns current state. An expired running lease becomes FAILED before returning. Stored FAILED and ABSTAIN both use HTTP 200.')
op('post','/analyses/{analysis_id}/retry','retryAnalysis','Analysis',request='RetryAnalysis',tag='Analysis',description='Only FAILED; same analysis_id, fresh idempotency key, matching expected_attempt. Fences late responses from prior attempts.')
op('post','/analyses/{analysis_id}/recommendations','recomputeRecommendation','Recommendation',201,'RecomputeRecommendation',tag='Analysis',description='Creates a new context snapshot and recommendation, reusing the existing audio result.')
op('post','/babies/{baby_id}/care-events','createCareEvent','CareEvent',201,'CreateCareEvent',tag='Care')
op('get','/care-events/{care_event_id}','getCareEvent','CareEvent',tag='Care')
op('patch','/care-events/{care_event_id}','patchCareEvent','CareEvent',request='PatchCareEvent',tag='Care',description='Original author or OWNER; verifies version and preserves original author.')
op('delete','/care-events/{care_event_id}','deleteCareEvent','DeletionJob',202,tag='Care',params=[{'$ref':'#/components/parameters/Version'}],description='Blocks the selected record and derived references, not all baby access.')
op('post','/episodes/{episode_id}/actions','createAction','ActionAttempt',201,'CreateAction',tag='Care',description='Existing event linkage does not create another daily care event. Duplicate episode/event link returns the same action.')
op('patch','/actions/{action_id}','closeActionFollowup','ActionAttempt',request='CloseActionFollowup',tag='Care')
op('post','/actions/{action_id}/outcomes','createOutcome','Outcome',201,'CreateOutcome',tag='Care')
op('post','/action-groups/{action_group_id}/outcomes','createGroupOutcome','Outcome',201,'CreateOutcome',tag='Care')
op('patch','/outcomes/{outcome_id}','patchOutcome','Outcome',request='PatchOutcome',tag='Care')
op('post','/babies/{baby_id}/state-observations','createStateObservation','StateObservation',201,'CreateStateObservation',tag='Care')
op('post','/episodes/{episode_id}/observations','createObservation','CaregiverObservation',201,'CreateObservation',tag='Care')
op('post','/babies/{baby_id}/care-entries','createCareEntry','CareEntry',201,'CreateCareEntry',tag='Normalization')
op('get','/babies/{baby_id}/care-entries','listMyCareEntries','CareEntryPage',tag='Normalization',params=[query('cursor',STR),query('limit',{'type':'integer','minimum':1,'maximum':100,'default':30}),query('status',enum('DRAFT','NORMALIZING','REVIEW_READY','NEEDS_MANUAL_REVIEW'))],description='Returns only the authenticated author own unconfirmed server drafts. Never place drafts on the shared timeline.')
op('patch','/care-entries/{entry_id}','patchCareEntry','CareEntry',request='PatchCareEntry',tag='Normalization',description='Draft author only. CONFIRMED cannot be directly patched; create a revision draft.')
op('get','/care-entries/{entry_id}','getCareEntry','CareEntry',tag='Normalization',description='Draft: author only, even OWNER cannot read another draft. Confirmed: active members.')
op('delete','/care-entries/{entry_id}','deleteCareEntry','DeletionJob',202,tag='Normalization',params=[{'$ref':'#/components/parameters/Version'}],description='Draft author only. Confirmed records use record/contribution deletion and lineage cleanup.')
op('post','/care-entries/{entry_id}/normalizations','createNormalization','NormalizationRun',request='CreateNormalization',tag='Normalization',description='Up to 20 seconds within request; 30-second lease. Client-generated run_id allows recovery.')
op('get','/normalizations/{run_id}','getNormalization','NormalizationRun',tag='Normalization',description='Draft author only. Stale input revision is never applied.')
op('post','/care-entries/{entry_id}/confirm','confirmCareEntry','ConfirmedResources',request='ConfirmCareEntry',tag='Normalization',description='Atomically validates current revision, run, base versions and caregiver confirmation; returns only actual generated or updated IDs.')
op('get','/babies/{baby_id}/timeline','getTimeline','TimelineItemPage',tag='Read',params=[query('from',TIME),query('to',TIME),query('cursor',STR),query('limit',{'type':'integer','minimum':1,'maximum':100,'default':30})])
op('get','/babies/{baby_id}/changes','getChanges','Changes',tag='Read',params=[query('since_revision',{**NONNEG,'description':'Last fully applied feed revision for this user and baby scope. Use 0 only to bootstrap a full resync.'},True)],description='Primary foreground recovery path. Returns coalesced identifiers for all committed shared changes after since_revision through one atomic current_revision boundary. Empty changes means no change only when resync_required=false. Initial revision 0, retained-history gaps, future revisions, or more than 500 distinct resources return an empty list with resync_required=true. Poll every 5 seconds only while visible and immediately on return; this interval is not a 5-second screen guarantee. Realtime remains disabled until SEC30 and SEC31 are verified.')
op('get','/babies/{baby_id}/similar-cases','getSimilarCases','SimilarCases',tag='Read',params=[query('analysis_id',UUID,True)],description='Same baby only, last 30 days, at least two comparable context fields, at most five eligible cases; no drafts or deleted sources.')
op('get','/babies/{baby_id}/summary','getDailySummary','DailySummary',tag='Read',params=[query('date',DATE,True),query('timezone',STR,True)],description='Timezone must equal current baby timezone. Split sleep at date boundaries for aggregates while keeping one source event. Unknown amounts stay unknown.')
op('get','/babies/{baby_id}/patterns','getPatterns','Patterns',tag='Read',params=[query('range',{'const':7,'type':'integer'}),query('kind',REMINDER_KIND)],description='Seven days; at least ten valid intervals on three confirmed days. Hold if IQR exceeds half the median. Diaper timing is manual.')
op('put','/babies/{baby_id}/record-coverage','setRecordCoverage','RecordCoverage',request='SetRecordCoverage',tag='Care')
op('get','/babies/{baby_id}/reminders','listReminders','ReminderPage',tag='Reminder',params=[query('active_only',BOOL)],description='Only reminders for the current recipient. Another caregiver has independent settings and dismissal state.')
op('patch','/reminders/{reminder_id}','patchReminder','Reminder',request='PatchReminder',tag='Reminder',description='Recipient only. A click or seen state does not create a performed action.')
op('get','/babies/{baby_id}/reminder-settings','getReminderSettings','ReminderSettings',tag='Reminder')
op('put','/babies/{baby_id}/reminder-settings','setReminderSetting','ReminderSetting',request='SetReminderSetting',tag='Reminder',description='Current user only. Defaults off. Diaper reminders require a user-selected interval; no automatic diaper pattern.')
op('post','/observation-sessions','createObservationSession','ObservationSession',201,'CreateSession',tag='Detection')
op('post','/observation-sessions/{session_id}/heartbeat','heartbeatObservationSession','ObservationSession',request='Heartbeat',tag='Detection',description='Every 10 seconds. Verify monotonic seq, actual input range, current membership and session owner. Lease expires after 30 seconds.')
op('post','/observation-sessions/{session_id}/stop','stopObservationSession','ObservationSession',request='StopSession',tag='Detection',description='Session owner may stop; another active member may explicitly stop for takeover. Start the new session after this response.')
op('delete','/babies/{baby_id}/data','deleteBabyData','DeletionJob',202,tag='Deletion',params=[{'$ref':'#/components/parameters/Version'},{'$ref':'#/components/parameters/ReauthenticationProof'},query('confirm',{'const':'DELETE_BABY','type':'string'},True)],description='OWNER only; requires a DELETE_BABY reauthentication proof, then sets Baby=DELETING and enqueues durable cleanup atomically. Ordinary reads and writes are blocked immediately.')
op('delete','/me/contributions/{baby_id}','deleteMyContributions','DeletionJob',202,tag='Deletion',params=[query('confirm',{'const':'DELETE_MY_CONTRIBUTIONS','type':'string'},True)],description='Authenticated contributor, including a former member. Resolves only the caller own contribution lineage; does not disclose other baby data.')
op('get','/deletions/{deletion_job_id}','getDeletion','DeletionJob',tag='Deletion',description='Requester only, including after membership removal. FAILED retains access_blocked=true for the deletion scope.')
op('post','/deletions/{deletion_job_id}/retry','retryDeletion','DeletionJob',202,'RetryDeletion',tag='Deletion',description='Requester only; same failed job and matching expected_attempt. Returns to RUNNING while access remains blocked.')

DOC = {'openapi':'3.1.0','info':{'title':'Baby care team integration contract','version':'1.1.1','description':'P0 frontend integration agreement dated 2026-09-20, covering A-01 to A-11. B-04 adds authentication recovery, reauthentication, invite reissue, child-data gate status, and private draft listing. B-09 clarifies the existing changes polling semantics without changing its response fields. Internal training curator/export APIs retain the v2 scope and are not browser endpoints.'},
       'servers':[{'url':'/v1','description':'Relative API prefix. The development deployment URL is not provisioned by this file.'}],
       'security':[{'UserBearer':[]}], 'paths':PATHS,
       'components':{'securitySchemes':{'UserBearer':{'type':'http','scheme':'bearer','bearerFormat':'Supabase user access token','description':'B validates signature, issuer, audience, expiry and current DB membership. A publishable key is not a user token.'}},
         'parameters':PARAMS,'schemas':SCHEMAS,
         'responses':{f'Error{k}':{'description':v,'headers':{'X-Request-ID':{'schema':UUID},**({'Retry-After':{'schema':{'type':'integer','minimum':1}}} if k in [429,503] else {})},'content':{'application/json':{'schema':ref('ApiError')}}} for k,v in ERRORS.items()}},
       'x-error-status':{c:status for status,codes in ERROR_CODES.items() for c in codes},
       'x-contract-rules':{'browser_data_api_allowlist':[],'direct_supabase':['Auth','registered-private-Storage-upload','issued-playback-URL','authorized-private-Realtime-subscription'],'idempotency_retention_days':7,'analysis_deadline_seconds':45,'analysis_lease_seconds':60,'analysis_client_timeout_seconds':65,'normalization_deadline_seconds':20,'normalization_lease_seconds':30,'normalization_client_timeout_seconds':35,'invite_ttl_seconds':86400,'reauthentication_challenge_ttl_seconds':600,'reauthentication_proof_ttl_seconds':300,'reauthentication_accepted_amr':['otp','magiclink'],'child_data_production_gate':'APPROVED_GUARDIAN_VERIFICATION_REQUIRED','playback_ttl_seconds':60,'upload_session_ttl_seconds':900,'foreground_refresh_seconds':5,'change_feed_max_items':500,'change_feed_history_retention_days':90,'change_feed_initial_revision':0,'change_feed_initial_requires_resync':True,'realtime_enabled':False,'observation_heartbeat_seconds':10,'observation_lease_seconds':30}}

def uid(n): return f'10000000-0000-4000-8000-{n:012d}'
NOW='2026-09-19T09:00:00Z'
VER={'version':1,'recorded_at':NOW,'updated_at':NOW}
USERS={'owner_a':uid(1),'caregiver_a':uid(2),'owner_b':uid(3),'invited_a':uid(4),'removed_a':uid(5)}
BABY_A=uid(101); BABY_B=uid(102)
baby={'baby_id':BABY_A,'owner_user_id':USERS['owner_a'],'alias':'예시 아기 A','birth_date':'2026-07-01','feeding_mode':'MIXED','timezone':'Asia/Seoul','status':'ACTIVE','context_revision':7,**VER}
member={'membership_id':uid(201),'baby_id':BABY_A,'user_id':USERS['owner_a'],'role':'OWNER','relationship':'OTHER','display_name':'시험 소유자 A','status':'ACTIVE',**VER}
episode={'episode_id':uid(301),'baby_id':BABY_A,'created_by_user_id':USERS['owner_a'],'status':'CLOSED','source':'FILE','timing_status':'UNKNOWN','started_at':None,'ended_at':None,'closed_reason':'FILE_IMPORT','observation_session_id':None,'data_origin':'DEMO',**VER}
audio={'audio_id':uid(401),'episode_id':uid(301),'baby_id':BABY_A,'created_by_user_id':USERS['owner_a'],'mime_type':'audio/wav','bytes':480044,'duration_seconds':15,'checksum_sha256':None,'status':'READY','retention_until':'2026-09-19T10:00:00Z','rejection_code':None,'quality_reasons':[],'data_origin':'DEMO',**VER}
analysis={'analysis_id':uid(501),'baby_id':BABY_A,'episode_id':uid(301),'audio_id':uid(401),'created_by_user_id':USERS['owner_a'],'status':'COMPLETE','stage':'FINISHED','attempt_no':1,'lease_expires_at':None,'quality_status':'PASS','quality_reasons':[],'cry_detected':True,'audio_candidates':[{'code':'hungry','label':'수유 필요 신호 확인','rank':1}],'abstain_reason':None,'failure':None,'model_version':'stub-audio-v1','preprocess_version':'stub-preprocess-v1','label_mapping_version':'fixture-labels-v1','context_snapshot':None,'recommendation':None,'inference_mode':'STUB','inference_executed':False,'data_origin':'DEMO','recorded_at':NOW,'completed_at':NOW}
analysis['recommendation']={'recommendation_id':uid(502),'analysis_id':uid(501),'context_snapshot_id':None,'status':'GENERAL_CHECKLIST','actions':[{'action_type':'FEEDING','text':'수유가 필요한 신호가 있는지 살펴보세요.','evidence_refs':[{'kind':'AUDIO','resource_id':uid(401),'version':1}]}],'optional_questions':[],'help_action':None,'policy_version':'stub-policy-v1','supersedes_id':None,'recorded_at':NOW}
feeding={'type':'FEEDING','occurred_at':NOW,'ended_at':None,'time_precision':'EXACT','payload':{'mode':'FORMULA','amount_ml':80,'duration_minutes':None}}
event={'care_event_id':uid(601),'baby_id':BABY_A,'created_by_user_id':USERS['caregiver_a'],'updated_by_user_id':USERS['caregiver_a'],'source_entry_id':None,'status':'ACTIVE','event':feeding,'data_origin':'DEMO',**VER}
empty_details={k:None for k in SCHEMAS['ErrorDetails']['properties']}
def err(code,message,retryable=False,**details):
    return {'code':code,'message':message,'retryable':retryable,'request_id':uid(900),'field_errors':[],'details':{**empty_details,**details}}
fixtures=[]
def fixture(name,operation,status,response,expected,request=None):
    item={'name':name,'operation_id':operation,'synthetic':True,'expected_ui':expected,'response':{'status':status,'headers':{'X-Request-ID':uid(900)},'body':response}}
    if request is not None:
        item['request']={'headers':{'Idempotency-Key':request['client_request_id']},'body':request}
    fixtures.append(item)

fixture('baby_created','createBaby',201,{'baby':baby,'membership':member},'아기와 OWNER 관계를 함께 표시',{'client_request_id':uid(801),'alias':'예시 아기 A','birth_date':'2026-07-01','feeding_mode':'MIXED','timezone':'Asia/Seoul'})
fixture('active_baby_lost_membership','getActiveBaby',200,{'baby_id':None},'이전 아기 화면과 캐시를 비우고 선택 화면으로 이동')
challenge={'challenge_id':uid(830),'user_id':USERS['owner_a'],'requested_session_id':uid(831),'operation':'CREATE_INVITE','baby_id':BABY_A,'auth_method':'SUPABASE_OTP','status':'PENDING','created_at':NOW,'expires_at':'2026-09-19T09:10:00Z'}
fixture('reauthentication_challenge','createReauthenticationChallenge',201,challenge,'10분 안에 새 이메일 OTP 인증 화면으로 이동, access token 갱신만으로 완료 처리하지 않음',{'client_request_id':uid(832),'operation':'CREATE_INVITE','baby_id':BABY_A})
proof={'proof_id':uid(833),'challenge_id':uid(830),'user_id':USERS['owner_a'],'session_id':uid(834),'operation':'CREATE_INVITE','baby_id':BABY_A,'proof_token':'synthetic-one-time-proof-not-a-live-secret','token_reissue_required':False,'issued_at':'2026-09-19T09:01:00Z','expires_at':'2026-09-19T09:06:00Z'}
fixture('reauthentication_proof','createReauthenticationProof',201,proof,'현재 OTP 세션·작업·아기에 묶인 5분 일회용 증명을 메모리에만 유지',{'client_request_id':uid(835),'challenge_id':uid(830)})
revocation={'revocation_id':uid(836),'requester_user_id':USERS['owner_a'],'requester_session_id':uid(834),'scope':'OTHERS','status':'COMPLETE','target_session_count':1,'provider_scope':'others','provider_http_status':200,'failure':None,'access_blocked':True,'requested_at':NOW,'completed_at':'2026-09-19T09:00:01Z'}
fixture('other_sessions_revoked','revokeSessions',200,revocation,'다른 기기의 로컬 접근 차단과 제공자 refresh 세션 종료 완료를 함께 표시',{'client_request_id':uid(837),'scope':'OTHERS'})
fixture('session_revocation_provider_failed','revokeSessions',503,err('AUTH_PROVIDER_REVOCATION_FAILED','제공자 세션 종료를 완료하지 못했어요.',True,session_revocation_id=uid(838),status_url=f'/v1/auth/session-revocations/{uid(838)}'),'로컬 접근 차단은 유지하고 로그아웃 완료가 아닌 재시도 가능한 실패로 표시',{'client_request_id':uid(839),'scope':'CURRENT'})
fixture('child_data_unverified','getChildDataVerification',200,{'baby_id':BABY_A,'subject_user_id':USERS['owner_a'],'status':'UNVERIFIED','method':None,'policy_version':None,'verified_at':None,'production_processing_allowed':False},'OWNER·이메일 OTP만으로 법정대리인 확인 완료로 표시하지 않고 실사용 처리를 잠금')
fixture('analysis_complete_stub','createAnalysis',200,analysis,'개발용 고정 응답 및 예시 자료 표시',{'client_request_id':uid(802),'analysis_id':uid(501),'audio_id':uid(401)})
running={**analysis,'status':'RUNNING','stage':'INFERENCE','lease_expires_at':'2026-09-19T09:01:00Z','audio_candidates':[],'recommendation':None,'completed_at':None}
fixture('analysis_running','getAnalysis',200,running,'분석 중, 같은 ID로 조회')
fixture('analysis_abstain','getAnalysis',200,{**analysis,'status':'ABSTAIN','audio_candidates':[],'recommendation':None,'quality_status':'INSUFFICIENT','quality_reasons':['HIGH_NOISE'],'cry_detected':None,'abstain_reason':'LOW_QUALITY'},'판단 유보 및 입력 품질 사유 표시, 처리 실패로 집계하지 않음')
fixture('analysis_no_cry','getAnalysis',200,{**analysis,'status':'ABSTAIN','audio_candidates':[],'recommendation':None,'quality_reasons':['NO_CRY'],'cry_detected':False,'abstain_reason':'NO_CRY'},'울음이 확인되지 않았어요')
fixture('analysis_failed','getAnalysis',200,{**analysis,'status':'FAILED','audio_candidates':[],'recommendation':None,'failure':{'code':'ANALYSIS_TIMEOUT','message':'처리 시간이 초과됐어요.','retryable':True}},'HTTP 조회 성공과 분석 작업 실패를 구분')
fixture('analysis_in_progress','createAnalysis',409,err('ANALYSIS_IN_PROGRESS','이미 분석 중이에요.',existing_analysis_id=uid(501),status_url=f'/v1/analyses/{uid(501)}'),'새 분석 생성 없이 기존 분석 조회')
fixture('idempotent_replay','createAnalysis',200,analysis,'같은 요청을 다시 보내도 같은 분석 ID와 결과')
fixture('idempotency_mismatch','createAnalysis',409,err('IDEMPOTENCY_KEY_REUSED','요청 키와 내용이 일치하지 않아요.'),'내용을 바꾼 새 작업에는 새 키 사용')
fixture('auth_expired','getAnalysis',401,err('TOKEN_EXPIRED','다시 로그인해 주세요.'),'갱신 1회 후 재로그인')
fixture('owner_required','patchBaby',403,err('OWNER_ONLY','관리 보호자만 바꿀 수 있어요.'),'권한 안내, 반복 재시도 금지')
fixture('not_member','getEpisode',404,err('RESOURCE_NOT_FOUND','찾을 수 없거나 접근할 수 없어요.'),'아기 데이터와 식별 정보 노출하지 않음')
latest={**event,'version':2}
fixture('edit_conflict','patchCareEvent',409,err('VERSION_CONFLICT','다른 보호자가 먼저 수정했어요.',current_version=2,current_resource=latest,resource_type='CARE_EVENT'),'최신 수유량과 로컬 초안 비교',{'client_request_id':uid(803),'version':1,'event':feeding})
fixture('owner_cannot_leave','removeMembership',409,err('OWNER_REQUIRED','관리 보호자는 나갈 수 없어요.'),'아기 공간 전체 삭제 경로 안내')
fixture('invite_expired','acceptInvite',410,err('INVITE_EXPIRED','초대가 만료됐어요.'),'관리 보호자에게 새 초대 요청')
fixture('invite_wrong_email','acceptInvite',403,err('INVITE_EMAIL_MISMATCH','초대받은 이메일로 로그인해 주세요.'),'계정 전환 안내')
fixture('invite_used','acceptInvite',409,err('INVITE_ALREADY_USED','이미 사용된 초대예요.'),'기존 참여 상태 확인')
sleep_event={**event,'care_event_id':uid(604),'event':{'type':'SLEEP','occurred_at':NOW,'ended_at':None,'time_precision':'EXACT','payload':{}}}
fixture('sleep_conflict','createCareEvent',409,err('SLEEP_ALREADY_ACTIVE','진행 중인 수면 기록이 있어요.',current_resource=sleep_event,resource_type='CARE_EVENT'),'기존 수면 종료 또는 수정 선택')
fixture('detection_conflict','createObservationSession',409,err('ACTIVE_SESSION_EXISTS','다른 기기에서 감지 중이에요.',existing_session_id=uid(701)),'기존 세션 명시적 종료 후 새 감지 시작')

raw='분유 80mL 먹였어요'
content={'actions':[{'action_ref':'a1','action_code':'FEEDING','assertion':'PERFORMED','performed_by_user_id':None,'occurred_at':None,'relative_time':None,'time_precision':'UNKNOWN','sequence':1,'amount':80,'unit':'ML','feeding_mode':'FORMULA','evidence':[{'source':'TEXT','choice_id':None,'span_start':0,'span_end':len(raw),'quote':raw}]}],'states':[],'outcomes':[],'caregiver_interpretations':[],'unresolved':[{'field':'actions.a1.occurred_at','code':'UNKNOWN_TIME','message':'수유한 시각을 확인하거나 모름으로 저장해 주세요.'}]}
run={'run_id':uid(751),'entry_id':uid(750),'input_revision':1,'status':'COMPLETE','lease_expires_at':None,'execution_mode':'STUB','provider':None,'model':None,'prompt_version':'fixture-prompt-v1','schema_version':'1.0.0','ontology_version':'care-v1','result':content,'failure':None,'recorded_at':NOW,'completed_at':NOW}
entry={'entry_id':uid(750),'baby_id':BABY_A,'author_user_id':USERS['owner_a'],'original_author_user_id':USERS['owner_a'],'episode_id':uid(301),'input_mode':'TEXT','raw_text':raw,'choices':[],'occurred_at':None,'time_precision':'UNKNOWN','input_revision':1,'status':'REVIEW_READY','normalization_run_id':uid(751),'normalized_content':content,'supersedes_entry_id':None,'base_record_versions':[],'confirmed_resources':None,'confirmed_by_user_id':None,'confirmed_at':None,'data_origin':'DEMO',**VER}
fixture('normalization_review','createNormalization',200,run,'확인 전 초안, 실제 수행 기록과 분리',{'client_request_id':uid(804),'run_id':uid(751),'input_revision':1})
fixture('draft_private','getCareEntry',200,entry,'작성자 본인의 초안만 표시')
fixture('draft_list_private','listMyCareEntries',200,{'items':[entry],'next_cursor':None},'현재 사용자와 아기에 묶인 서버 초안만 복구하며 공동 타임라인에는 넣지 않음')
fixture('draft_other_author','getCareEntry',404,err('RESOURCE_NOT_FOUND','찾을 수 없거나 접근할 수 없어요.'),'OWNER라도 타인 미공유 초안을 표시하지 않음')
fixture('normalization_failure','getNormalization',200,{**run,'status':'FAILED','result':None,'failure':{'code':'NORMALIZATION_TIMEOUT','message':'문장을 정리하지 못했어요.','retryable':True}},'원문을 보존하고 선택지 직접 정리 제공')
fixture('stale_draft','confirmCareEntry',409,err('SOURCE_REVISION_CHANGED','원문이 바뀌어 다시 확인해야 해요.',current_version=2,resource_type='CARE_ENTRY'),'오래된 정규화 결과를 적용하지 않음')
confirmed={'entry_id':uid(750),'input_revision':1,'care_event_ids':[uid(601)],'action_ids':[uid(602)],'action_group_ids':[],'state_observation_ids':[],'outcome_ids':[],'label_annotation_ids':[uid(603)]}
fixture('confirm_once','confirmCareEntry',200,confirmed,'생활 기록과 행동 한 번만 저장, 반응은 미확인',{'client_request_id':uid(805),'input_revision':1,'run_id':uid(751),'normalization_mode':'LLM','content':content,'base_record_versions':[]})
fixture('confirm_replay','confirmCareEntry',200,confirmed,'같은 저장 요청은 같은 생성 ID 반환')

job={'deletion_job_id':uid(780),'requester_user_id':USERS['owner_a'],'baby_id':BABY_A,'scope':'ALL','resource_id':None,'status':'PENDING','access_blocked':True,'requested_at':NOW,'completed_at':None,'failure':None,'pending_categories':['AUDIO','RAW_TEXT','RECORDS','ANALYSES','DERIVED_FEATURES','TRAINING_COPIES'],'attempt_no':1}
fixture('deletion_accepted','deleteBabyData',202,job,'삭제 요청 접수, 접근 차단, 아직 완료 아님')
fixture('resource_deleting','createCareEvent',409,err('RESOURCE_DELETING','아기 자료를 삭제하고 있어요.',deletion_job_id=uid(780),status_url=f'/v1/deletions/{uid(780)}'),'신규 저장 차단 및 삭제 진행 안내')
fixture('deletion_failed','getDeletion',200,{**job,'status':'FAILED','failure':{'code':'CLEANUP_FAILED','message':'일부 자료 정리가 남아 있어요.','retryable':True},'pending_categories':['TRAINING_COPIES']},'일부 자료 정리 중, 차단 유지, 재시도 제공')
fixture('deletion_complete','getDeletion',200,{**job,'status':'COMPLETE','completed_at':NOW,'pending_categories':[]},'완료 확인 뒤 삭제 완료 표시')
fixture('consent_revoke_after_leave','setMyTrainingConsent',200,{'consent_id':uid(790),'baby_id':BABY_A,'actor_user_id':USERS['removed_a'],'scope':'CONTRIBUTOR_TRAINING','status':'REVOKED','policy_version':'fixture-policy-v1','granted_at':'2026-09-18T09:00:00Z','revoked_at':NOW,'version':2},'탈퇴 후 본인 학습 참여 철회, 공동 기록 접근은 열리지 않음',{'client_request_id':uid(806),'granted':False,'policy_version':'fixture-policy-v1','version':1})
fixture('care_event_saved','createCareEvent',201,event,'80mL 수유 기록 저장',{'client_request_id':uid(807),'event':feeding})
fixture('changes_initial_resync','getChanges',200,{'baby_id':BABY_A,'current_revision':8,'changes':[],'resync_required':True,'server_time':NOW},'since_revision=0이므로 권한 있는 현재 자료를 전체 재조회하고 8은 성공 후에만 저장')
fixture('changes_incremental','getChanges',200,{'baby_id':BABY_A,'current_revision':10,'changes':[{'resource_type':'CARE_EVENT','resource_id':uid(601),'version':3,'deleted':False},{'resource_type':'BABY','resource_id':BABY_A,'version':8,'deleted':False},{'resource_type':'MEMBERSHIP','resource_id':uid(204),'version':1,'deleted':False}],'resync_required':False,'server_time':NOW},'since_revision=8 이후 10까지 CareEvent와 갱신된 Baby context를 재조회한 뒤 10을 저장')
fixture('changes_empty','getChanges',200,{'baby_id':BABY_A,'current_revision':10,'changes':[],'resync_required':False,'server_time':NOW},'추가 변경 없음; 기존 캐시와 revision 10을 유지')
fixture('changes_deleted','getChanges',200,{'baby_id':BABY_A,'current_revision':11,'changes':[{'resource_type':'CARE_EVENT','resource_id':uid(601),'version':4,'deleted':True},{'resource_type':'BABY','resource_id':BABY_A,'version':9,'deleted':False}],'resync_required':False,'server_time':NOW},'CareEvent version 4 tombstone을 제거하고 공개 Baby context는 다시 조회')
fixture('changes_history_or_limit_resync','getChanges',200,{'baby_id':BABY_A,'current_revision':740,'changes':[],'resync_required':True,'server_time':NOW},'보관 경계 이전이거나 500개 초과이므로 일부 응답을 적용하지 않고 전체 재동기화')
fixture('file_rejected','completeUpload',422,err('INVALID_AUDIO','검증할 수 없는 음원 파일이에요.'),'파일 교체 및 기존 음원 REJECTED 표시')
fixture('model_unavailable','createAnalysis',503,err('MODEL_NOT_READY','분석 모델을 준비하고 있어요.',True,retry_after_seconds=5),'요청 미등록 여부 확인, 목으로 바꾸지 않음')
fixtures[-1]['response']['headers']['Retry-After']='5'
invite={'invite_id':uid(260),'baby_id':BABY_A,'inviter_user_id':USERS['owner_a'],'email':'invited_a@example.invalid','status':'PENDING','expires_at':'2026-09-20T09:00:00Z',**VER}
fixture('invite_issued','createInvite',201,{'invite':invite,'invite_url':'https://example.invalid/invite#synthetic-not-a-working-token','link_reissue_required':False},'관리 보호자가 최초 한 번 표시된 링크를 복사해 직접 전달',{'client_request_id':uid(840),'email':'invited_a@example.invalid'})
fixtures[-1]['request']['headers']['X-Reauthentication-Proof']='synthetic-one-time-proof-not-a-live-secret'
fixture('invite_replay_no_token','createInvite',201,{'invite':invite,'invite_url':None,'link_reissue_required':True},'동일 초대는 유지, 링크를 잃었으면 명시적 재발급')
reissued_invite={**invite,'invite_id':uid(261),'version':1}
fixture('invite_reissued','reissueInvite',201,{'invite':reissued_invite,'invite_url':'https://example.invalid/invite#synthetic-reissued-token','link_reissue_required':False},'이전 링크를 폐기하고 새 링크를 최초 한 번 표시',{'client_request_id':uid(841)})
fixtures[-1]['request']['headers']['X-Reauthentication-Proof']='synthetic-one-time-proof-not-a-live-secret'
fixture('invite_accepted','acceptInvite',200,{'baby':baby,'membership':{**member,'membership_id':uid(204),'user_id':USERS['invited_a'],'role':'CAREGIVER','display_name':'시험 초대자'}},'공유 범위 수락 후 아기 홈으로 이동')
fixture('member_left','removeMembership',200,{**member,'membership_id':uid(202),'user_id':USERS['caregiver_a'],'role':'CAREGIVER','status':'LEFT'},'권한·캐시 정리, 기록 삭제와 동의 철회는 별도')
fixture('normalization_running','getNormalization',200,{**run,'status':'RUNNING','result':None,'completed_at':None,'lease_expires_at':'2026-09-19T09:00:30Z'},'정리 중, 기존 run ID 조회')
fixture('normalization_stale','getNormalization',200,{**run,'status':'STALE'},'원문이 수정되어 이전 초안을 적용하지 않음')
empty_summary={'baby_id':BABY_A,'date':'2026-09-19','timezone':'Asia/Seoul','as_of':NOW,'context_revision':7,'has_records':False,'feeding':{'record_count':0,'known_amount_count':0,'unknown_amount_count':0,'total_recorded_ml':None,'breastfeeding_minutes':None},'sleep':{'record_count':0,'recorded_minutes_in_day':0,'active_sleep_id':None,'has_unknown_duration':False},'diaper':{'check_count':0,'change_count':0},'record_coverage':[],'latest_confirmed_state':None,'missing_fields':['feeding','sleep','diaper']}
fixture('summary_empty','getDailySummary',200,empty_summary,'기록 없음 표시, 실제 수유량 0으로 오인하지 않음')
fixture('summary_unknown_amount','getDailySummary',200,{**empty_summary,'has_records':True,'feeding':{'record_count':1,'known_amount_count':0,'unknown_amount_count':1,'total_recorded_ml':None,'breastfeeding_minutes':None},'missing_fields':['feeding.amount','sleep','diaper']},'수유 1회, 양 모름을 구별')
pattern={'kind':'FEEDING','status':'ON_HOLD','reason':'INSUFFICIENT_RECORDS','valid_days':1,'interval_count':2,'median_minutes':None,'p25_minutes':None,'p75_minutes':None,'anchor_event_id':None,'estimated_due_at':None,'policy_version':'fixture-pattern-v1'}
fixture('pattern_insufficient','getPatterns',200,{'baby_id':BABY_A,'range_days':7,'as_of':NOW,'items':[pattern]},'유효 간격과 날짜 부족으로 준비 시점 보류')
fixture('similar_cases_empty','getSimilarCases',200,{'baby_id':BABY_A,'analysis_id':uid(501),'items':[],'eligible_case_count':0,'reason':'NO_CASES','policy_version':'fixture-personalization-v1'},'적격 이전 사례가 없다고 표시')
state={'state_observation_id':uid(820),'baby_id':BABY_A,'episode_id':uid(301),'action_id':None,'source_entry_id':uid(750),'phase':'AFTER','observed_at':NOW,'time_precision':'EXACT','state_codes':['ASLEEP'],'observation_source':'SELF_REPORTED','confirmation_status':'USER_CONFIRMED','visual_state_code':'ASLEEP','visual_mapping_version':'care-visual-v1','created_by_user_id':USERS['owner_a'],'data_origin':'DEMO',**VER}
fixture('confirmed_state_visual','createStateObservation',201,state,'보호자가 잠듦으로 기록한 시각 표시, 자동 기분 추정 아님')

FIXTURE_DOC={'contract_version':'1.1.1','notice':'All fixtures are synthetic contract examples. No model ran, no audio is supplied, and no real account or token is created. Never count these as measured model results.',
    'test_users':[{'alias':alias,'user_id':user,'email_placeholder':alias+'@example.invalid'} for alias,user in USERS.items()],
    'test_babies':{'baby_a':BABY_A,'baby_b':BABY_B},
    'role_assignments':[{'user_id':USERS['owner_a'],'baby_id':BABY_A,'role':'OWNER','status':'ACTIVE'}, {'user_id':USERS['caregiver_a'],'baby_id':BABY_A,'role':'CAREGIVER','status':'ACTIVE'}, {'user_id':USERS['owner_b'],'baby_id':BABY_B,'role':'OWNER','status':'ACTIVE'}, {'user_id':USERS['owner_a'],'baby_id':BABY_B,'role':'CAREGIVER','status':'ACTIVE'}, {'user_id':USERS['removed_a'],'baby_id':BABY_A,'role':'CAREGIVER','status':'REVOKED'}],
    'scenarios':fixtures}

def build_contract():
    (OUT/'openapi계약.json').write_text(json.dumps(DOC,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    (OUT/'목 응답과 시험 사용자 배치.json').write_text(json.dumps(FIXTURE_DOC,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    return {'operations':len(REGISTRY),'paths':len(PATHS),'schemas':len(SCHEMAS),'scenarios':len(fixtures)}


if __name__ == '__main__':
    print(json.dumps(build_contract(),ensure_ascii=False))
