"""Permanent content-addressed thumbnail objects and job-scoped SQL references.

Files are written before references commit. Cache eviction never visits this
directory. Recovery requires an explicit token or exact path/metadata evidence;
we never guess by basename or printer address.
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from functools import wraps
from contextlib import contextmanager

from core import db
from core.config import DATA_DIR

ARCHIVE_ROOT = Path(DATA_DIR)/"thumbnails"/"objects"
archive_lock = threading.RLock()
_guard_state=threading.local()
MAX_IMAGE_BYTES = 8*1024*1024
log = logging.getLogger(__name__)


@contextmanager
def archive_guard():
    with archive_lock:
        if getattr(_guard_state,'depth',0):
            yield
            return
        ARCHIVE_ROOT.parent.mkdir(parents=True,exist_ok=True)
        path=ARCHIVE_ROOT.parent/'.archive.lock'
        fd=os.open(path,os.O_RDWR|os.O_CREAT,0o600)
        try:
            if os.name=='nt':
                import msvcrt
                if os.fstat(fd).st_size==0:os.write(fd,b'0')
                os.lseek(fd,0,os.SEEK_SET);msvcrt.locking(fd,msvcrt.LK_LOCK,1)
            else:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_EX)
            _guard_state.depth=1
            yield
        finally:
            _guard_state.depth=0
            if os.name=='nt':
                os.lseek(fd,0,os.SEEK_SET);msvcrt.locking(fd,msvcrt.LK_UNLCK,1)
            else:fcntl.flock(fd,fcntl.LOCK_UN)
            os.close(fd)


def archive_consistent(function):
    @wraps(function)
    def call(*args,**kwargs):
        with archive_guard():return function(*args,**kwargs)
    return call


def _image_type(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'):return 'png','image/png'
    if data.startswith(b'\xff\xd8\xff'):return 'jpg','image/jpeg'
    if data.startswith(b'RIFF') and data[8:12]==b'WEBP':return 'webp','image/webp'
    raise ValueError("Unsupported or invalid thumbnail image")


@archive_consistent
def retain_image(data):
    if not data or len(data)>MAX_IMAGE_BYTES:raise ValueError("Thumbnail exceeds image size limit")
    extension,mime=_image_type(data)
    digest=hashlib.sha256(data).hexdigest()
    with archive_lock:
        ARCHIVE_ROOT.mkdir(parents=True,exist_ok=True)
        destination=ARCHIVE_ROOT/(digest+'.'+extension)
        if not destination.exists():
            fd,tmp=tempfile.mkstemp(prefix='.archive-',dir=ARCHIVE_ROOT)
            try:
                with os.fdopen(fd,'wb') as stream:
                    stream.write(data);stream.flush();os.fsync(stream.fileno())
                os.chmod(tmp,0o600);os.replace(tmp,destination)
            finally:
                if os.path.exists(tmp):os.unlink(tmp)
        with db.connect_db() as conn:
            db.apply_migrations(conn)
            conn.execute('INSERT OR IGNORE INTO thumbnail_assets VALUES (?,?,?,?,?)',(digest,extension,mime,len(data),db._utc_now_iso()))
    return digest


def asset_path(digest):
    if not re.fullmatch(r'[a-f0-9]{64}',str(digest or '')):return None
    with db.connect_db() as conn:
        db.apply_migrations(conn)
        row=conn.execute('SELECT extension,mime_type FROM thumbnail_assets WHERE sha256=?',(digest,)).fetchone()
    if not row:return None
    path=ARCHIVE_ROOT/(digest+'.'+row['extension'])
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==digest:return path,row['mime_type']
    return None


def get_job_asset(job_uid):
    with db.connect_db() as conn:
        db.apply_migrations(conn)
        row=conn.execute('SELECT asset_sha256,status FROM job_thumbnails WHERE job_uid=?',(job_uid,)).fetchone()
    if row and row['asset_sha256'] and asset_path(row['asset_sha256']):return row['asset_sha256']
    return None


def get_job_status(job_uid):
    with db.connect_db() as conn:
        db.apply_migrations(conn)
        row=conn.execute('SELECT * FROM job_thumbnails WHERE job_uid=?',(job_uid,)).fetchone()
    if not row:return {'status':'not_archived','checked_at':None}
    result=dict(row)
    if result['asset_sha256'] and not asset_path(result['asset_sha256']):result['status']='archive_missing'
    return result


@archive_consistent
def _reference(job_uid,asset,status,source_url='',filename='',modified=None,provenance=''):
    with archive_lock, db.connect_db() as conn:
        db.apply_migrations(conn)
        if not conn.execute('SELECT 1 FROM jobs WHERE job_uid=?',(job_uid,)).fetchone():return
        conn.execute('''INSERT INTO job_thumbnails VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(job_uid) DO UPDATE SET asset_sha256=COALESCE(excluded.asset_sha256,job_thumbnails.asset_sha256),
            status=CASE WHEN COALESCE(excluded.asset_sha256,job_thumbnails.asset_sha256) IS NOT NULL THEN 'archived' ELSE excluded.status END,source_url=excluded.source_url,source_filename=excluded.source_filename,
            source_modified=excluded.source_modified,provenance=excluded.provenance,checked_at=excluded.checked_at''',
            (job_uid,asset,status,source_url,filename,modified,provenance,db._utc_now_iso()))


def fetch_image(printer,filename,*,ended_at=None,historical=False):
    from core import thumbnails as cache
    base=cache.resolve_moonraker_base_url(printer)
    if not base:return None,'missing_endpoint',None
    filename=cache._normalize_moonraker_filename(filename)
    if historical and not ended_at:return None,'insufficient_history',None
    def get(path,binary=False):
        with urllib.request.urlopen(base+path,timeout=3) as response:
            if binary:
                data=response.read(MAX_IMAGE_BYTES+1)
                if len(data)>MAX_IMAGE_BYTES:raise ValueError('Image too large')
                return data
            return json.load(response)
    try:
        metadata=get('/server/files/metadata?'+urllib.parse.urlencode({'filename':filename})).get('result',{})
        modified=metadata.get('modified')
        # A reused exact filename can now represent a different model. Do not
        # retroactively attach a replacement's image to an older print.
        if historical and ended_at and modified and float(modified)>float(ended_at)+2:
            return None,'source_changed',modified
        choices=cache._extract_thumbnails({'result':metadata})
        picked=cache._pick_thumbnail(choices,'card')
        if not picked:return None,'no_slicer_thumbnail',modified
        rel=str(picked.get('relative_path') or picked.get('path') or '').lstrip('/')
        if not rel:return None,'no_slicer_thumbnail',modified
        if rel.startswith('gcodes/'):rel=rel[7:]
        # Moonraker metadata paths may be relative to the G-code directory.
        directory=str(PurePosixPath(filename).parent)
        if directory!='.' and not rel.startswith(directory+'/'):
            rel=directory+'/'+rel
        if '..' in PurePosixPath(rel).parts:return None,'invalid_source_path',modified
        data=get('/server/files/gcodes/'+urllib.parse.quote(rel,safe='/'),True)
        return retain_image(data),'archived',modified
    except urllib.error.HTTPError as exc:
        return None,'source_unavailable' if exc.code==404 else 'source_auth_error' if exc.code in (401,403) else 'network_error',None
    except (OSError,TimeoutError,ValueError,KeyError,TypeError):return None,'network_error',None


def capture_job(job_uid,printer,filename,*,live_asset=None,ended_at=None,historical=False):
    existing=get_job_asset(job_uid)
    if existing:return {'asset':existing,'status':'archived'}
    if live_asset and asset_path(live_asset):
        _reference(job_uid,live_asset,'archived',filename=filename,provenance='captured_at_start')
        return {'asset':live_asset,'status':'archived'}
    asset,status,modified=fetch_image(printer,filename,ended_at=ended_at,historical=historical)
    from core.thumbnails import resolve_moonraker_base_url
    _reference(job_uid,asset,status,resolve_moonraker_base_url(printer) or '',filename,modified,'exact_source_path')
    return {'asset':asset,'status':status}


def recover_cached_job(job_uid,printer,filename,token,ended_at=None):
    from core import thumbnails as cache
    if not re.fullmatch(r'[a-f0-9]{40}',str(token or '')):return None
    with db.connect_db() as conn:
        if conn.execute('SELECT COUNT(*) FROM jobs WHERE thumbnail=?',(token,)).fetchone()[0]>1:
            return None
    # An explicit saved job token is evidence; hashes alone cannot establish
    # which historical job a filename-only cache belongs to. Require one image
    # variant per token across old printer directories, never choose first.
    for size in ('card','small'):
        paths=list(Path(cache._CACHE_ROOT).glob('*/'+token+'_'+size+'.png'))
        if not paths:continue
        payloads={hashlib.sha256(p.read_bytes()).hexdigest():p for p in paths}
        if len(payloads)!=1:return None
        path=next(iter(payloads.values()))
        # A cache refreshed after the job may contain a reused filename's image.
        if ended_at and path.stat().st_mtime>float(ended_at)+2:return None
        asset=retain_image(path.read_bytes())
        _reference(job_uid,asset,'archived',filename=filename,provenance='explicit_saved_cache_token')
        return asset
    return None


@archive_consistent
def prune_unreferenced(*,apply=False,min_age_seconds=86400):
    from core import live
    protected={j.get('thumbnail_asset') for j in live._jobs.values()}
    with archive_lock, db.connect_db() as conn:
        db.apply_migrations(conn)
        rows=conn.execute('SELECT sha256,extension FROM thumbnail_assets WHERE sha256 NOT IN (SELECT asset_sha256 FROM job_thumbnails WHERE asset_sha256 IS NOT NULL)').fetchall()
        import time
        candidates=[r for r in rows if r['sha256'] not in protected and (ARCHIVE_ROOT/(r['sha256']+'.'+r['extension'])).exists() and time.time()-(ARCHIVE_ROOT/(r['sha256']+'.'+r['extension'])).stat().st_mtime>=min_age_seconds]
        if apply:
            for row in candidates:
                (ARCHIVE_ROOT/(row['sha256']+'.'+row['extension'])).unlink(missing_ok=True)
                conn.execute('DELETE FROM thumbnail_assets WHERE sha256=?',(row['sha256'],))
    return len(candidates)


def backfill(*,apply=False,offline=False,limit=100,printer=None):
    from concurrent.futures import ThreadPoolExecutor
    from collections import Counter
    if not 1<=int(limit)<=10000:raise ValueError('Thumbnail limit must be between 1 and 10000')
    with db.connect_db() as conn:
        db.apply_migrations(conn)
        rows=conn.execute('''SELECT j.job_uid,p.name AS printer,j.filename,j.thumbnail,j.ended_at
            FROM jobs j JOIN printers p ON p.id=j.printer_id
            LEFT JOIN job_thumbnails t ON t.job_uid=j.job_uid
            WHERE (? IS NULL OR p.name=?) AND t.asset_sha256 IS NULL
            ORDER BY j.ended_at DESC LIMIT ?''',(printer,printer,int(limit))).fetchall()
    if not apply:return {'apply':False,'candidates':len(rows),'limit':limit,'offline':offline}
    def process(row):
        ended=None
        try:ended=datetime.fromisoformat(row['ended_at'].replace('Z','+00:00')).timestamp()
        except (TypeError,ValueError,AttributeError):pass
        if recover_cached_job(row['job_uid'],row['printer'],row['filename'],row['thumbnail'],ended):return 'recovered_cache'
        if offline:return 'no_proven_cache'
        return capture_job(row['job_uid'],row['printer'],row['filename'],ended_at=ended,historical=True)['status']
    with ThreadPoolExecutor(max_workers=4) as pool:
        counts=Counter(pool.map(process,rows))
    return {'apply':True,'processed':len(rows),'results':dict(counts)}
