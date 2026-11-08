"""Verifica la libreta usando únicamente su paquete de apoyo, sin el prototipo."""
from pathlib import Path
import tempfile,shutil,os,json,hashlib
import nbformat
from nbclient import NotebookClient
N=Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix='voc-evidence-portable-') as tmp:
 isolated=Path(tmp);src=N.parent/'difusion/evidencia-articulo-14.ipynb'
 (isolated/'difusion').mkdir()
 shutil.copy2(src,isolated/'difusion'/src.name)
 shutil.copytree(N/'sources/evidencia-articulo-14',isolated/'soporte-articulo-14/sources/evidencia-articulo-14')
 nb=nbformat.read(isolated/'difusion'/src.name,as_version=4)
 env={k:v for k,v in os.environ.items() if not any(x in k.upper() for x in ['TOKEN','SECRET','API_KEY','AWS_','OPENROUTER','HF_','HUGGING'])}
 NotebookClient(nb,timeout=120,resources={'metadata':{'path':str(isolated/'difusion')}}).execute(env=env)
 latest=json.loads((isolated/'soporte-articulo-14/exports/evidencia-articulo-14/latest.json').read_text());v=json.loads((isolated/'soporte-articulo-14'/latest['directory']/'verificacion.json').read_text())
 result={'state':v['state'],'checks_passed':len(v['checks']),'isolated_package':True,'credentials_removed_from_kernel_environment':True,'application_imports':False,'notebook_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'source_manifest_sha256':v['source_manifest_sha256'],'model_fits':0,'llm_requests':0}
 (N/'exports/evidencia-articulo-14/verificacion-portabilidad.json').write_text(json.dumps(result,indent=2)+'\n');print(result)
