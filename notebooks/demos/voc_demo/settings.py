"""Demo-only settings. Never reads the parent application's config or databases."""
import os
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def data_root():
    return Path(os.environ.get('DEMO_DATA_DIR', str(ROOT / '.data'))).expanduser().resolve()


def model_root():
    return Path(os.environ.get('DEMO_MODEL_DIR', str(data_root() / 'models'))).expanduser().resolve()


def _prefix(value, name):
    value = value.strip('/')
    if not value or any(part in ('.', '..') or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', part) for part in value.split('/')):
        raise ValueError(f'{name} must contain ordinary slash-separated key segments')
    return value


@dataclass(frozen=True)
class DemoSettings:
    runtime_root: Path
    bucket: str
    region: str
    member_id: str = ''
    shared_prefix: str = 'voc/datasets'
    demos_prefix: str = 'voc/demos'
    dataset_source: str = 's3'

    @property
    def shared_dataset_uri(self):
        return f's3://{self.bucket}/{self.shared_prefix}'


def load_settings():
    shared = _prefix(os.environ.get('DEMO_DATASET_PREFIX', 'voc/datasets'), 'DEMO_DATASET_PREFIX')
    demos = _prefix(os.environ.get('DEMO_S3_PREFIX', 'voc/demos'), 'DEMO_S3_PREFIX')
    if any(root == demos or root.startswith(demos + '/') or demos.startswith(root + '/') for root in (shared, 'members')):
        raise ValueError('Dataset and demo prefixes must not overlap')
    return DemoSettings(
        runtime_root=data_root(),
        bucket=os.environ.get('AWS_BUCKET', ''),
        region=os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION', ''),
        member_id=os.environ.get('DEMO_MEMBER_ID', ''),
        shared_prefix=shared,
        demos_prefix=demos,
    )
