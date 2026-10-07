"""Prove the copied demo does not depend on the application's Python packages."""
import ast
from pathlib import Path

import pytest

from voc_demo.settings import data_root, load_settings

ROOT = Path(__file__).resolve().parents[1]


def test_no_application_imports():
    forbidden = {'voc', 'voc_ml', 'voc_dev'}
    paths = [ROOT / 'workflow01.py', *(ROOT / 'voc_demo').rglob('*.py')]
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                assert not forbidden.intersection(a.name.split('.')[0] for a in node.names), path
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split('.')[0] not in forbidden, path


def test_data_settings_ignore_parent_application(monkeypatch, tmp_path):
    monkeypatch.setenv('VOC_DATA_DIR', str(tmp_path / 'parent-data'))
    monkeypatch.setenv('VOC_STORAGE_CONFIG', str(tmp_path / 'missing.toml'))
    monkeypatch.delenv('DEMO_DATA_DIR', raising=False)
    assert data_root() == ROOT / '.data'
    monkeypatch.setenv('DEMO_DATA_DIR', str(tmp_path / 'demo-data'))
    assert load_settings().runtime_root == tmp_path / 'demo-data'
    assert not (tmp_path / 'demo-data').exists()


@pytest.mark.parametrize('prefix', ['voc/datasets', 'voc/datasets/nested', 'voc', '../unsafe'])
def test_demo_writes_cannot_overlap_dataset_prefix(monkeypatch, prefix):
    monkeypatch.setenv('DEMO_DATASET_PREFIX', 'voc/datasets')
    monkeypatch.setenv('DEMO_S3_PREFIX', prefix)
    with pytest.raises(ValueError):
        load_settings()
