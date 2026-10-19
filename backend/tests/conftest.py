import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="dc-tests-"))
os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{_TMP / 'app.db'}",
        "DATA_ROOT": str(_TMP / "data"),
        "WORK_DIR": str(_TMP / "work"),
        "SEED_DEMO": "true",
        "SECRET_KEY": "test-secret",
    }
)


@pytest.fixture(scope="session")
def data_root() -> Path:
    from app.demo import generate
    from app.settings import get_settings

    root = get_settings().data_root
    generate(root)
    return root


@pytest.fixture(scope="session")
def demo_connectors(data_root):
    from app.connectors import get_connector

    src = get_connector("sqlite", {"path": "demo/legacy_oracle.db"})
    tgt = get_connector("files", {"base_path": "demo/spark_lake", "format": "parquet"})
    yield src, tgt
    src.close()
    tgt.close()
