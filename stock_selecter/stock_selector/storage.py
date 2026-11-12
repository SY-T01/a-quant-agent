"""Project-local storage, immutable blobs and append-only observed versions."""
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parents[1]

def project_path(value):
    p=Path(value).expanduser()
    return (p if p.is_absolute() else PROJECT/p).resolve()

def now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')

class Store:
    def __init__(self, root=None):
        self.root=project_path(root or 'data')
        self.root.mkdir(parents=True,exist_ok=True)
        self.db=self.root/'warehouse'/'observations.sqlite3'
        self.db.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS snapshots (
              id INTEGER PRIMARY KEY, provider TEXT NOT NULL, endpoint TEXT NOT NULL,
              symbol TEXT NOT NULL, observed_at TEXT NOT NULL, sha256 TEXT NOT NULL,
              relative_path TEXT NOT NULL, UNIQUE(provider,endpoint,symbol,observed_at,sha256));
            CREATE TABLE IF NOT EXISTS records (
              snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
              trade_date TEXT NOT NULL, payload TEXT NOT NULL,
              PRIMARY KEY(snapshot_id,trade_date));
            ''')
    def connect(self):
        db=sqlite3.connect(self.db); db.execute('PRAGMA foreign_keys=ON'); return db
    def save(self, provider, endpoint, symbol, payload, records, observed_at=None):
        stamp=observed_at or now()
        if any(not x.replace('_','').replace('.','').isalnum() for x in (provider,endpoint,symbol)):
            raise ValueError('非法数据路径标识')
        blob=json.dumps(payload,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()
        sha=hashlib.sha256(blob).hexdigest()
        rel=Path('raw')/provider/endpoint/symbol/(sha+'.json')
        dest=self.root/rel; dest.parent.mkdir(parents=True,exist_ok=True)
        if not dest.exists():
            with dest.open('xb') as f: f.write(blob)
        elif dest.read_bytes()!=blob: raise ValueError('原始数据校验冲突')
        with self.connect() as db:
            cursor=db.execute('INSERT OR IGNORE INTO snapshots(provider,endpoint,symbol,observed_at,sha256,relative_path) VALUES (?,?,?,?,?,?)',(provider,endpoint,symbol,stamp,sha,str(rel)))
            sid=db.execute('SELECT id FROM snapshots WHERE provider=? AND endpoint=? AND symbol=? AND observed_at=? AND sha256=?',(provider,endpoint,symbol,stamp,sha)).fetchone()[0]
            if cursor.rowcount:
                for record in records:
                    db.execute('INSERT INTO records VALUES (?,?,?)',(sid,record['date'],json.dumps(record,ensure_ascii=False,allow_nan=False)))
        return dict(snapshot_id=sid,sha256=sha,path=str(dest),observed_at=stamp,records=len(records))
    def status(self):
        with self.connect() as db:
            n=db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]
            r=db.execute('SELECT COUNT(*) FROM records').fetchone()[0]
        return dict(root=str(self.root),database=str(self.db),snapshots=n,records=r,bytes=sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file()),free_bytes=shutil.disk_usage(self.root).free)


def atomic_json(path,value):
    """Replace a complete JSON file on the same volume; preserve old data on failure."""
    import os
    import tempfile
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    payload=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            f.write(payload);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)
