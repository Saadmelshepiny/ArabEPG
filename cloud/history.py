#!/usr/bin/env python3
"""Estimated guide: channels whose source stopped (or never had one) still get a schedule when their
programmes repeat - the same weekly grid (news at fixed hours, the Friday film...) or the same daily grid.

  history.json.gz  the real programmes of the last 22 days of every Arabic channel (kept on the epg branch,
                   brought back by publish.sh and extended at every run). Estimates are never added to it.
  guides/est.xml.gz  for channels whose real data stops before the end of the guide: the coming days copied
                   from the most recent same weekday (or yesterday) - only when the past weeks (days) really
                   repeat each other. Channel id = the plugin's key of the channel (elcinema:1289,
                   xml:oe_egypt1/xxx...). Every description starts with EST_MARK so the receiver can show it.
"""
import calendar
import gzip
import html
import json
import os
import re
import time

KEEP_DAYS = 22
EST_MARK = u'[جدول تقديري] '
_PROG = re.compile(r'<programme start="([^"]+)" stop="([^"]+)" channel="([^"]+)"[^>]*>(.*?)</programme>', re.S)
_TITLE = re.compile(r'<title(?![^>]*tr="1")[^>]*>([^<]*)</title>')
_DESC = re.compile(r'<desc(?![^>]*tr="1")[^>]*>([^<]*)</desc>')
_CAT = re.compile(r'<category[^>]*>([^<]*)</category>')


def xtime(s):
    m = re.match(r'(\d{14})\s*([+-]\d{4})?', s or '')
    if not m:
        return None
    ts = calendar.timegm(time.strptime(m.group(1), '%Y%m%d%H%M%S'))
    if m.group(2):
        z = m.group(2)
        ts -= (1 if z[0] == '+' else -1) * (int(z[1:3]) * 3600 + int(z[3:5]) * 60)
    return ts


def fmt(ts):
    return time.strftime('%Y%m%d%H%M%S +0000', time.gmtime(ts))


def plugin_key(fname, kind, cid):
    if kind == 'site' and '.' in cid:
        src, _, i = cid.partition('.')
        return '%s:%s' % (src, i)
    return 'xml:%s/%s' % (fname, cid)


def _norm(t):
    return re.sub(r'[\W_]+', '', (t or '').lower())


def read_file(path):
    """-> {cid: [(start, stop, title, desc, cat)]}"""
    out = {}
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        s = f.read()
    for m in _PROG.finditer(s):
        st, en = xtime(m.group(1)), xtime(m.group(2))
        body = m.group(4)
        t = _TITLE.search(body)
        if not st or not en or en <= st or not t:
            continue
        d = _DESC.search(body)
        c = _CAT.search(body)
        out.setdefault(html.unescape(m.group(3)), []).append((
            st, en, html.unescape(t.group(1)).strip(), html.unescape(d.group(1)).strip()[:300] if d else '',
            html.unescape(c.group(1)).strip() if c else ''))
    return out


def load(path):
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            h = json.load(f)
        if h.get('v') == 1:
            return h
    except Exception:
        pass
    return {'v': 1, 'ch': {}}


