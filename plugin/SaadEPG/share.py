# -*- coding: utf-8 -*-
"""Send the satellite's own guide to the cloud (optional, off by default).

What is sent, once a day (after the night scan, or from the menu): for every channel of your bouquets that the
plugin does NOT fill and that has a satellite guide, its programmes of the next 2 days, its service number
(SID:TSID:ONID:namespace) and its name; plus the names of your channels without any guide. Nothing else: no
IP, no settings, no other files.

Where: comments on one issue of the plugin's GitHub repository, with a token YOU create that can only write
issues of that repository (fine-grained token, "Issues: Read and write", one repository). The token stays in
/etc/enigma2/SaadEPG/gh_token (readable by root only) and is sent only to api.github.com over a verified
HTTPS connection. The cloud reads only comments of the repository owner.

What it gives: the cloud keeps those guides (the receiver links them by service number even when the satellite
stops sending them, and the estimated guide learns from them), and every day it looks for sources for the
names of your channels without a guide (public/tr/wanted.txt).
"""
from __future__ import absolute_import
import base64
import gzip
import json
import os
import ssl
import time

from . import engine
from .sources import log, u

TOKEN_FILE = os.path.join(engine.DATA_DIR, 'gh_token')
REPO = 'Saadmelshepiny/ArabEPG'
PART = 60000            # GitHub comments hold 65536 characters
HOURS = 48


def token():
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    except Exception:
        return ''


def set_token(t):
    t = (t or '').strip()
    if not t:
        try:
            os.remove(TOKEN_FILE)
        except Exception:
            pass
        return
    if not (t.startswith('github_pat_') or t.startswith('ghp_')) or len(t) > 300 or any(c.isspace() for c in t):
        raise ValueError(u'هذا ليس توكن GitHub')
    fd = os.open(TOKEN_FILE + '.tmp', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(t)
    os.rename(TOKEN_FILE + '.tmp', TOKEN_FILE)


def collect(epgcache, services, state=None):
    """main thread (epgcache): -> payload dict"""
    state = state or engine.load_state()
    filled = state.get('filled') or {}
    now = int(time.time())
    out, missing, seen = [], [], set()
    for ref, name in services:
        k = engine.ref_key(ref)
        if not k or k in seen:
            continue
        seen.add(k)
        if ref in filled:
            continue
        try:
            res = epgcache.lookupEvent(['BDTSE', (engine._enc(ref), 0, now - 3600, HOURS * 60)]) or []
        except Exception:
            res = []
        evs = []
        for x in res:
            if x and x[0] and x[1] and x[2]:
                evs.append([int(x[0]), int(x[1]), u(x[2]), u(x[3] or u'')[:150], u(x[4] or u'')[:300]])
        if len(evs) >= 4:
            out.append({'k': k, 'n': u(name), 's': engine.sat_of_ref(ref), 'e': evs})
        elif len(missing) < 400:
            missing.append(u(name))
    return {'v': 1, 't': now, 'ch': out, 'missing': missing}


def _post(url, body, tok):
    try:
        from urllib.request import Request, urlopen
    except ImportError:
        from urllib2 import Request, urlopen
    req = Request(url, data=json.dumps(body).encode('utf-8'), headers={
        'Authorization': 'Bearer ' + tok, 'Accept': 'application/vnd.github+json',
        'Content-Type': 'application/json', 'User-Agent': 'SaadEPG-receiver'})
    # verified TLS only: the token is never sent over a connection that could be read by someone else
    return urlopen(req, timeout=60, context=ssl.create_default_context()).read()


def upload(payload, issue, tok=None):
    """any thread: -> (ok, message)"""
    tok = tok or token()
    if not tok:
        return False, u'لا يوجد توكن'
    if not issue:
        return False, u'السحابة لم تنشئ مكان الرفع بعد (حدّث الدليل بعد تشغيل السحابة)'
    raw = base64.b64encode(gzip.compress(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
                           if hasattr(gzip, 'compress') else _gz(payload)).decode('ascii')
    parts = [raw[i:i + PART] for i in range(0, len(raw), PART)] or ['']
    if len(parts) > 20:
        return False, u'البيانات كبيرة جدا (%d جزء)' % len(parts)
    bid = int(time.time())
    url = 'https://api.github.com/repos/%s/issues/%d/comments' % (REPO, int(issue))
    try:
        for i, p in enumerate(parts):
            _post(url, {'body': u'SAADEPG1 %d %d/%d\n%s' % (bid, i + 1, len(parts), p)}, tok)
    except Exception as e:
        msg = str(e)
        if '401' in msg or '403' in msg or '404' in msg:
            msg += u' - التوكن غير صالح أو ليس له صلاحية Issues على المستودع'
        return False, u'فشل الرفع: %s' % msg
    st = engine.load_state()
    st['shared'] = {'t': bid, 'ch': len(payload.get('ch') or []), 'missing': len(payload.get('missing') or []),
                    'kb': len(raw) // 1024}
    engine.save_state(st)
    msg = u'تم رفع دليل القمر لـ %d قناة و %d اسم قناة بدون دليل (%d كيلوبايت)' % (
        len(payload.get('ch') or []), len(payload.get('missing') or []), len(raw) // 1024)
    log(msg)
    return True, msg


def _gz(payload):
    import io
    b = io.BytesIO()
    with gzip.GzipFile(fileobj=b, mode='wb') as f:
        f.write(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    return b.getvalue()


def issue():
    return (engine.load_index().get('crowd') or {}).get('issue')


def due(hours=20):
    return time.time() - ((engine.state_ro().get('shared') or {}).get('t') or 0) > hours * 3600
