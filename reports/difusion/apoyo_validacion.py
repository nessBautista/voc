"""Lectura, métricas descriptivas y exportación editorial; nunca ejecuta modelos."""
from datetime import datetime, timezone
from hashlib import sha256
from itertools import combinations
from pathlib import Path
import html
import importlib.metadata
import json

import numpy as np
import pandas as pd
from IPython.display import HTML, display


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def show_texts(records, text_column="model_text"):
    """HTML escaped, wrapping full text: no pandas ellipses or HTML injection."""
    if isinstance(records, pd.DataFrame):
        records = records.to_dict("records")
    for row in records:
        meta = " · ".join(f"{k}: {v}" for k, v in row.items() if k != text_column)
        display(HTML('<article style="border:1px solid #bbb;padding:12px;margin:8px 0;overflow-wrap:anywhere">'
                     + '<small>' + html.escape(meta) + '</small><p style="white-space:pre-wrap">'
                     + html.escape(str(row[text_column])) + '</p></article>'))


def ranked_sample(frame, n, salt, distinct_text=True):
    """Stable SHA-256 ordering, independent of dataframe or Python hash order."""
    out = frame.sort_values("record_id").copy()
    if distinct_text:
        out = out.drop_duplicates("model_text")
    out["_rank"] = out.record_id.map(lambda x: sha256(f"{salt}:{x}".encode()).hexdigest())
    return out.sort_values(["_rank", "record_id"]).head(n).drop(columns="_rank")


def topic_review_cases(topic_tables, frames, analyses):
    """Two topics per rank-tertile; five distinct random texts + up to two saved."""
    cases, selection = [], []
    for system, table in topic_tables.items():
        ordered = table.sort_values(["size", "topic_id"])
        lookup = {t.topic_id: t for t in analyses[system].topics}
        for band, indices in zip(["pequeno", "mediano", "grande"], np.array_split(np.arange(len(ordered)), 3)):
            candidates = ordered.iloc[indices].copy()
            candidates["_rank"] = candidates.topic_id.map(lambda t: sha256(f"topics-v1:42:{system}:{t}".encode()).hexdigest())
            for row in candidates.sort_values(["_rank", "topic_id"]).head(2).itertuples():
                members = frames[system].loc[frames[system].topic_id.eq(row.topic_id)]
                picked = ranked_sample(members, 5, f"members-v1:42:{system}:{row.topic_id}")
                evidence = members.loc[members.record_id.isin(lookup[row.topic_id].evidence_ids)]
                evidence = evidence.loc[~evidence.model_text.isin(picked.model_text)].sort_values("record_id").drop_duplicates("model_text").head(2)
                texts = []
                for origin, subset in [("muestra_hash", picked), ("evidencia_guardada", evidence)]:
                    for r in subset.itertuples():
                        texts.append({"record_id":r.record_id,"platform":r.platform,"model_text":r.model_text,"origin":origin})
                key=f"{system}:{row.topic_id}"
                case={"case_id":key,"system":system,"topic_id":int(row.topic_id),"band":band,"size":int(row.size),"keywords":row.keywords,"reviews":texts}
                cases.append(case)
                selection.append({k:v for k,v in case.items() if k!='reviews'})
    return cases, pd.DataFrame(selection)


