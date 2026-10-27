#!/usr/bin/env python3
"""One small file per channel: public/ch/<file>/<hash>.json.gz

A receiver needs a few hundred channels out of ~70 MB of guides. With these files it downloads only its own
channels (a few KB each, side by side) instead of reading every big European file on its slow processor.
The events are already parsed by the plugin's own reader (sources.parse_xmltv_file), in both languages when
the file has the cloud's English copy, so the receiver only does json.loads.

  {"c": channel id, "e": [[start, duration, title, short, desc, genre], ...],
   "en": {"<i>": [title, desc]}}            # English copy of event i (only where it differs)
"""
import gzip
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'plugin'))
from SaadEPG import sources  # noqa: E402


def chan_hash(cid):
    return hashlib.sha1(cid.encode('utf-8')).hexdigest()[:16]


def build(gdir, out_dir, index, days=3, log=print):
    t0 = time.time()
    n_files = n_ch = size = 0
    for fname, f in sorted((index.get('files') or {}).items()):
        path = os.path.join(gdir, fname + '.xml.gz')
        if not os.path.exists(path):
            continue
        keep = set(c for c, _n in f.get('channels') or [])
        try:
            sources.XML_PREFER_EN = False
            orig, _names = sources.parse_xmltv_file(path, keep, days + 1)
            sources.XML_PREFER_EN = True
            en, _names = sources.parse_xmltv_file(path, keep, days + 1)
        except Exception as e:
            log('chunks: %s unreadable (%s)' % (fname, e))
            continue
        finally:
            sources.XML_PREFER_EN = False
        d = os.path.join(out_dir, 'ch', fname)
        os.makedirs(d, exist_ok=True)
        done = 0
        for cid, evs in orig.items():
            if not evs:
                continue
            obj = {'c': cid, 'e': [list(e) for e in evs]}
            alt = dict((e[0], e) for e in en.get(cid) or [])
            tr = {}
            for i, e in enumerate(evs):
                x = alt.get(e[0])
                if x and (x[2] != e[2] or x[4] != e[4]):
                    tr[str(i)] = [x[2], x[4]]
            if tr:
                obj['en'] = tr
            p = os.path.join(d, chan_hash(cid) + '.json.gz')
            with gzip.open(p, 'wb', compresslevel=9) as fh:
                fh.write(json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
            size += os.path.getsize(p)
            done += 1
        if done:
            f['ch'] = 1
            n_files += 1
            n_ch += done
    index['chunks'] = {'v': 1, 'path': 'ch/%s/%s.json.gz', 'hash': 'sha1:16'}
    log('per-channel files: %d channels from %d guides, %d MB, %ds' % (n_ch, n_files, size // 1048576,
                                                                      time.time() - t0))
    return n_ch
