from pathlib import Path

import pytest

DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="session")
def data_dir() -> Path:
    if not (DATA / "8_color_data_set").exists():
        pytest.skip("test data missing: run `python scripts/fetch_test_data.py`")
    return DATA