def update(hist, gdir, index, now, log=print):
    """the real programmes of this run replace the history in the hours they cover"""
    chans = hist.setdefault('ch', {})
    last_real = {}
    n = 0
    for fname, f in (index.get('files') or {}).items():
        if f.get('region') != 'ar' or f.get('kind') == 'est':
            continue
        path = os.path.join(gdir, fname + '.xml.gz')
        if not os.path.exists(path):
            continue
        try:
            data = read_file(path)
        except Exception as e:
            log('history: %s unreadable (%s)' % (fname, e))
            continue
        listed = set(c for c, _n in f.get('channels') or [])   # channels the cloud removed (copies) stay out
        for cid, evs in data.items():
            if len(evs) < 3 or cid not in listed:
                continue
            key = plugin_key(fname, f.get('kind'), cid)
            evs.sort()
            lo, hi = evs[0][0], evs[-1][1]
            last_real[key] = hi
            c = chans.get(key) or {'t': [], 'e': []}
            titles = c['t']
            tid = dict((t, i) for i, t in enumerate(titles))
            kept = [e for e in c['e'] if e[0] * 60 + e[1] * 60 <= lo or e[0] * 60 >= hi]
            for st, en, t, _d, cat in evs:
                k = (t, cat)          # no descriptions: the history stays small
                ks = json.dumps(k, ensure_ascii=False)
                if ks not in tid:
                    tid[ks] = len(titles)
                    titles.append(ks)
                kept.append([st // 60, (en - st) // 60, tid[ks]])
            limit = (now - KEEP_DAYS * 86400) // 60
            kept = sorted(e for e in kept if e[0] >= limit)
            used = sorted(set(e[2] for e in kept))
            remap = dict((o, i) for i, o in enumerate(used))
            chans[key] = {'t': [titles[o] for o in used], 'e': [[e[0], e[1], remap[e[2]]] for e in kept],
                          'last': hi}
            n += 1
    for key in list(chans):
        if not chans[key]['e'] or chans[key]['e'][-1][0] * 60 < now - KEEP_DAYS * 86400:
            del chans[key]
    hist['generated'] = int(now)
    log('history: %d channels updated, %d kept' % (n, len(chans)))
    return last_real


def _day(evs, day0):
    """events starting in [day0, day0 + 1 day) as {minute of day: (dur, title id)}"""
    out = {}
    lo, hi = day0 // 60, (day0 + 86400) // 60
    for st, dur, ti in evs:
        if lo <= st < hi:
            out[st - lo] = (dur, ti)
    return out


def _sim(a, b):
    """share of programmes of day a found in day b (same title, start within 10 minutes)"""
    if len(a) < 4 or len(b) < 4:
        return 0.0
    bt = {}
    for m, (_d, ti) in b.items():
        bt.setdefault(ti, []).append(m)
    hit = sum(1 for m, (_d, ti) in a.items() if any(abs(m - x) <= 10 for x in bt.get(ti, ())))
    return hit / float(len(a))


def estimate(hist, key, start, end):
    """-> [(start, stop, title, desc, cat)] from start to end, or []"""
    c = hist['ch'].get(key)
    if not c:
        return []
    evs = c['e']
    out = []
    d0 = (start // 86400) * 86400
    while d0 < end:
        best = None
        # weekly: the most recent real same weekday, checked against the week before it
        for k in (1, 2, 3):
            a = _day(evs, d0 - 7 * k * 86400)
            if len(a) >= 4:
                b = _day(evs, d0 - 7 * (k + 1) * 86400)
                s = _sim(a, b) if b else 0.0
                if s >= 0.5:
                    best = (s, a)
                break
        # daily: the most recent real day, checked against the day before it
        for k in range(1, 8):
            a = _day(evs, d0 - k * 86400)
            if len(a) >= 4:
                b = _day(evs, d0 - (k + 1) * 86400)
                s = _sim(a, b) if b else 0.0
                if s >= 0.6 and (not best or s > best[0] + 0.1):
                    best = (s, a)
                break
        if best:
            for m, (dur, ti) in sorted(best[1].items()):
                st = d0 + m * 60
                if start <= st < end:
                    t, cat = json.loads(c['t'][ti])
                    out.append((st, st + dur * 60, t, u'', cat, int(best[0] * 100)))
        d0 += 86400
    return out


def build(gdir, out_dir, index, prev_path, days=3, now=None, log=print):
    now = int(now or time.time())
    hist = load(prev_path)
    last_real = update(hist, gdir, index, now, log)
    with gzip.open(os.path.join(out_dir, 'history.json.gz'), 'wt', encoding='utf-8', compresslevel=9) as f:
        json.dump(hist, f, ensure_ascii=False, separators=(',', ':'))
    end = now + days * 86400 + 6 * 3600       # what a receiver asks for (its days setting) and a little more
    path = os.path.join(gdir, 'est.xml.gz')
    chans, n_ev = [], 0
    with gzip.open(path, 'wt', encoding='utf-8', compresslevel=9) as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="SaadEPG estimate">\n')
        body = []
        for key, c in sorted(hist['ch'].items()):
            real_end = last_real.get(key, c.get('last', 0))
            if real_end >= end - 12 * 3600:
                continue                      # the real guide covers (almost) everything
            evs = estimate(hist, key, max(real_end, now - 3 * 3600), end)
            if len(evs) < 4:
                continue
            chans.append([key, []])
            for st, en, t, d, cat, conf in evs:
                body.append('<programme start="%s" stop="%s" channel="%s"><title lang="ar">%s</title>'
                            '<desc lang="ar">%s</desc>%s</programme>\n' % (
                                fmt(st), fmt(en), html.escape(key), html.escape(t),
                                html.escape(EST_MARK + (u'متوقع حسب تكرار البث (%d%%)' % conf)), ('<category>%s</category>' % html.escape(cat)) if cat else ''))
                n_ev += 1
        for key, _n in chans:
            f.write('<channel id="%s"><display-name>%s</display-name></channel>\n' % (html.escape(key), html.escape(key)))
        f.writelines(body)
        f.write('</tv>\n')
    index['files']['est'] = {'url': 'guides/est.xml.gz', 'sat': ['*'], 'title': 'جدول تقديري', 'kind': 'est',
                             'region': 'ar', 'lang': 'ar', 'size': os.path.getsize(path), 'programmes': n_ev,
                             'channels': chans}
    index['est'] = {'file': 'est', 'mark': EST_MARK}
    log('estimated guide: %d channels without (enough) real data got %d programmes from their repeating grid'
        % (len(chans), n_ev))
    return len(chans)
