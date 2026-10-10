"""SQLite WAL job/result repository used by the local worker pool."""
import json,sqlite3,uuid
from datetime import datetime,timezone,timedelta
from ..config import DATA_DIR,DB_PATH

def now():return datetime.now(timezone.utc).isoformat()
def db():
    DATA_DIR.mkdir(parents=True,exist_ok=True);c=sqlite3.connect(DB_PATH,timeout=15);c.row_factory=sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL");c.execute("PRAGMA foreign_keys=ON")
    c.execute("CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,status TEXT NOT NULL,created_at TEXT NOT NULL,filename TEXT NOT NULL,sha256 TEXT NOT NULL,kind TEXT NOT NULL,result TEXT,error TEXT)")
    extra={"batch_id":"TEXT","input_kind":"TEXT","stage":"TEXT NOT NULL DEFAULT 'queued'","progress":"REAL NOT NULL DEFAULT 0","options_json":"TEXT NOT NULL DEFAULT '{}'","error_code":"TEXT","error_message":"TEXT","started_at":"TEXT","finished_at":"TEXT","expires_at":"TEXT","attempt":"INTEGER NOT NULL DEFAULT 0"}
    existing={r["name"] for r in c.execute("PRAGMA table_info(jobs)")}
    for name,decl in extra.items():
        if name not in existing:c.execute(f"ALTER TABLE jobs ADD COLUMN {name} {decl}")
    c.execute("CREATE INDEX IF NOT EXISTS jobs_sha_options ON jobs(sha256,options_json,status)")
    c.execute("CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,ts TEXT NOT NULL,stage TEXT NOT NULL,level TEXT NOT NULL,message TEXT NOT NULL,data_json TEXT NOT NULL DEFAULT '{}',FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE)")
    c.execute("CREATE TABLE IF NOT EXISTS doc_keys(job_id TEXT NOT NULL,doc_index INTEGER NOT NULL,sha256 TEXT NOT NULL,supplier_gstin TEXT,invoice_no_norm TEXT,invoice_date TEXT,total_amount TEXT,supplier_name TEXT,PRIMARY KEY(job_id,doc_index),FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE)")
    c.commit();return c
