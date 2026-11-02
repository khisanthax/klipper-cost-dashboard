"""Bounded, cached, read-only Moonraker observations. Never finalize stored jobs."""
from concurrent.futures import ThreadPoolExecutor
import json
import math
import threading
import time
import urllib.error
import urllib.request

_lock=threading.Lock()
_cache={}
TTL=5


def probe(url):
    try:
        with urllib.request.urlopen(url.rstrip('/')+'/printer/objects/query?print_stats',timeout=1.2) as response:
            stats=json.loads(response.read(65536))['result']['status']['print_stats']
        state=stats['state']
        if state not in ('standby','printing','paused','complete','cancelled','error'):
            raise ValueError('Unknown printer state')
        def metric(key):
            value=float(stats.get(key) or 0)
            if not math.isfinite(value) or value<0:raise ValueError('Invalid printer measurement')
            return value
        return dict(connected=True,state=state,filename=str(stats.get('filename') or ''),
                    print_seconds=metric('print_duration'),total_seconds=metric('total_duration'),
                    filament_mm=metric('filament_used'),observed_at=time.time(),error=None)
    except urllib.error.HTTPError as exc:
        error='Moonraker authentication required' if exc.code in (401,403) else 'Moonraker HTTP error '+str(exc.code)
    except (OSError,ValueError,KeyError,TypeError):
        error='Moonraker unavailable or invalid telemetry; stored tracking is unverified'
    return dict(connected=False,state='disconnected',observed_at=time.time(),error=error)


def statuses(endpoints):
    """Single bounded refresh shared by concurrent tabs; URLs are part of the key."""
    with _lock:
        now=time.monotonic()
        missing={name:url for name,url in endpoints.items() if (name,url) not in _cache or now-_cache[(name,url)][0]>TTL}
        if missing:
            with ThreadPoolExecutor(max_workers=min(4,len(missing))) as pool:
                observations=list(pool.map(probe,missing.values()))
            for (name,url),value in zip(missing.items(),observations):_cache[(name,url)]=(time.monotonic(),value)
        result={name:dict(_cache[(name,url)][1]) for name,url in endpoints.items()}
        for key in list(_cache):
            if key not in {(name,url) for name,url in endpoints.items()}:del _cache[key]
        return result
