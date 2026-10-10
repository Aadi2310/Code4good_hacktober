from dataclasses import dataclass
from pathlib import Path
from ..errors import PipelineError

@dataclass(frozen=True)
class Detected:
    kind: str
    mime: str
    ext_mismatch: bool

def detect(path: Path) -> Detected:
    path = Path(path)
    try:
        with path.open("rb") as f: b = f.read(8192)
    except OSError as e:
        raise PipelineError("CORRUPT_FILE", "Unable to read uploaded file") from e
    if not b: raise PipelineError("EMPTY_FILE", "Uploaded file is empty")
    ext = path.suffix.lower()
    if b.startswith(b"PK\x03\x04"):
        # XLSX is a ZIP package; validate expected internal marker to reject generic ZIPs.
        import zipfile
        try:
            with zipfile.ZipFile(path) as z:
                infos=z.infolist()
                if "xl/workbook.xml" not in z.namelist(): raise ValueError("not xlsx")
                expanded=sum(i.file_size for i in infos)
                if expanded > 100 * 1024 * 1024 or any(i.file_size > 1024 * 1024 and i.file_size / max(i.compress_size,1) > 200 for i in infos):
                    raise PipelineError("FILE_TOO_LARGE","XLSX expands beyond safe processing limits")
                if len(infos)>10000:raise PipelineError("FILE_TOO_LARGE","XLSX contains too many archive entries")
        except PipelineError:raise
        except Exception as e: raise PipelineError("CORRUPT_FILE", "File signature is not a valid XLSX") from e
        kind,mime="xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif b.startswith(b"%PDF-"): kind,mime="pdf","application/pdf"
    elif b.startswith(b"\xff\xd8\xff"): kind,mime="jpeg","image/jpeg"
    elif b.startswith(b"\x89PNG\r\n\x1a\n"): kind,mime="png","image/png"
    elif b.startswith((b"\xd0\xcf\x11\xe0",)): raise PipelineError("UNSUPPORTED_FORMAT", "Legacy XLS is not supported")
    else:
        import csv,io
        try:
            text=b.decode("utf-8-sig")
        except UnicodeDecodeError:
            if b"\x00" in b:
                try:text=b.decode("utf-16")
                except UnicodeDecodeError:text=b.decode("cp1252")
            else:
                try:text=b.decode("cp1252")
                except UnicodeDecodeError:raise PipelineError("UNSUPPORTED_FORMAT","Unsupported or unrecognized file signature")
        try:guessed=csv.Sniffer().sniff(text,delimiters=",;\t|").delimiter
        except csv.Error:guessed=None
        def rows_for(delim):return list(csv.reader(io.StringIO(text),delimiter=delim))
        parsed=rows_for(guessed) if guessed else []
        if sum(len(row)>=2 for row in parsed[:50])<2:
            choices=[]
            for delim in ",;\t|":
                candidate=rows_for(delim);valid=sum(len(row)>=2 for row in candidate[:50]);width=max((len(row) for row in candidate[:50]),default=0);choices.append((valid,width,delim,candidate))
            valid,width,_,parsed=max(choices)
        if sum(len(row)>=2 for row in parsed[:50])<2:
            raise PipelineError("UNSUPPORTED_FORMAT","Text file does not contain a delimited table")
        kind,mime="csv","text/csv"
    expected={".xlsx":"xlsx", ".csv":"csv", ".pdf":"pdf", ".jpg":"jpeg", ".jpeg":"jpeg", ".png":"png"}
    return Detected(kind,mime,ext in expected and expected[ext] != kind)