def create(filename,sha,kind,force=False,batch_id=None,options=None,input_kind=None):
    options_json=json.dumps(options or {},sort_keys=True,separators=(",",":"));c=db()
    found=c.execute("SELECT * FROM jobs WHERE sha256=? AND options_json=? AND status='completed' AND (expires_at IS NULL OR expires_at>?) ORDER BY created_at DESC LIMIT 1",(sha,options_json,now())).fetchone()
    if found and not force:return dict(found),True
    jid=str(uuid.uuid4());created=now();expires=(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat()
    c.execute("INSERT INTO jobs(id,status,created_at,filename,sha256,kind,result,error,batch_id,input_kind,stage,progress,options_json,attempt,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(jid,"queued",created,filename,sha,kind,None,None,batch_id,input_kind or kind,"queued",0,options_json,0,expires))
    c.commit();event(c,jid,"queued","info","Job accepted")
    row=dict(c.execute("SELECT * FROM jobs WHERE id=?",(jid,)).fetchone());c.close();return row,False
def event(connection,jid,stage,level,message,data=None):
    connection.execute("INSERT INTO events(job_id,ts,stage,level,message,data_json) VALUES(?,?,?,?,?,?)",(jid,now(),stage,level,message,json.dumps(data or {},ensure_ascii=False)))
def update(jid,status,result=None,error=None,*,stage=None,progress=None,error_code=None,error_message=None):
    c=db();row=c.execute("SELECT * FROM jobs WHERE id=?",(jid,)).fetchone()
    if not row:c.close();return
    st=stage or status;pr=progress if progress is not None else (1 if status in {"completed","failed"} else row["progress"])
    started=row["started_at"] or (now() if status in {"processing","running"} else None);finished=now() if status in {"completed","failed"} else None
    c.execute("UPDATE jobs SET status=?,result=COALESCE(?,result),error=?,stage=?,progress=?,error_code=?,error_message=?,started_at=COALESCE(started_at,?),finished_at=COALESCE(?,finished_at) WHERE id=?",(status,json.dumps(result,ensure_ascii=False) if result is not None else None,error,st,pr,error_code,error_message,started,finished,jid))
    event(c,jid,st,"error" if status=="failed" else "info",error_message or st);c.commit();c.close()
def get(jid):
    c=db();r=c.execute("SELECT * FROM jobs WHERE id=?",(jid,)).fetchone();c.close();return dict(r) if r else None
def all_jobs(limit=100):
    c=db();rows=[dict(r) for r in c.execute("SELECT id,batch_id,status,created_at,filename,kind,input_kind,stage,progress,error_code,error_message,started_at,finished_at FROM jobs ORDER BY created_at DESC LIMIT ?",(limit,))];c.close();return rows
def find_cached(sha,options):
    opts=json.dumps(options or {},sort_keys=True,separators=(",",":"));c=db();r=c.execute("SELECT * FROM jobs WHERE sha256=? AND options_json=? AND status='completed' AND (expires_at IS NULL OR expires_at>?) ORDER BY created_at DESC LIMIT 1",(sha,opts,now())).fetchone();c.close();return dict(r) if r else None
def candidates(exclude_job_id=None):
    c=db()
    q="SELECT d.*,j.filename FROM doc_keys d JOIN jobs j ON j.id=d.job_id WHERE j.status='completed'"
    args=()
    if exclude_job_id:q+=" AND d.job_id<>?";args=(exclude_job_id,)
    rows=[dict(r) for r in c.execute(q,args)];c.close();return rows
def add_doc_key(job_id,index,sha,*,supplier_gstin=None,invoice_no_norm=None,invoice_date=None,total_amount=None,supplier_name=None):
    c=db();c.execute("INSERT OR REPLACE INTO doc_keys VALUES(?,?,?,?,?,?,?,?)",(job_id,index,sha,supplier_gstin,invoice_no_norm,invoice_date,total_amount,supplier_name));c.commit();c.close()
def result(jid):
    j=get(jid)
    if not j or not j.get("result"):return None
    return json.loads(j["result"])
def delete(jid):
    c=db();c.execute("DELETE FROM jobs WHERE id=?",(jid,));ok=c.total_changes>0;c.commit();c.close();return ok
def event_list(jid):
    c=db();rows=[dict(r) for r in c.execute("SELECT ts,stage,level,message,data_json FROM events WHERE job_id=? ORDER BY id",(jid,))];c.close()
    for r in rows:r["data"]=json.loads(r.pop("data_json"))
    return rows
def queued_count():
    c=db();n=c.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0];c.close();return n
def has_upload(sha):
    c=db();exists=c.execute("SELECT 1 FROM jobs WHERE sha256=? LIMIT 1",(sha,)).fetchone() is not None;c.close();return exists
def queued_jobs():
    c=db();rows=[dict(r) for r in c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at")];c.close();return rows
def reset_for_retry(jid,options=None):
    c=db();opts=json.dumps(options or {},sort_keys=True,separators=(",",":"));c.execute("UPDATE jobs SET status='queued',stage='queued',progress=0,result=NULL,error=NULL,error_code=NULL,error_message=NULL,started_at=NULL,finished_at=NULL,attempt=attempt+1,options_json=? WHERE id=?",(opts,jid));c.execute("DELETE FROM doc_keys WHERE job_id=?",(jid,));event(c,jid,"queued","info","Job requeued");c.commit();c.close()
def recover_running():
    c=db();rows=[dict(r) for r in c.execute("SELECT * FROM jobs WHERE status IN ('processing','running')")]
    for row in rows:
        if row["attempt"]>=2:
            c.execute("UPDATE jobs SET status='failed',stage='failed',progress=1,error='WORKER_CRASH',error_code='WORKER_CRASH',error_message='Job exceeded crash retry limit' WHERE id=?",(row["id"],))
        else:c.execute("UPDATE jobs SET status='queued',stage='queued',progress=0,attempt=attempt+1 WHERE id=?",(row["id"],));event(c,row["id"],"queued","warning","Recovered interrupted job")
    c.commit();c.close();return [r for r in rows if r["attempt"]<2]
def cleanup_expired():
    c=db();ids=[r[0] for r in c.execute("SELECT id FROM jobs WHERE expires_at IS NOT NULL AND expires_at<=?",(now(),))];
    if ids:c.executemany("DELETE FROM jobs WHERE id=?",[(x,) for x in ids])
    c.commit();c.close();return ids
