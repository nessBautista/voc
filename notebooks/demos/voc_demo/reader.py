"""Small notebook API backed only by the demo's verified S3 reader."""
from .datasets.shared_reader import SharedDatasetReader


def get_dataset(*, stage='prepared', source='s3', version='latest'):
    if source != 's3':
        raise ValueError('This standalone demo reads only shared S3 datasets')
    return SharedDatasetReader().read(version, stage=stage)
