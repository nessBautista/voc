"""Preparar referencias humanas y leer evidencia sin entrenar ni llamar modelos."""
import hashlib
import html
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support

from apoyo_validacion import digest, ranked_sample

LABELS = ['negative', 'neutral', 'positive']
FIELDS = ['sentimiento_humano', 'justificacion', 'revisor', 'fecha']


def verified_bundle(root, relative, expected_manifest):
    """Pin an existing bundle, including all its output hashes."""
    directory = Path(root) / relative
    if digest(directory / 'manifest.json') != expected_manifest:
        raise ValueError('El manifiesto de evidencia cambió.')
    manifest = json.loads((directory / 'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        if digest(directory / name) != expected:
            raise ValueError(f'Evidencia modificada: {name}')
    return directory


def proportional_sample(frame, n=60, salt='sentiment-human-v1:42'):
    """Largest remainder by platform; deterministic ordering independent of predictions."""
    if frame.record_id.duplicated().any() or not 0 < n <= len(frame):
        raise ValueError('IDs repetidos o tamaño de muestra inválido.')
    counts = frame.groupby('platform').size().sort_index()
    quotas = counts * n / len(frame)
    allocation = np.floor(quotas).astype(int)
    remainder_order = (quotas-allocation).sort_values(ascending=False, kind='stable').index
    allocation.loc[remainder_order[:n-int(allocation.sum())]] += 1
    selected = pd.concat([ranked_sample(frame.loc[frame.platform.eq(p)], int(k), salt,
                                       distinct_text=False) for p, k in allocation.items()])
    return selected.sort_values('record_id').reset_index(drop=True)


def sample_identity(frame, protocol_id):
    records = frame[['record_id', 'platform', 'model_text']].sort_values('record_id').to_dict('records')
    return hashlib.sha256(json.dumps({'protocol':protocol_id, 'policy':'sentiment-human-v1:42',
                                      'records':records}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def sentiment_sheet(path, sample, sample_id):
    """Create once; preserve annotations and reject identity drift or unknown labels."""
    template = sample[['record_id', 'platform', 'model_text']].copy()
    template.insert(0, 'sample_id', sample_id)
    for field in FIELDS:
        template[field] = ''
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open('x') as stream:
            template.to_csv(stream, index=False)
    saved = pd.read_csv(path, dtype=str, keep_default_na=False)
    if list(saved.columns) != list(template.columns):
        raise ValueError('Esquema de anotación alterado.')
    identity = ['sample_id', 'record_id', 'platform', 'model_text']
    if not saved[identity].sort_values('record_id').reset_index(drop=True).equals(
        template[identity].astype(str).sort_values('record_id').reset_index(drop=True)):
        raise ValueError('Selección o textos alterados; se conserva el archivo para inspección.')
    if not set(saved.sentimiento_humano) <= set(LABELS + ['uncertain', '']):
        raise ValueError('Etiqueta humana desconocida.')
    saved['completa'] = saved[FIELDS].apply(lambda s:s.str.strip().ne('')).all(axis=1)
    return saved


def sentiment_metrics(review, predictions):
    """No selective early scores: require every case reviewed; uncertain is counted/excluded."""
    if review.record_id.duplicated().any() or predictions.record_id.duplicated().any():
        raise ValueError('IDs repetidos.')
    if not set(review.record_id) <= set(predictions.record_id):
        raise ValueError('Faltan predicciones para la muestra.')
    if not set(review.sentimiento_humano) <= set(LABELS + ['uncertain', '']):
        raise ValueError('Etiqueta humana desconocida.')
    complete = review[FIELDS].apply(lambda s:s.str.strip().ne('')).all(axis=1)
    status = {'selected':len(review), 'reviewed':int(complete.sum()),
              'uncertain':int(review.sentimiento_humano.eq('uncertain').sum()),
              'state':'pending_human_review', 'independent_test':False,
              'training_overlap':'unknown: individual historical training/test IDs unavailable'}
    if not complete.all():
        return status, pd.DataFrame(), pd.DataFrame(), {}
    valid = review.loc[review.sentimiento_humano.isin(LABELS)].merge(
        predictions, on='record_id', validate='one_to_one')
    status.update(evaluated=len(valid), state='review_complete' if len(valid) else 'no_evaluable_reference')
    if not len(valid):
        return status, pd.DataFrame(), pd.DataFrame(), {}
    rows, classes, matrices = [], [], {}
    for system in ['base', 'alternative']:
        if not set(valid[system]) <= set(LABELS):
            raise ValueError('Predicción desconocida.')
        precision, recall, f1, support = precision_recall_fscore_support(
            valid.sentimiento_humano, valid[system], labels=LABELS, zero_division=0)
        rows.append({'system':system,'rows':len(valid),'accuracy':accuracy_score(valid.sentimiento_humano,valid[system]),
                     'macro_f1_fixed_three_classes':float(f1.mean())})
        for i,label in enumerate(LABELS):
            classes.append({'system':system,'label':label,'precision':precision[i],
                            'recall':recall[i],'f1':f1[i],'support':int(support[i])})
        matrices[system] = confusion_matrix(valid.sentimiento_humano, valid[system], labels=LABELS)
    return status, pd.DataFrame(rows), pd.DataFrame(classes), matrices


def blind_packet(sample):
    """Allowlist: never include ratings, topic, predictions or model identities."""
    intro = '<!doctype html><html lang="es"><meta charset="utf-8"><title>Revisión de sentimiento</title><style>body{max-width:850px;margin:2em auto;font:17px/1.6 sans-serif}article{border:1px solid #bbb;padding:1em;margin:1em 0;overflow-wrap:anywhere}p{white-space:pre-wrap}</style><h1>Referencia humana de sentimiento</h1><p>Valora el tono del texto completo: negative (descontento), neutral (descripción o consulta sin valoración), positive (satisfacción), uncertain (no se puede resolver o mezcla sin predominio). Anota etiqueta, justificación, revisor y fecha en sentimiento.csv. No consultes las predicciones antes de anotar.</p>'
    cards = ''.join('<article><small>'+html.escape(str(row.record_id))+' · '+html.escape(str(row.platform))+'</small><p>'+html.escape(str(row.model_text))+'</p></article>' for row in sample.itertuples())
    return intro + cards + '</html>'


def citation_audit(attempt):
    evidence = attempt['request']['evidence']
    ids = [x['id'] for x in evidence]
    citations = attempt['response']['supporting_review_ids']
    if len(ids) != len(set(ids)):
        raise ValueError('Identificadores de evidencia repetidos.')
    unknown = sorted(set(citations)-set(ids))
    return {'provided':len(ids),'cited':len(citations),'unknown_citations':unknown,
            'references_exist':not unknown, 'semantic_validation':'not established by reference existence'}
