"""P1 platform API: bounded uploads, background jobs, review and exports."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse, FileResponse
from fastapi.exceptions import RequestValidationError
from dataclasses import replace
import asyncio
from pydantic import BaseModel, Field
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,hmac,os,re,time,uuid,shutil,threading,logging
from ..config import DATA_DIR,MAX_UPLOAD_BYTES,MAX_FILES_PER_REQUEST,JOB_TIMEOUT_S
from ..errors import PipelineError
from ..intake import detect
from ..models import RecordSource,ReviewEvent,Alt,InvoiceRecord,Check
from ..tabular import run as tabular_run
from ..jobs import store
from ..dedupe import find as find_duplicates
from ..outputs.export import json_bytes,csv_bytes,xlsx_bytes,exportable
from ..domain import normalize_field
from ..validation import validate

app=FastAPI(title="VYOM+ Backend API",version="1.0.0",openapi_url="/api/v1/openapi.json",docs_url="/docs")
app.add_middleware(CORSMiddleware,allow_origins=os.getenv("CORS_ORIGINS","http://localhost:5173").split(","),allow_credentials=True,allow_methods=["*"],allow_headers=["*"])
_writes={};_executor=ThreadPoolExecutor(max_workers=max(1,int(os.getenv("WORKER_THREADS","2"))),thread_name_prefix="vyom-worker");_maintenance_stop=threading.Event()
_logger=logging.getLogger(__name__)

@asynccontextmanager
async def _lifespan(app: FastAPI):
    # --- startup ---
    _maintenance_stop.clear()
    _retention_loop_once()
    store.recover_running()
    for job in store.queued_jobs():
        path=DATA_DIR/"artifacts"/job["id"]/"source.bin"
        if not path.is_file():
            store.update(job["id"],"failed",error="SOURCE_MISSING",stage="failed",error_code="SOURCE_MISSING",error_message="Stored upload is unavailable")
            continue
        kind=job.get("kind") or job.get("input_kind") or "csv"
        mime={"csv":"text/csv","xlsx":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet","pdf":"application/pdf","jpeg":"image/jpeg","png":"image/png"}.get(kind,"application/octet-stream")
        input_kind=job.get("input_kind") or {"pdf":"pdf_scanned","jpeg":"image_printed","png":"image_printed"}.get(kind,kind)
        source=RecordSource(filename=job["filename"],sha256=job["sha256"],detected_type=mime,input_kind=input_kind)
        _executor.submit(_process,job["id"],path,source,json.loads(job.get("options_json") or "{}"))
    threading.Thread(target=_retention_loop,name="vyom-retention",daemon=True).start()
    yield
    # --- shutdown ---
    _maintenance_stop.set()

app=FastAPI(title="VYOM+ Backend API",version="1.0.0",openapi_url="/api/v1/openapi.json",docs_url="/docs",lifespan=_lifespan)
app.add_middleware(CORSMiddleware,allow_origins=os.getenv("CORS_ORIGINS","http://localhost:5173").split(","),allow_credentials=True,allow_methods=["*"],allow_headers=["*"])

class Edit(BaseModel):path:str;value:str|None
class PatchRequest(BaseModel):edits:list[Edit]=Field(min_length=1);actor:str="reviewer"
class ReviewRequest(BaseModel):action:str;actor:str="reviewer"

def _problem(code,message,details=None,status=400):return JSONResponse(status_code=status,content={"code":code,"message":message,"details":details or {}})
def _safe_filename(name):
    base=Path(name or "upload").name
    cleaned=re.sub(r"[^A-Za-z0-9._ -]","_",base).strip(" .")[:120]
    return cleaned or "upload"
def _options(raw,force):
    if not raw:return {"force":bool(force)}
    try:value=json.loads(raw)
    except json.JSONDecodeError as e:raise PipelineError("INVALID_OPTIONS","options must be valid JSON") from e
    if not isinstance(value,dict):raise PipelineError("INVALID_OPTIONS","options must be a JSON object")
    allowed={"extractor_mode","expect_handwritten","pdf_password","force","timeout_s"};extra=set(value)-allowed
    if extra:raise PipelineError("INVALID_OPTIONS","Unsupported processing option",{"options":sorted(extra)})
    if value.get("extractor_mode","auto") not in {"auto","vlm","rules"}:raise PipelineError("INVALID_OPTIONS","extractor_mode must be auto, vlm or rules")
    if "timeout_s" in value and (not isinstance(value["timeout_s"],int) or value["timeout_s"]<1 or value["timeout_s"]>JOB_TIMEOUT_S):raise PipelineError("INVALID_OPTIONS",f"timeout_s must be between 1 and {JOB_TIMEOUT_S}")
    value["force"]=bool(value.get("force",force));return value
def _safe_options(options):return {k:v for k,v in options.items() if k!="pdf_password"}
def _field(record,name):
    value=record.invoice.get(name)
    return value.value if value else None
def _source_key_value(s):return re.sub(r"[^A-Z0-9]","",str(s or "").upper())
def _process(job_id,source_path,source,options):
    started=time.perf_counter();partial=None;table=None;page_artifacts=[]
    try:
        store.update(job_id,"processing",stage="parsing",progress=.20)
        if source.input_kind in {"csv","xlsx"}:
            result=tabular_run(source_path,source);records=result.records
            table={"table_rows":result.table_rows,"mapping":result.mapping,"unmapped_columns":result.unmapped_columns,"warnings":[w.model_dump(mode="json") for w in result.warnings]}
            partial={"job_id":job_id,"status":"failed","input":source.model_dump(mode="json"),"documents":[r.model_dump(mode="json") for r in records],"tabular":table,"error":{"code":"JOB_TIMEOUT","message":"Partial tabular output retained after timeout"},"artifacts":{"pages":[]},"timings_ms":{"total":int((time.perf_counter()-started)*1000)}}
        else:
            # Keep the optional document dependencies lazy so tabular-only deployments
            # continue to work without installing the P2 requirements.
            try:
                from ..docai import build_bundle
                from ..extraction import run as extraction_run
            except ImportError as exc:
                raise PipelineError("DOC_PROCESSOR_UNAVAILABLE","P2 document dependencies are unavailable; install backend/requirements/p2.txt",{"kind":source.input_kind}) from exc
            artifact=DATA_DIR/"artifacts"/job_id
            store.update(job_id,"processing",stage="extracting",progress=.45)
            kind={"pdf_digital":"pdf","pdf_scanned":"pdf","pdf_mixed":"pdf","image_printed":"jpeg","image_handwritten":"jpeg","image_mixed":"jpeg"}.get(source.input_kind,source.input_kind)
            bundle=build_bundle(source_path,kind,artifact,options)
            # P3's source contract records the classification made from page content.
            document_source=source.model_copy(update={"input_kind":bundle.input_kind})
            store.update(job_id,"processing",stage="validating",progress=.70)
            records=extraction_run(bundle,document_source,options)
            page_artifacts=sorted({Path(name).name for page in bundle.pages for name in (page.original_path,page.enhanced_path) if name})
            if (artifact/"ocr.json").is_file():page_artifacts.append("ocr.json")
        if time.perf_counter()-started>int(options.get("timeout_s",JOB_TIMEOUT_S)):
            raise PipelineError("JOB_TIMEOUT","Processing exceeded the configured job deadline")
        store.update(job_id,"processing",stage="dedupe",progress=.80)
        prior=[]
        for index,rec in enumerate(records):
            rec.duplicates=find_duplicates(rec,store,exclude_job_id=job_id,other_records=prior)
            if rec.duplicates:
                rec.review.required=True
                rec.validation.checks.append(Check(code="POSSIBLE_DUPLICATE",severity="warning",passed=False,field_paths=["invoice.invoice_no"],message="Possible duplicate invoice; human confirmation is required"))
                validate(rec,repair=False)
            invno=_source_key_value(_field(rec,"invoice_no"))
            gst=_source_key_value(_field(rec,"supplier_gstin"))
            total=_field(rec,"total_amount")
            store.add_doc_key(job_id,index,source.sha256,supplier_gstin=gst or None,invoice_no_norm=invno or None,invoice_date=_field(rec,"invoice_date"),total_amount=total,supplier_name=_field(rec,"supplier_name"))
            prior.append((job_id,index,rec))
        duration=int((time.perf_counter()-started)*1000)
        mismatch=[Check(code="FORMAT_MISMATCH",severity="info",passed=True,message="File extension did not match detected content; content type was used") .model_dump(mode="json")] if options.get("_format_mismatch") else []
        envelope={"job_id":job_id,"status":"completed","input":source.model_dump(mode="json"),"documents":[r.model_dump(mode="json") for r in records],"tabular":table,"error":None,"artifacts":{"pages":[f"/api/v1/jobs/{job_id}/artifacts/{name}" for name in page_artifacts]},"timings_ms":{"total":duration},"warnings":mismatch}
        artifact=DATA_DIR/"artifacts"/job_id;artifact.mkdir(parents=True,exist_ok=True)
        (artifact/"result.json").write_text(json.dumps(envelope,ensure_ascii=False,indent=2),encoding="utf-8")
        store.update(job_id,"completed",envelope,stage="done",progress=1)
    except PipelineError as e:
        if partial and e.code=="JOB_TIMEOUT":
            partial["error"]={"code":e.code,"message":e.message};artifact=DATA_DIR/"artifacts"/job_id;artifact.mkdir(parents=True,exist_ok=True)
            (artifact/"result.json").write_text(json.dumps(partial,ensure_ascii=False,indent=2),encoding="utf-8")
            store.update(job_id,"failed",partial,error=e.code,stage="failed",progress=1,error_code=e.code,error_message=e.message)
        else:store.update(job_id,"failed",error=e.code,stage="failed",progress=1,error_code=e.code,error_message=e.message)
    except Exception:
        _logger.exception("Invoice processing failed for job %s",job_id)
        store.update(job_id,"failed",error="INTERNAL_ERROR",stage="failed",progress=1,error_code="INTERNAL_ERROR",error_message="Job processing failed")

def _queue(path,filename,sha,detected,batch,options,existing_id=None):
    safe_opts=_safe_options(options);jid=existing_id
    input_kind={"pdf":"pdf_scanned","jpeg":"image_printed","png":"image_printed"}.get(detected.kind,detected.kind)
    source=RecordSource(filename=filename,sha256=sha,detected_type=detected.mime,input_kind=input_kind)
    if jid is None:
        cached=store.find_cached(sha,safe_opts) if not options.get("force") else None
        if cached:return {"job_id":cached["id"],"filename":filename,"status":cached["status"],"cached":True}
        job,cached_flag=store.create(filename,sha,detected.kind,force=bool(options.get("force")),batch_id=batch,options=safe_opts,input_kind=detected.kind)
        if cached_flag:return {"job_id":job["id"],"filename":filename,"status":job["status"],"cached":True}
        jid=job["id"]
    artifact=DATA_DIR/"artifacts"/jid;artifact.mkdir(parents=True,exist_ok=True)
    source_copy=artifact/"source.bin"
    if path.resolve()!=source_copy.resolve():shutil.copyfile(path,source_copy)
    store.update(jid,"queued",stage="queued",progress=0,error=None,error_code=None,error_message=None)
    run_options=dict(options);run_options["_format_mismatch"]=detected.ext_mismatch
    _executor.submit(_process,jid,source_copy,source,run_options)
    return {"job_id":jid,"filename":filename,"status":"queued","cached":False}

def _retention_loop():
    while not _maintenance_stop.wait(3600):
        for jid in store.cleanup_expired():
            folder=DATA_DIR/"artifacts"/jid
            if folder.exists():shutil.rmtree(folder,ignore_errors=True)


def _retention_loop_once():
    for jid in store.cleanup_expired():
        folder=DATA_DIR/"artifacts"/jid
        if folder.exists():shutil.rmtree(folder,ignore_errors=True)

@app.middleware("http")
async def protect(request,call_next):
    if request.method in {"POST","PATCH","PUT","DELETE"}:
        key=request.client.host if request.client else "unknown";now=time.time();arr=[t for t in _writes.get(key,[]) if now-t<60]
        if len(arr)>=60:return _problem("RATE_LIMITED","Too many write requests",status=429)
        arr.append(now);_writes[key]=arr
    api_key=os.getenv("API_KEY")
    public={"/api/v1/health","/health","/docs","/api/v1/openapi.json","/openapi.json"}
    if api_key and request.url.path not in public and not hmac.compare_digest(api_key,request.headers.get("X-API-Key","")):
        return _problem("UNAUTHORIZED","Invalid API key",status=401)
    try:response=await call_next(request)
    except PipelineError as e:
        status=413 if e.code in {"FILE_TOO_LARGE","ARCHIVE_TOO_LARGE","TOO_MANY_FILES","TOO_MANY_ROWS","TOO_MANY_SHEETS"} else 415 if e.code=="UNSUPPORTED_FORMAT" else 422 if e.code.startswith("INVALID") else 400
        return _problem(e.code,e.message,e.details,status)
    response.headers["X-Content-Type-Options"]="nosniff";response.headers["Referrer-Policy"]="no-referrer";return response

@app.exception_handler(HTTPException)
async def http_problem(request,exc):
    detail=exc.detail
    if isinstance(detail,dict) and {"code","message","details"}.issubset(detail):return JSONResponse(status_code=exc.status_code,content=detail,headers=exc.headers)
    return _problem("HTTP_ERROR",str(detail),status=exc.status_code)
@app.exception_handler(PipelineError)
async def pipeline_problem(request,exc):
    status=413 if exc.code in {"FILE_TOO_LARGE","ARCHIVE_TOO_LARGE","TOO_MANY_FILES","TOO_MANY_ROWS","TOO_MANY_SHEETS","IMAGE_TOO_LARGE"} else 415 if exc.code=="UNSUPPORTED_FORMAT" else 422 if exc.code.startswith("INVALID") else 429 if exc.code=="QUEUE_FULL" else 400
    return _problem(exc.code,exc.message,exc.details,status)
@app.exception_handler(RequestValidationError)
async def request_problem(request,exc):
    issues=[{"location":[str(x) for x in e.get("loc",[])],"message":e.get("msg","invalid value"),"type":e.get("type")} for e in exc.errors()]
    return _problem("INVALID_REQUEST","Request validation failed",{"issues":issues},422)
@app.exception_handler(Exception)
async def unexpected_problem(request,exc):return _problem("INTERNAL_ERROR","The request could not be completed",status=500)

@app.get("/api/v1/health")
@app.get("/health")
def health():return {"status":"ok"}
@app.get("/api/v1/ready")
@app.get("/ready")
def ready():
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    states={"ocr":"unavailable","trocr":"unavailable","vlm":"degraded","db":"available"}
    for module_name,key in (("vyom.ocr","ocr"),("vyom.handwriting","trocr"),("vyom.extraction.vlm","vlm")):
        try:
            module=__import__(module_name,fromlist=["available"]);probe=getattr(module,"available",None)
            if callable(probe):states[key]="available" if probe() else "unavailable"
        except Exception:pass
    try:store.db().close()
    except Exception:states["db"]="unavailable"
    return states
@app.post("/api/v1/validate")
@app.post("/validate")
def validate_record(body:InvoiceRecord):return validate(body,repair=False).model_dump(mode="json")

@app.post("/api/v1/documents",status_code=202)
@app.post("/documents",status_code=202)
async def upload(request:Request,files:list[UploadFile]|None=File(None),file:UploadFile|None=File(None),options:str|None=Form(None),force:bool=Form(False),wait:bool=False,timeout:int=60):
    received=list(files or [])
    if file:received.append(file)
    # Support older callers sending the repeated "file" field without using a nested list.
    if not received:
        form=await request.form();received=[v for key,v in form.multi_items() if key in {"file","files"} and isinstance(v,UploadFile)]
    if not received:raise PipelineError("NO_FILE","Provide at least one file")
    if len(received)>MAX_FILES_PER_REQUEST:raise PipelineError("TOO_MANY_FILES",f"At most {MAX_FILES_PER_REQUEST} files are accepted")
    if timeout<1 or timeout>300:raise PipelineError("INVALID_TIMEOUT","timeout must be between 1 and 300 seconds")
    opts=_options(options,force);batch_id=str(uuid.uuid4());jobs=[]
    if store.queued_count()+len(received)>50:raise PipelineError("QUEUE_FULL","Processing queue is full")
    upload_dir=DATA_DIR/"uploads";upload_dir.mkdir(parents=True,exist_ok=True)
    for upload_file in received:
        name=_safe_filename(upload_file.filename or "upload");temp=upload_dir/f".partial-{uuid.uuid4().hex}"
        digest=hashlib.sha256();size=0
        try:
            with temp.open("wb") as out:
                while chunk:=await upload_file.read(1024*1024):
                    size+=len(chunk)
                    if size>MAX_UPLOAD_BYTES:raise PipelineError("FILE_TOO_LARGE",f"{name} exceeds the upload limit",{"max_bytes":MAX_UPLOAD_BYTES})
                    digest.update(chunk);out.write(chunk)
            detected=detect(temp);sha=digest.hexdigest()
            # Re-run extension comparison against the original sanitized name; content remains authoritative.
            expected={"csv":{".csv",".tsv",".txt"},"xlsx":{".xlsx"},"pdf":{".pdf"},"jpeg":{".jpg",".jpeg"},"png":{".png"}}
            detected=replace(detected,ext_mismatch=bool(Path(name).suffix and Path(name).suffix.lower() not in expected.get(detected.kind,set())))
            stored=upload_dir/sha
            if not stored.exists():temp.replace(stored)
            else:temp.unlink(missing_ok=True)
            mismatch_warning=detected.ext_mismatch
            job_info=_queue(stored,name,sha,detected,batch_id,opts)
            if mismatch_warning:job_info["format_mismatch"]=True
            jobs.append(job_info)
        finally:
            temp.unlink(missing_ok=True)
            await upload_file.close()
        status_code=202
        if wait:
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                states=[store.get(j["job_id"]) for j in jobs]
                if all(x and x["status"] in {"completed","failed"} for x in states):status_code=200;break
                await asyncio.sleep(.1)
            for item in jobs:
                current=store.get(item["job_id"])
                if current:item["status"]=current["status"]
        return JSONResponse(status_code=status_code,content={"batch_id":batch_id,"jobs":jobs})

@app.get("/api/v1/jobs")
@app.get("/jobs")
def list_jobs():return {"jobs":store.all_jobs()}
@app.get("/api/v1/jobs/{job_id}")
@app.get("/jobs/{job_id}")
def job_detail(job_id):
    job=store.get(job_id)
    if not job:raise HTTPException(404,detail={"code":"JOB_NOT_FOUND","message":"Job not found","details":{}})
    envelope=store.result(job_id);conf=sum((d.get("quality",{}).get("overall_confidence",0) for d in (envelope or {}).get("documents",[])),0)/max(1,len((envelope or {}).get("documents",[])))
    return {k:job.get(k) for k in ("id","batch_id","filename","input_kind","status","stage","progress","error_code","error_message","created_at","started_at","finished_at","attempt")} | {"overall_confidence":conf,"duration_ms":None}
@app.get("/api/v1/jobs/{job_id}/result")
@app.get("/jobs/{job_id}/result")
def get_result(job_id):
    job=store.get(job_id)
    if not job:raise HTTPException(404,detail={"code":"JOB_NOT_FOUND","message":"Job not found","details":{}})
    result=store.result(job_id)
    if result is None:raise HTTPException(409,detail={"code":"NOT_READY","message":"Job result is not ready","details":{"status":job["status"]}})
    return result
@app.get("/jobs/mock/result")
def mock_result():
    fixture=Path(__file__).resolve().parents[2]/"tests"/"fixtures"/"mock_result.json"
    if fixture.exists():return json.loads(fixture.read_text(encoding="utf-8"))
    return {"job_id":"mock","status":"completed","input":None,"documents":[],"tabular":None,"error":None,"artifacts":{"pages":[]},"timings_ms":{}}

@app.get("/api/v1/jobs/{job_id}/documents/{index}")
def document(job_id,index:int):
    result=store.result(job_id)
    if result is None:raise HTTPException(409,detail={"code":"NOT_READY","message":"Job result is not ready","details":{}})
    try:
        if index<0:raise IndexError
        return result["documents"][index]
    except IndexError:raise HTTPException(404,detail={"code":"DOCUMENT_NOT_FOUND","message":"Document index is out of range","details":{}})
@app.patch("/api/v1/jobs/{job_id}/documents/{index}")
@app.patch("/jobs/{job_id}/documents/{index}")
async def patch_doc(job_id,index:int,body:PatchRequest):
    job=store.get(job_id);result=store.result(job_id)
    if not job:raise HTTPException(404,detail={"code":"JOB_NOT_FOUND","message":"Job not found","details":{}})
    if result is None:raise HTTPException(409,detail={"code":"NOT_READY","message":"Job result is not ready","details":{}})
    try:rec=InvoiceRecord.model_validate(result["documents"][index])
    except IndexError:raise HTTPException(404,detail={"code":"DOCUMENT_NOT_FOUND","message":"Document index is out of range","details":{}})
    for edit in body.edits:
        match=re.fullmatch(r"invoice\.([a-z_]+)|line_items\[(\d+)\]\.([a-z_]+)",edit.path)
        if not match:raise HTTPException(422,detail={"code":"INVALID_PATCH","message":"Invalid field path","details":{"path":edit.path}})
        field=match.group(1) or match.group(3);target=rec.invoice if match.group(1) else (rec.line_items[int(match.group(2))] if int(match.group(2))<len(rec.line_items) else None)
        if target is None or field not in target:raise HTTPException(422,detail={"code":"INVALID_PATCH","message":"Unknown field path","details":{"path":edit.path}})
        value,issues=normalize_field(field,edit.value)
        if issues and edit.value is not None:raise HTTPException(422,detail={"code":"INVALID_PATCH","message":"Value could not be normalized","details":{"path":edit.path,"issues":issues}})
        fv=target[field];old=fv.value
        if old is not None:fv.alternatives=[Alt(value=old,confidence=fv.confidence,source=(fv.sources[0] if fv.sources else "unknown"))]+fv.alternatives[:2]
        fv.value=value;fv.raw=edit.value;fv.status="human_verified";fv.confidence=1.
        if "human" not in fv.sources:fv.sources.append("human")
        rec.review.history.append(ReviewEvent(ts=datetime.now(timezone.utc).isoformat(),actor=body.actor,action="edit",field_path=edit.path,old=old,new=value))
    validate(rec,repair=False);result["documents"][index]=rec.model_dump(mode="json")
    store.add_doc_key(job_id,index,rec.source.sha256,supplier_gstin=_source_key_value(_field(rec,"supplier_gstin")) or None,invoice_no_norm=_source_key_value(_field(rec,"invoice_no")) or None,invoice_date=_field(rec,"invoice_date"),total_amount=_field(rec,"total_amount"),supplier_name=_field(rec,"supplier_name"))
    _save_result(job_id,result);return result["documents"][index]
@app.post("/api/v1/jobs/{job_id}/documents/{index}/review")
@app.post("/jobs/{job_id}/documents/{index}/review")
async def review(job_id,index:int,body:ReviewRequest):
    result=store.result(job_id);job=store.get(job_id)
    if not job:raise HTTPException(404,detail={"code":"JOB_NOT_FOUND","message":"Job not found","details":{}})
    if result is None:raise HTTPException(409,detail={"code":"NOT_READY","message":"Job result is not ready","details":{}})
    try:rec=InvoiceRecord.model_validate(result["documents"][index])
    except IndexError:raise HTTPException(404,detail={"code":"DOCUMENT_NOT_FOUND","message":"Document index is out of range","details":{}})
    states={"approve":"approved","reject":"rejected","reopen":"pending"}
    if body.action not in states:raise HTTPException(422,detail={"code":"INVALID_REVIEW_ACTION","message":"Action must be approve, reject or reopen","details":{}})
    rec.review.state=states[body.action];rec.review.history.append(ReviewEvent(ts=datetime.now(timezone.utc).isoformat(),actor=body.actor,action=body.action));result["documents"][index]=rec.model_dump(mode="json");_save_result(job_id,result);return result["documents"][index]
def _save_result(job_id,result):
    store.update(job_id,"completed",result,stage="done",progress=1)
    path=DATA_DIR/"artifacts"/job_id/"result.json";path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")

@app.get("/api/v1/jobs/{job_id}/export.{fmt}")
@app.get("/jobs/{job_id}/export.{fmt}")
def export(job_id,fmt:str,scope:str="exportable"):
    if scope not in {"exportable","all"}:raise HTTPException(422,detail={"code":"INVALID_EXPORT_SCOPE","message":"scope must be exportable or all","details":{}})
    result=store.result(job_id)
    if result is None:
        if not store.get(job_id):raise HTTPException(404,detail={"code":"JOB_NOT_FOUND","message":"Job not found","details":{}})
        raise HTTPException(409,detail={"code":"NOT_READY","message":"Job result is not ready","details":{}})
    recs=[InvoiceRecord.model_validate(x) for x in result["documents"]];recs=exportable(recs,scope)
    if fmt=="json":data=json_bytes(recs);mime="application/json";name="invoices.json"
    elif fmt=="csv":data=csv_bytes(recs);mime="text/csv; charset=utf-8";name="line_items.csv"
    elif fmt=="xlsx":data=xlsx_bytes(recs);mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";name="invoices.xlsx"
    else:raise HTTPException(404,detail={"code":"UNSUPPORTED_EXPORT","message":"Supported formats: json, csv, xlsx","details":{}})
    return Response(data,media_type=mime,headers={"Content-Disposition":f'attachment; filename="{name}"'})
@app.get("/api/v1/jobs/{job_id}/artifacts/{name}")
@app.get("/jobs/{job_id}/artifacts/{name}")
def artifact(job_id,name:str):
    if not re.fullmatch(r"[A-Za-z0-9._-]+",name):raise HTTPException(400,detail={"code":"INVALID_ARTIFACT_NAME","message":"Invalid artifact name","details":{}})
    path=DATA_DIR/"artifacts"/job_id/name
    if not path.is_file():raise HTTPException(404,detail={"code":"ARTIFACT_NOT_FOUND","message":"Artifact not found","details":{}})
    return FileResponse(path,filename=name)
@app.post("/api/v1/jobs/{job_id}/retry")
@app.post("/jobs/{job_id}/retry")
async def retry(job_id,overrides:dict|None=None):
    job=store.get(job_id)
    if not job:raise HTTPException(404,detail={"code":"JOB_NOT_FOUND","message":"Job not found","details":{}})
    if job["status"] not in {"failed","completed"}:raise HTTPException(409,detail={"code":"JOB_NOT_RETRYABLE","message":"Only completed or failed jobs can be retried","details":{"status":job["status"]}})
    source_path=DATA_DIR/"artifacts"/job_id/"source.bin"
    if not source_path.is_file():raise HTTPException(410,detail={"code":"SOURCE_EXPIRED","message":"Original upload is unavailable","details":{}})
    from ..intake import Detected
    detected=Detected(kind=job["kind"],mime={"csv":"text/csv","xlsx":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet","pdf":"application/pdf","jpeg":"image/jpeg","png":"image/png"}.get(job["kind"],"application/octet-stream"),ext_mismatch=False)
    options=json.loads(job.get("options_json") or "{}");options.update(overrides or {});options=_options(json.dumps(options),False)
    store.reset_for_retry(job_id,_safe_options(options))
    _executor.submit(_process,job_id,source_path,RecordSource(filename=job["filename"],sha256=job["sha256"],detected_type=detected.mime,input_kind=detected.kind),options)
    return JSONResponse(status_code=202,content={"job_id":job_id,"status":"queued"})
@app.delete("/api/v1/jobs/{job_id}")
@app.delete("/jobs/{job_id}")
def delete_job(job_id):
    job=store.get(job_id)
    if not job:raise HTTPException(404,detail={"code":"JOB_NOT_FOUND","message":"Job not found","details":{}})
    store.delete(job_id)
    artifact_dir=DATA_DIR/"artifacts"/job_id
    if artifact_dir.exists():shutil.rmtree(artifact_dir)
    if job.get("sha256") and not store.has_upload(job["sha256"]):
        uploaded=DATA_DIR/"uploads"/job["sha256"]
        if uploaded.exists():uploaded.unlink()
    return Response(status_code=204)
@app.get("/api/v1/samples")
@app.get("/samples")
def samples():
    root=Path(__file__).resolve().parents[3]/"samples"
    if not root.is_dir():return {"samples":[]}
    return {"samples":[{"name":p.name,"size":p.stat().st_size} for p in sorted(root.iterdir()) if p.is_file() and p.suffix.lower() in {".csv",".xlsx",".pdf",".jpg",".jpeg",".png"}]}
@app.post("/api/v1/samples/{name}/process",status_code=202)
@app.post("/samples/{name}/process",status_code=202)
async def process_sample(name:str,force:bool=False):
    if not re.fullmatch(r"[A-Za-z0-9._ -]{1,120}",name):raise HTTPException(400,detail={"code":"INVALID_SAMPLE_NAME","message":"Invalid sample name","details":{}})
    root=(Path(__file__).resolve().parents[3]/"samples").resolve();path=(root/name).resolve()
    if path.parent!=root or not path.is_file():raise HTTPException(404,detail={"code":"SAMPLE_NOT_FOUND","message":"Sample not found","details":{}})
    detected=detect(path);sha=hashlib.sha256(path.read_bytes()).hexdigest();batch=str(uuid.uuid4());safe=_safe_filename(name);stored=DATA_DIR/"uploads"/sha;stored.parent.mkdir(parents=True,exist_ok=True)
    if not stored.exists():shutil.copyfile(path,stored)
    item=_queue(stored,safe,sha,detected,batch,{"force":force});return JSONResponse(status_code=202,content={"batch_id":batch,"jobs":[item]})