def lexical_metrics(topics, texts, analyzer, k=10):
    """NPMI on document occurrence; OOV / p_joint=1 pairs undefined, zero joint=-1."""
    docs = [set(analyzer(str(t))) for t in texts]
    n = len(docs)
    all_terms = {w for t in topics for w in t[:k]}
    occurrence = {w: np.array([w in d for d in docs], dtype=bool) for w in all_terms}
    results=[]
    for terms in topics:
        terms=list(dict.fromkeys(w for w in terms[:k] if w))
        scores=[]; absent=0; degenerate=0; zero=0
        for a,b in combinations(terms,2):
            pa=occurrence[a].mean();pb=occurrence[b].mean();joint=(occurrence[a]&occurrence[b]).mean()
            if pa == 0 or pb == 0:
                absent+=1
            elif joint == 1:
                degenerate+=1
            elif joint == 0:
                scores.append(-1.0);zero+=1
            else:
                scores.append(float(np.log(joint/(pa*pb)) / -np.log(joint)))
        results.append({"npmi_document":float(np.mean(scores)) if scores else np.nan,
                        "terms":len(terms),"terms_found":sum(occurrence[w].any() for w in terms),
                        "pairs":len(terms)*(len(terms)-1)//2,"evaluated_pairs":len(scores),
                        "oov_pairs":absent,"degenerate_pairs":degenerate,"zero_cooccurrence_pairs":zero})
    slots=sum(len(list(dict.fromkeys(t[:k]))) for t in topics)
    return pd.DataFrame(results), {"unique_terms":len(all_terms),"term_slots":slots,"diversity":len(all_terms)/slots if slots else np.nan,"reference_rows":n}


REVIEW_FIELDS = ["coherencia", "palabras_clave", "tipo_asunto", "justificacion", "revisor", "fecha"]


def review_sheet(path, selection, protocol_id):
    """Create only if missing; never overwrite annotations; reject changed selection."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    template=selection[["case_id", "system", "topic_id", "band", "size"]].copy()
    template.insert(0,"protocol_id",protocol_id)
    for col in REVIEW_FIELDS:template[col]=""
    if not path.exists():
        with path.open('x') as f:template.to_csv(f,index=False)
    saved=pd.read_csv(path,keep_default_na=False,dtype=str)
    if list(saved.columns)!=list(template.columns):raise ValueError('La plantilla de revisión cambió: conservar el archivo y revisar su esquema.')
    identity=list(template.columns[:-len(REVIEW_FIELDS)])
    expected=template[identity].astype(str).sort_values('case_id').reset_index(drop=True)
    actual=saved[identity].sort_values('case_id').reset_index(drop=True)
    if not actual.equals(expected):raise ValueError('La revisión pertenece a otra selección o protocolo; no se sobrescribe.')
    allowed={"coherencia":{"0","1","2","no_evaluable"},"palabras_clave":{"0","1","2","no_evaluable"},"tipo_asunto":{"funcionalidad","mixto","valoracion_general","otro","no_evaluable"}}
    for col,values in allowed.items():
        if not set(saved[col]) <= values|{""}:raise ValueError(f'Valores desconocidos en {col}')
    saved["completa"]=saved[REVIEW_FIELDS].apply(lambda s:s.str.strip().ne("")).all(axis=1)
    return saved


def export_bundle(notebook, figures, tables, payloads, sources, scope):
    """Versioned export inside reports/difusion; review files are never outputs here."""
    notebook=Path(notebook)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out=notebook.parent/'exports'/notebook.stem/stamp
    out.mkdir(parents=True,exist_ok=False)
    for name,fig in figures.items():
        fig.savefig(out/f'{name}.png',dpi=180,bbox_inches='tight',facecolor='white')
        fig.savefig(out/f'{name}.pdf',bbox_inches='tight',facecolor='white')
    for name,table in tables.items():table.to_csv(out/f'{name}.csv',index=False)
    for name,value in payloads.items():
        (out/f'{name}.json').write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    nb=json.loads(notebook.read_text())
    code='\n'.join(''.join(c.get('source',[])) for c in nb['cells'])
    files={p.name:digest(p) for p in out.iterdir()}
    manifest={"created_at":stamp,"notebook":notebook.name,"cell_sources_sha256":sha256(code.encode()).hexdigest(),
              "helper_sha256":digest(Path(__file__)),"scope":scope,"sources":sources,"files":files,
              "software":{name:importlib.metadata.version(name) for name in ['numpy','pandas','matplotlib','scikit-learn']},
              "model_fits":0,"llm_requests":0}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    pointer = {"directory":str(out.relative_to(notebook.parent)), "manifest_sha256":digest(out/'manifest.json')}
    (out.parent/'latest.json').write_text(json.dumps(pointer,indent=2)+'\n')
    print('Evidencia exportada:',out)
    return out
