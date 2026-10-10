from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("VYOM_DATA_DIR", ROOT / "data")).resolve()
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", 25 * 1024 * 1024))
MAX_FILES_PER_REQUEST = int(os.getenv("MAX_FILES_PER_REQUEST", "10"))
MAX_XLSX_ROWS = int(os.getenv("MAX_XLSX_ROWS", "200000"))
MAX_XLSX_SHEETS = int(os.getenv("MAX_XLSX_SHEETS", "20"))
MAX_UNCOMPRESSED_BYTES = int(os.getenv("MAX_UNCOMPRESSED_BYTES", 200 * 1024 * 1024))
JOB_TIMEOUT_S = int(os.getenv("JOB_TIMEOUT_S", "300"))
DB_PATH = DATA_DIR / "vyom.db"
