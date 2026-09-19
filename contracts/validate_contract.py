import copy
import json
from pathlib import Path

from build_contract import DOC, FIXTURE_DOC, REGISTRY
from openapi_spec_validator import validate
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

folder=Path(__file__).resolve().parent
doc=json.loads((folder/'openapi계약.json').read_text(encoding='utf-8'))
fixtures=json.loads((folder/'목 응답과 시험 사용자 배치.json').read_text(encoding='utf-8'))
registry=REGISTRY
assert doc==DOC, 'OpenAPI file is out of sync; run build_contract.py'
assert fixtures==FIXTURE_DOC, 'Fixture file is out of sync; run build_contract.py'
validate(doc)
schema_registry=Registry().with_resource('urn:baby-care:openapi',Resource(contents=doc,specification=DRAFT202012))
checker=FormatChecker()

def validator(name):
    return Draft202012Validator({'$ref':f'urn:baby-care:openapi#/components/schemas/{name}'},registry=schema_registry,format_checker=checker)

def check(name,value):
    validator(name).validate(value)

requests=0
for item in fixtures['scenarios']:
    op=registry[item['operation_id']]
    status=item['response']['status']
    response_name=op['response'] if status<400 else 'ApiError'
    check(response_name,item['response']['body'])
    if status>=400:
        assert doc['x-error-status'][item['response']['body']['code']]==status, item['name']
    assert str(status) in doc['paths'][op['path']][op['method'].lower()]['responses'],item['name']
    if 'request' in item:
        assert item['request']['headers']['Idempotency-Key']==item['request']['body']['client_request_id']
        check(op['request'],item['request']['body'])
        requests+=1

by_name={f['name']:f for f in fixtures['scenarios']}
negative=[]
def rejected(label,schema,value):
    errors=list(validator(schema).iter_errors(value))
    assert errors, f'Invalid example was accepted: {label}'
    negative.append(label)

a=copy.deepcopy(by_name['analysis_abstain']['response']['body'])
a['audio_candidates']=[{'code':'hungry','label':'invalid','rank':1}]
rejected('ABSTAIN cannot return cause candidates','Analysis',a)
a=copy.deepcopy(by_name['analysis_failed']['response']['body']);a['failure']=None
rejected('FAILED requires failure details','Analysis',a)
a=copy.deepcopy(by_name['analysis_complete_stub']['response']['body']);a['recommendation']=None
rejected('COMPLETE requires a saved recommendation','Analysis',a)
a=copy.deepcopy(by_name['analysis_complete_stub']['response']['body']);a['inference_executed']=True
rejected('STUB cannot claim a real model call','Analysis',a)
a=copy.deepcopy(by_name['deletion_complete']['response']['body']);a['pending_categories']=['AUDIO']
rejected('Deletion COMPLETE cannot have pending cleanup','DeletionJob',a)
a=copy.deepcopy(by_name['confirm_once']['request']['body']);a['run_id']=None
rejected('LLM confirmation needs run_id','ConfirmCareEntry',a)
a=copy.deepcopy(by_name['confirm_once']['request']['body']);a['content']['unresolved'][0]['code']='CONFLICT'
rejected('Unresolved conflicting statements cannot be confirmed','ConfirmCareEntry',a)
a={'client_request_id':'10000000-0000-4000-8000-000000000901','care_event_id':'10000000-0000-4000-8000-000000000601','new_care_event':by_name['care_event_saved']['response']['body']['event'],'recommendation_id':None,'performed_by_user_id':None,'sequence':1}
rejected('Action cannot create and link an event simultaneously','CreateAction',a)
a=copy.deepcopy(by_name['baby_created']['request']['body']);a['owner_user_id']='10000000-0000-4000-8000-000000000999'
rejected('Client cannot inject owner user id','CreateBaby',a)

def verify_refs(node):
    if isinstance(node,dict):
        if '$ref' in node:
            path=node['$ref']
            assert path.startswith('#/'), path
            resolved=doc
            for key in path[2:].split('/'):
                resolved=resolved[key.replace('~1','/').replace('~0','~')]
        for value in node.values():verify_refs(value)
    elif isinstance(node,list):
        for value in node:verify_refs(value)
verify_refs(doc)
assert len(registry)==len({v['operationId'] for p in doc['paths'].values() for v in p.values()})

summary={'openapi_validation':'passed','reference_validation':'passed','operations':len(registry),'paths':len(doc['paths']),'schemas':len(doc['components']['schemas']),'response_examples_validated':len(fixtures['scenarios']),'request_examples_validated':requests,'invalid_contract_examples_rejected':negative,'not_verified':['Live FastAPI implementation','Supabase RLS and Storage runtime behavior','Actual accounts or OTP','Real audio or LLM model execution','Browser end-to-end behavior']}
(folder/'validation.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
print(json.dumps(summary,ensure_ascii=False,indent=2))
