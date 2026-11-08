"""Provider safety and replay behavior, using synthetic responses only."""
from decimal import Decimal
from types import SimpleNamespace
import json
import pytest
import apoyo_sensibilidad_pareada as paired


@pytest.fixture
def experiment(tmp_path, monkeypatch):
    ctx=SimpleNamespace(work=tmp_path/'experiment')
    request={'topic_id':0,'evidence_ids':['review-1'],
             'request':{'body':{'model':'google/gemini-2.5-flash','max_tokens':800,
                                 'messages':[{'role':'user','content':'Synthetic review'}]}}}
    key=paired.fingerprint(request['request']);request['request_fingerprint']=key
    calls=[]
    monkeypatch.setenv('OPENROUTER_API_KEY','synthetic-test-key')
    monkeypatch.setattr(paired,'find_reusable',lambda _: {})
    monkeypatch.setattr(paired,'_catalog',lambda _: (Decimal('.0000003'),Decimal('.0000025')))
    monkeypatch.setattr(paired.openrouter,'create_client',lambda: SimpleNamespace(close=lambda:None))
    response={'id':'synthetic-response','model':'google/gemini-2.5-flash',
              'choices':[{'finish_reason':'stop','message':{'content':json.dumps({
                  'name':'Prueba','topic_description':'Ejemplo sintético','assessment':'unclear',
                  'proposed_feature':None,'supporting_evidence':['E1']})}}],
              'usage':{'prompt_tokens':30,'completion_tokens':40,'cost':.0001}}
    def send(client,body):calls.append(body);return response
    monkeypatch.setattr(paired.openrouter,'request',send)
    return ctx,key,{key:request},calls,response


def test_budget_stops_before_request(experiment):
    ctx,key,requests,calls,response=experiment
    with pytest.raises(RuntimeError,match='Presupuesto'):
        paired.interpret_all(ctx,requests,live=True,max_usd=0)
    assert calls==[]
    assert not (ctx.work/'llm'/key).exists()


def test_completed_request_replays_without_provider(experiment):
    ctx,key,requests,calls,response=experiment
    records,receipt=paired.interpret_all(ctx,requests,live=True)
    assert records[key]['status']=='completed' and receipt['new_provider_attempts']==1
    again,receipt=paired.interpret_all(ctx,requests,live=False)
    assert again==records and len(calls)==1 and receipt['new_provider_attempts']==0
    (ctx.work/'llm'/key/'response.json').write_text('{}')
    with pytest.raises(AssertionError):paired.interpret_all(ctx,requests,live=True)
    assert len(calls)==1


def test_failed_unknown_cost_retains_reservation_without_retry(experiment):
    ctx,key,requests,calls,response=experiment
    response.clear();response.update({'error':'provider_error'})
    records,receipt=paired.interpret_all(ctx,requests,live=True)
    assert records[key]['status']=='failed' and records[key]['cost_usd'] is None
    assert records[key]['charged_or_reserved_usd']>0
    again,receipt=paired.interpret_all(ctx,requests,live=True)
    assert again==records and len(calls)==1 and receipt['new_provider_attempts']==0
