import os
import pytest

def pytest_collection_modifyitems(config, items):
    if os.environ.get("VOC_RUN_LIVE_TESTS") != "1":
        skip = pytest.mark.skip(reason="Set VOC_RUN_LIVE_TESTS=1 to opt into live acceptance checks")
        for item in items:
            if item.get_closest_marker("live"):
                item.add_marker(skip)
