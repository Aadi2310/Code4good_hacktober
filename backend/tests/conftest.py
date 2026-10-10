from __future__ import annotations
import hashlib
import pytest
from pathlib import Path
from vyom.models import RecordSource
from vyom.intake import detect
from vyom import config
from vyom.jobs import store
from vyom.api import main as api

@pytest.fixture(autouse=True)
def isolated_data(tmp_path,monkeypatch):
    monkeypatch.setattr(config,"DATA_DIR",tmp_path)
    monkeypatch.setattr(config,"DB_PATH",tmp_path/"vyom.db")
    monkeypatch.setattr(store,"DATA_DIR",tmp_path)
    monkeypatch.setattr(store,"DB_PATH",tmp_path/"vyom.db")
    monkeypatch.setattr(api,"DATA_DIR",tmp_path)

def make_source(path:Path,kind="csv"):
    detected=detect(path)
    return RecordSource(filename=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),detected_type=detected.mime,input_kind=kind)
