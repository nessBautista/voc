"""Checks for meaningful metric edge cases and preservation of human annotations."""
import numpy as np
import pandas as pd
import pytest
from apoyo_validacion import lexical_metrics, ranked_sample, review_sheet


def test_npmi_perfect_joint_absent_and_zero_joint():
    # a,b always cooccur in half of documents => 1; c occurs in opposite half => -1.
    table, summary=lexical_metrics([["a","b"],["a","c"],["absent","a"]],["a b","a b","c","c"],str.split)
    assert np.isclose(table.iloc[0].npmi_document,1)
    assert table.iloc[1].npmi_document == -1
    assert np.isnan(table.iloc[2].npmi_document)
    assert table.iloc[2].oov_pairs==1
    assert summary['reference_rows']==4


def test_npmi_ubiquitous_pair_is_not_arbitrary_perfect_score():
    table,_=lexical_metrics([["a","b"]],["a b","a b"],str.split)
    assert table.iloc[0].degenerate_pairs==1
    assert np.isnan(table.iloc[0].npmi_document)


def test_review_preserves_answers_and_rejects_different_case_identity(tmp_path):
    selected=pd.DataFrame([dict(case_id="base:0",system="base",topic_id=0,band="grande",size=50)])
    path=tmp_path/'review.csv'
    review=review_sheet(path,selected,'protocol')
    assert not review.completa.any()
    saved=pd.read_csv(path,keep_default_na=False)
    saved.loc[0,['coherencia','palabras_clave','tipo_asunto','justificacion','revisor','fecha']]=['2','1','mixto','Two aspects visible','reviewer','2026-11-05']
    saved.to_csv(path,index=False)
    before=path.read_bytes()
    assert review_sheet(path,selected,'protocol').completa.all()
    assert path.read_bytes()==before
    with pytest.raises(ValueError):review_sheet(path,selected.assign(size=51),'protocol')
    assert path.read_bytes()==before


def test_sampling_independent_of_row_order_and_distinct_text():
    f=pd.DataFrame({'record_id':['r3','r2','r1','r4'],'model_text':['same','same','other','last']})
    a=ranked_sample(f,3,'seed42');b=ranked_sample(f.iloc[::-1],3,'seed42')
    assert a.record_id.tolist()==b.record_id.tolist()
    assert a.model_text.is_unique and len(a)==3
