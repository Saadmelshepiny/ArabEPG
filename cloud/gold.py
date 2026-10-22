#!/usr/bin/env python3
"""The golden guide: one schedule per Arabic channel, made from every source that has it.

The same channel is often in several files (MBC 1: Shahid, OSN, elCinema, open-epg Egypt, epgshare UAE...).
They are recognised by their names (the plugin's own matcher) AND confirmed by their schedules
(programmes starting at the same minutes), then merged:
  - times and titles: the best member (official Arabic site, Arabic titles, longest coverage)
  - holes in its schedule: filled from the other members
  - missing / short descriptions: taken from the same programme in another member
  - English title: the Arabic title of the same programme in another member when there is one
Result: guides/gold.xml.gz + index['gold'] = {'members': {plugin key: gold channel id}}: the plugin
uses the golden channel instead of any of its members.

Also made from the same data (extras.py): matches.json (sports centre) and films.json (cinema tonight).
"""
import gzip
import html
import importlib.util
import os
import re
import time
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
_spec = importlib.util.spec_from_file_location('saad_matcher', os.path.join(ROOT, 'plugin', 'SaadEPG', 'matcher.py'))
matcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(matcher)

AR = re.compile('[ء-ي]')
OFFICIAL = ('ar_shahid', 'ar_osn', 'ar_rotana', 'ar_bein', 'ar_beinsports', 'ar_aljazeera', 'ar_roya', 'ar_art',
            'ar_ayn', 'ar_snrt', 'ar_lbci', 'ar_tunisia', 'ar_trt')
WINDOW_BACK = 6 * 3600


def xtime(s):
    m = re.match(r'(\d{14})\s*([+-]\d{4})?', s or '')
    if not m:
        return None
    import calendar
    ts = calendar.timegm(time.strptime(m.group(1), '%Y%m%d%H%M%S'))
    if m.group(2):
        z = m.group(2)
        ts -= (1 if z[0] == '+' else -1) * (int(z[1:3]) * 3600 + int(z[3:5]) * 60)
    return ts


def fmt(ts):
    return time.strftime('%Y%m%d%H%M%S +0000', time.gmtime(ts))


def _first(el, tag):
    """the original text (not the cloud's English copy tr="1")"""
    items = [x for x in el.findall(tag) if x.text and x.text.strip()]
    orig = [x for x in items if x.get('tr') != '1']
    return (orig or items or [None])[0]


def load_file(path):
    """-> {cid: {'names': [...], 'ev': [event dict sorted by start]}}"""
    out = {}
    for _e, el in ET.iterparse(gzip.open(path, 'rb'), events=('end',)):
        if el.tag == 'channel':
            out.setdefault(el.get('id'), {'names': [], 'ev': []})['names'] = [
                d.text.strip() for d in el.findall('display-name') if d.text and d.text.strip()]
            el.clear()
        elif el.tag == 'programme':
            st, en = xtime(el.get('start')), xtime(el.get('stop'))
            t = _first(el, 'title')
            if st and en and en > st and t is not None:
                e = {'start': st, 'stop': en, 'title': t.text.strip()}
                for tag, key in (('desc', 'desc'), ('sub-title', 'sub'), ('category', 'cat'), ('episode-num', 'ep')):
                    x = _first(el, tag)
                    if x is not None:
                        e[key] = x.text.strip()
                ic = el.find('icon')
                if ic is not None and ic.get('src'):
                    e['icon'] = ic.get('src')
                out.setdefault(el.get('channel'), {'names': [], 'ev': []})['ev'].append(e)
            el.clear()
    for c in out.values():
        c['ev'].sort(key=lambda e: e['start'])
    return out


def load_arabic(gdir, index):
    """all Arabic-region guides -> {fname: {cid: channel}}"""
    data = {}
    for fname, f in (index.get('files') or {}).items():
        if f.get('region') != 'ar' or fname == 'gold':
            continue
        p = os.path.join(gdir, fname + '.xml.gz')
        if os.path.exists(p):
            try:
                keep = set(c for c, _n in f.get('channels') or [])     # copied schedules were taken out
                data[fname] = dict((c, v) for c, v in load_file(p).items() if c in keep)
            except Exception as e:
                print('gold: %s unreadable (%s)' % (fname, e))
    return data


def plugin_key(fname, kind, cid):
    if kind == 'site' and '.' in cid:
        src, _, i = cid.partition('.')
        return '%s:%s' % (src, i)
    return 'xml:%s/%s' % (fname, cid)


def name_keys(name):
    n = matcher.norm(name)
    keys = set()
    if len(n) >= 3:
        keys.add('n:' + n)
    lat = matcher.latin(name)
    sk = matcher.skeleton(lat) + matcher.digits(lat)
    if len(sk) >= 4:
        keys.add('s:' + sk)
    return keys


def ar_ratio(evs):
    t = [e['title'] for e in evs]
    return sum(1 for x in t if AR.search(x)) / float(len(t)) if t else 0.0


