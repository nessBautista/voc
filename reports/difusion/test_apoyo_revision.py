import json
import pandas as pd
import pytest

from apoyo_revision import (proportional_sample, sample_identity, sentiment_sheet,
                            sentiment_metrics, blind_packet, citation_audit, verified_bundle)
from apoyo_validacion import digest


def population():
    return pd.DataFrame({'record_id':[f'id{i}' for i in range(20)],
                         'platform':['store']*15+['social']*5,
                         'model_text':['texto']*20})


def test_selection_does_not_depend_on_order_or_predictions():
    frame=population()
    a=proportional_sample(frame,8)
    changed=frame.sample(frac=1,random_state=7).assign(model_label='positive')
    b=proportional_sample(changed,8)
    assert a.record_id.tolist()==b.record_id.tolist()
    assert a.platform.value_counts().to_dict()=={'store':6,'social':2}
    assert len(a)==8  # Text duplicates retained as separate records.


def test_sheet_preserves_review_and_rejects_changed_text(tmp_path):
    sample=proportional_sample(population(),8)
    identity=sample_identity(sample,'protocol')
    path=tmp_path/'review.csv'
    review=sentiment_sheet(path,sample,identity).drop(columns='completa')
    review.loc[0,['sentimiento_humano','justificacion','revisor','fecha']]=['negative','Motivo','Humano','2026-11-06']
    review.to_csv(path,index=False)
    before=path.read_bytes()
    loaded=sentiment_sheet(path,sample,identity)
    assert loaded.completa.sum()==1 and path.read_bytes()==before
    sample.loc[0,'model_text']='texto alterado'
    with pytest.raises(ValueError,match='alterados'):
        sentiment_sheet(path,sample,identity)
    assert path.read_bytes()==before


def test_metrics_wait_for_all_reviews_and_use_same_evaluable_ids(tmp_path):
    sample=population().head(4)
    review=sentiment_sheet(tmp_path/'r.csv',sample,'identity')
    predictions=pd.DataFrame({'record_id':sample.record_id,'base':['negative','neutral','positive','positive'],
                              'alternative':['positive','neutral','positive','negative']})
    status,metrics,_,_=sentiment_metrics(review,predictions)
    assert status['state']=='pending_human_review' and metrics.empty
    review['sentimiento_humano']=['negative','neutral','positive','uncertain']
    review['justificacion']='Explicación';review['revisor']='Humano';review['fecha']='2026-11-06'
    status,metrics,classes,matrices=sentiment_metrics(review,predictions)
    assert status['evaluated']==3 and status['uncertain']==1
    assert metrics.rows.tolist()==[3,3]
    assert metrics.iloc[0].accuracy==1
    assert metrics.iloc[1].accuracy==pytest.approx(2/3)
    assert matrices['base'].trace()==3
    assert classes.groupby('system').support.sum().tolist()==[3,3]


def test_blind_packet_escapes_text_and_excludes_predictions():
    sample=population().head(1).assign(model_text='<script>alert(1)</script>',rating='SECRET_RATING',model_label='SECRET_PREDICTION')
    packet=blind_packet(sample)
    assert '<script>' not in packet and '&lt;script&gt;' in packet
    assert 'SECRET_RATING' not in packet and 'SECRET_PREDICTION' not in packet


def test_citations_exist_is_not_semantic_validation():
    attempt={'request':{'evidence':[{'id':'e1','text':'abc'}]},'response':{'supporting_review_ids':['e1','e2']}}
    result=citation_audit(attempt)
    assert not result['references_exist'] and result['unknown_citations']==['e2']


def test_export_corruption_rejected(tmp_path):
    out=tmp_path/'export';out.mkdir()
    data=out/'data.json';data.write_text('{}')
    manifest=out/'manifest.json';manifest.write_text(json.dumps({'files':{'data.json':digest(data)}}))
    expected=digest(manifest)
    assert verified_bundle(tmp_path,'export',expected)==out
    data.write_text('{"changed":true}')
    with pytest.raises(ValueError,match='modificada'):
        verified_bundle(tmp_path,'export',expected)