def _starts(evs, lo, hi):
    return [e['start'] for e in evs if lo <= e['start'] < hi]


def _tnorm(t):
    return re.sub('[^0-9a-zء-ي]', '', (t or '').lower())


def same_schedule(a, b, lo, hi):
    """programmes start at the same minutes (and are not only round hours, or their titles agree)"""
    ea = [e for e in a if lo <= e['start'] < hi]
    eb = [e for e in b if lo <= e['start'] < hi]
    if len(ea) < 4 or len(eb) < 4:
        return False
    j, hit, distinct, titles = 0, 0, 0, 0
    durs = set()
    for x in ea:
        while j < len(eb) and eb[j]['start'] < x['start'] - 120:
            j += 1
        if j < len(eb) and abs(eb[j]['start'] - x['start']) <= 120:
            hit += 1
            if abs(eb[j]['stop'] - x['stop']) <= 120:
                durs.add((x['stop'] - x['start']) // 300)
            if 3 <= (x['start'] // 60) % 30 <= 27:
                distinct += 1
            ta, tb = _tnorm(x['title']), _tnorm(eb[j]['title'])
            if ta and tb and (ta == tb or (len(ta) > 3 and (ta in tb or tb in ta)) or
                              SequenceMatcher(None, ta, tb).ratio() >= 0.6):
                titles += 1
    if hit < 4 or hit < 0.6 * min(len(ea), len(eb)):
        return False
    # round hours alone prove nothing; programmes of the same varied lengths (2h, 1.5h, 25 min...) do,
    # also when one file writes the titles in Arabic and the other in English
    return distinct >= 3 or titles >= 0.4 * hit or len(durs) >= 3


class UF(object):
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def rank(fname, ch, now, hi):
    """better member first: official Arabic site, Arabic titles, then the longest schedule"""
    evs = [e for e in ch['ev'] if e['stop'] > now and e['start'] < hi]
    cover = sum(min(e['stop'], hi) - max(e['start'], now) for e in evs)
    tier = 0 if fname in OFFICIAL else (1 if fname == 'ar_elcinema' else 2)
    return (ar_ratio(evs) >= 0.6, -tier, cover // 3600, sum(1 for e in evs if len(e.get('desc', '')) > 20))


def merge(members, now, hi):
    """members: [(fname, cid, ch)] best first -> merged events"""
    prim = [e for e in members[0][2]['ev'] if e['stop'] > now - WINDOW_BACK and e['start'] < hi]
    out = [dict(e) for e in prim]
    # 1. holes: programmes of the other members that fit where the best one has nothing
    for _f, _c, ch in members[1:]:
        filled = []
        cur = sorted(out, key=lambda e: e['start'])
        bounds = [(now - WINDOW_BACK, None)] + [(e['start'], e['stop']) for e in cur] + [(hi, None)]
        gaps = []
        prev_end = now - WINDOW_BACK
        for st, en in bounds[1:]:
            if st - prev_end >= 45 * 60:
                gaps.append((prev_end, st))
            if en:
                prev_end = max(prev_end, en)
        for g0, g1 in gaps:
            for e in ch['ev']:
                if e['start'] >= g0 - 60 and e['stop'] <= g1 + 60 and e['stop'] > now - WINDOW_BACK:
                    x = dict(e)
                    x['start'], x['stop'] = max(e['start'], g0), min(e['stop'], g1)
                    if x['stop'] - x['start'] >= 5 * 60:
                        filled.append(x)
        out.extend(filled)
    out.sort(key=lambda e: e['start'])
    # 2. descriptions and Arabic titles from the same programme elsewhere
    others = [ch['ev'] for _f, _c, ch in members[1:]]
    for e in out:
        same = []
        for evs in others:
            for x in evs:
                if abs(x['start'] - e['start']) <= 300 and abs((x['stop'] - x['start']) - (e['stop'] - e['start'])) <= 900:
                    same.append(x)
                    break
        if not same:
            continue
        if len(e.get('desc', '')) < 25:
            ds = sorted((x.get('desc', '') for x in same), key=lambda d: (bool(AR.search(d)) == bool(AR.search(e['title'])), len(d)))
            if ds and len(ds[-1]) > len(e.get('desc', '')):
                e['desc'] = ds[-1]
        if not AR.search(e['title']):
            ar = [x['title'] for x in same if AR.search(x['title'])]
            if ar:
                e['orig'] = e['title']
                e['title'] = ar[0]
                ad = [x.get('desc', '') for x in same if AR.search(x.get('desc', ''))]
                if ad and len(ad[0]) > 20:
                    e['desc'] = ad[0]
        for k in ('cat', 'icon', 'ep', 'sub'):
            if not e.get(k):
                for x in same:
                    if x.get(k):
                        e[k] = x[k]
                        break
    # 3. no overlaps (a filled programme never cuts the best member's own)
    clean, last_end = [], 0
    for e in out:
        if e['start'] >= last_end - 60:
            clean.append(e)
            last_end = e['stop']
    return clean


def build(gdir, index, catalog, days=3, data=None, log=print):
    """-> (data, members map). Writes guides/gold.xml.gz and adds it to the index."""
    now = time.time()
    hi = now + days * 86400
    data = data if data is not None else load_arabic(gdir, index)
    cat_names = {}
    for c in catalog:
        cat_names['%s.%s' % (c['src'], c['id'])] = c.get('names') or []
    nodes = {}
    by_key = {}
    for fname, chans in data.items():
        kind = (index['files'].get(fname) or {}).get('kind')
        for cid, ch in chans.items():
            if len([e for e in ch['ev'] if e['stop'] > now]) < 4:
                continue
            node = (fname, cid)
            nodes[node] = ch
            names = list(ch['names']) + (cat_names.get(cid, []) if kind == 'site' else [])
            ch['all_names'] = names
            for n in names:
                for k in name_keys(n):
                    by_key.setdefault(k, set()).add(node)
    uf = UF()
    lo, hi2 = now - 3600, now + 48 * 3600
    tried = set()
    for k, group in by_key.items():
        if len(group) < 2 or len(group) > 40:
            continue
        g = sorted(group)
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                a, b = g[i], g[j]
                if (a, b) in tried or uf.find(a) == uf.find(b):
                    continue
                tried.add((a, b))
                if same_schedule(nodes[a]['ev'], nodes[b]['ev'], lo, hi2):
                    uf.union(a, b)
    groups = {}
    for node in nodes:
        groups.setdefault(uf.find(node), []).append(node)
    gold, members_map, used_ids = [], {}, set()
    for g in groups.values():
        if len(g) < 2:
            continue
        ms = sorted(((f, c, nodes[(f, c)]) for f, c in g), key=lambda m: rank(m[0], m[2], now, hi), reverse=True)
        names = []
        for _f, _c, ch in ms:
            for n in ch['all_names']:
                if n not in names:
                    names.append(n)
        lat = [n for n in names if re.search('[A-Za-z]{2}', n)]
        base = re.sub('[^0-9a-z]', '', (lat[0] if lat else matcher.latin(names[0])).lower())[:40] or 'ch'
        gid, i = 'gold.' + base, 2
        while gid in used_ids:
            gid, i = 'gold.%s%d' % (base, i), i + 1
        used_ids.add(gid)
        evs = merge(ms, now, hi)
        if len(evs) < 4:
            continue
        gold.append((gid, names[:10], evs, ms))
        for f, c, _ch in ms:
            members_map[plugin_key(f, (index['files'].get(f) or {}).get('kind'), c)] = gid
    out = os.path.join(gdir, 'gold.xml.gz')
    n_ev = n_ar = 0
    with gzip.open(out, 'wt', encoding='utf-8', compresslevel=9) as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="SaadEPG-gold">\n')
        for gid, names, _evs, _ms in gold:
            f.write('<channel id="%s">%s</channel>\n' % (gid, ''.join(
                '<display-name>%s</display-name>' % html.escape(n) for n in names)))
        for gid, _names, evs, _ms in gold:
            for e in evs:
                n_ev += 1
                n_ar += 1 if e.get('orig') else 0
                desc = e.get('desc', '')
                if e.get('orig'):
                    desc = (desc + '\n' if desc else '') + '(%s)' % e['orig']
                f.write('<programme start="%s" stop="%s" channel="%s"><title>%s</title>' % (
                    fmt(e['start']), fmt(e['stop']), gid, html.escape(e['title'])))
                if e.get('sub'):
                    f.write('<sub-title>%s</sub-title>' % html.escape(e['sub']))
                if desc:
                    f.write('<desc>%s</desc>' % html.escape(desc[:1000]))
                if e.get('cat'):
                    f.write('<category>%s</category>' % html.escape(e['cat']))
                if e.get('ep'):
                    f.write('<episode-num system="onscreen">%s</episode-num>' % html.escape(e['ep']))
                f.write('</programme>\n')
        f.write('</tv>\n')
    sats = []
    for _g, _n, _e, ms in gold:
        for fn, _c, _ch in ms:
            for s in (index['files'].get(fn) or {}).get('sat') or []:
                if s not in sats:
                    sats.append(s)
    index['files']['gold'] = {
        'url': 'guides/gold.xml.gz', 'sat': sats, 'title': 'الدليل الذهبي', 'kind': 'xmltv', 'region': 'ar',
        'lang': 'ar', 'size': os.path.getsize(out), 'programmes': n_ev,
        'channels': [[gid, names] for gid, names, _e, _m in gold]}
    index['gold'] = {'file': 'gold', 'members': members_map}
    log('gold: %d channels from %d members (%d sources), %d programmes, %d English titles given their Arabic one' % (
        len(gold), len(members_map), len(data), n_ev, n_ar))
    for gid, names, evs, ms in sorted(gold, key=lambda g: -len(g[3]))[:12]:
        log('   %-28s %s' % (gid, ' + '.join('%s/%s' % (f, c) for f, c, _x in ms)[:150]))
    return data, members_map
