#!/usr/bin/env python3
"""Repack everything into small per-source guides for the receiver.

  work/<site>.xml           guides grabbed by iptv-org/epg (one per site, see sources.json "grab")
  sources.json "xmltv"      ready XMLTV files downloaded here

Writes to --out:
  guides/<name>.xml.gz      compact XMLTV (only the next N days, trimmed text)
  index.json.gz             what is in each guide (channel ids + names + satellites) -> used for auto-linking
  catalog.json.gz           the plugin's channel catalog (online catalog update)
  status.json               build report
"""
import argparse
import calendar
import glob
import gzip
import html
import io
import json
import os
import re
import shutil
import time
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
import sys  # noqa: E402
sys.path.insert(0, HERE)
from tr_common import route, norm_lang, TITLE_MAX, DESC_MAX  # noqa: E402
UA = 'Mozilla/5.0 (X11; Linux x86_64) ArabEPG-cloud/1.0'


def xtime(s):
    m = re.match(r'(\d{14})\s*([+-]\d{4})?', s or '')
    if not m:
        return None
    t = time.strptime(m.group(1), '%Y%m%d%H%M%S')
    ts = calendar.timegm(t)
    if m.group(2):
        z = m.group(2)
        ts -= (1 if z[0] == '+' else -1) * (int(z[1:3]) * 3600 + int(z[3:5]) * 60)
    return ts


def fmt(ts):
    return time.strftime('%Y%m%d%H%M%S +0000', time.gmtime(ts))


def open_any(path_or_bytes):
    if isinstance(path_or_bytes, bytes):
        data = path_or_bytes
    else:
        with open(path_or_bytes, 'rb') as f:
            data = f.read()
    if data[:2] == b'\x1f\x8b':
        data = gzip.decompress(data)
    return io.BytesIO(data)


def download(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read()
        except Exception as e:
            err = e
            time.sleep(5 * (i + 1))
    raise err


def our_ids(site):
    """display-name -> our xmltv id, from cloud/channels/<site>.channels.xml"""
    path = os.path.join(HERE, 'channels', site + '.channels.xml')
    out, names = {}, {}
    if not os.path.exists(path):
        return out, names
    for m in re.finditer(r'<channel ([^>]*)>([^<]*)</channel>', open(path, encoding='utf8').read()):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        cid = html.unescape(attrs.get('xmltv_id', ''))
        out[html.unescape(m.group(2)).strip()] = cid
        names[cid] = html.unescape(m.group(2)).strip()
    return out, names


def load_tr(trdir):
    """English dictionaries made by translate_en.py: {lang: {original: english}}"""
    out = {}
    for p in glob.glob(os.path.join(trdir or '', 'en_*.json.gz')):
        try:
            with gzip.open(p, 'rt', encoding='utf-8') as f:
                d = json.load(f)
            # older dictionaries kept SentencePiece markers (u2581) between words
            out[os.path.basename(p)[3:-8]] = dict(
                (k, re.sub(r'\s+', ' ', v.replace('\u2581', ' ')).strip()) for k, v in d.items() if v)
        except Exception as e:
            print('dictionary %s: %s' % (p, e))
    return out


def tag2(tag, text, en):
    """the original text, then its English translation marked tr="1" (the plugin chooses)"""
    s = '<%s>%s</%s>' % (tag, html.escape(text), tag)
    if en and en != text:
        s += '<%s lang="en" tr="1">%s</%s>' % (tag, html.escape(en), tag)
    return s


def title_pairs(paths):
    """the English copy (channel id + '.en') next to the Arabic guide of the same site: the same channel at the same
    time -> {english title or description: real Arabic one}, taken when most occurrences agree."""
    ar, en = {}, {}
    for p in paths:
        try:
            for _ev, el in ET.iterparse(open_any(p), events=('end',)):
                if el.tag == 'programme':
                    cid, st = el.get('channel') or '', el.get('start', '')[:12]
                    rec = (el.findtext('title') or '').strip(), (el.findtext('desc') or '').strip()[:DESC_MAX]
                    if cid.endswith('.en'):
                        en.setdefault(cid[:-3], {})[st] = rec
                    else:
                        ar.setdefault(cid, {})[st] = rec
                    el.clear()
        except ET.ParseError as e:
            print('pairs: %s: %s' % (p, e))
    votes = {}
    for cid, progs in en.items():
        for st, (et, ed) in progs.items():
            a = ar.get(cid, {}).get(st)
            if not a:
                continue
            for e_txt, a_txt in ((et, a[0]), (ed, a[1])):
                if e_txt and a_txt and AR_RE.search(a_txt) and not AR_RE.search(e_txt) and e_txt != a_txt:
                    votes.setdefault(e_txt, {})
                    votes[e_txt][a_txt] = votes[e_txt].get(a_txt, 0) + 1
    out = {}
    for e_txt, c in votes.items():
        best = max(c.items(), key=lambda kv: kv[1])
        if best[1] * 2 > sum(c.values()):
            out[e_txt] = best[0]
    return out


def repack(srcs, out_path, days, idmap=None, catalog_names=None, maxdesc=DESC_MAX, lang=None, tr=None, aliases=None):
    """stream-parse XMLTV file(s), keep [now-6h, now+days], write compact gz. -> {id: [names]}
    lang/tr: language of a foreign file and the English dictionaries -> English text added next to the original."""
    tr = tr or {}
    stats = [0, 0]
    now = time.time()
    lo, hi = now - 6 * 3600, now + days * 86400
    chans, progs = {}, {}
    rename = {}
    if not isinstance(srcs, list):
        srcs = [srcs]
    for _ev, el in (x for src in srcs for x in ET.iterparse(src, events=('end',))):
        if el.tag == 'channel':
            cid = el.get('id') or ''
            names = [d.text.strip() for d in el.findall('display-name') if d.text and d.text.strip()]
            new = cid
            if idmap is not None and cid not in (catalog_names or {}):
                for n in names:
                    if n in idmap:
                        new = idmap[n]
                        break
            rename[cid] = new
            extra = list((aliases or {}).get(cid) or [])
            for n in names:
                ln = latin_name(n)
                if ln and ln not in names + extra:
                    extra.append(ln)
            chans[new] = names + [x for x in extra if x not in names]
            el.clear()
        elif el.tag == 'programme':
            cid = rename.get(el.get('channel'), el.get('channel'))
            if (cid or '').endswith('.en'):        # English twin: only for title_pairs, not published
                el.clear()
                continue
            st, en = xtime(el.get('start')), xtime(el.get('stop'))
            if st and en and en > lo and st < hi:
                p = {'start': st, 'stop': en}
                for tag in ('title', 'sub-title', 'desc'):
                    e = el.find(tag)
                    if e is not None and e.text and e.text.strip():
                        p[tag] = e.text.strip()[:maxdesc if tag == 'desc' else TITLE_MAX]
                        p[tag + '@'] = e.get('lang')
                cats = [c for c in el.findall('category') if c.text and c.text.strip()]
                if cats:
                    p['category'] = cats[0].text.strip()[:60]
                    p['category@'] = cats[0].get('lang')
                epn = el.find('episode-num')
                if epn is not None and epn.text:
                    p['episode-num'] = (epn.get('system') or 'onscreen', epn.text.strip()[:30])
                if p.get('title'):
                    progs.setdefault(cid, []).append(p)
            el.clear()
    with gzip.open(out_path, 'wt', encoding='utf-8', compresslevel=9) as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="ArabEPG-cloud">\n')
        kept = {}
        for cid, plist in progs.items():
            names = chans.get(cid) or ([catalog_names[cid]] if catalog_names and cid in catalog_names else [cid])
            kept[cid] = names
            f.write('<channel id="%s">%s</channel>\n' % (html.escape(cid), ''.join(
                '<display-name>%s</display-name>' % html.escape(n) for n in names[:8])))
        written = 0
        for cid, plist in progs.items():
            seen = set()
            for p in sorted(plist, key=lambda x: x['start']):
                if p['start'] in seen:
                    continue
                seen.add(p['start'])
                written += 1
                f.write('<programme start="%s" stop="%s" channel="%s">' % (fmt(p['start']), fmt(p['stop']), html.escape(cid)))

                def en(tag, p=p):
                    if not lang or not p.get(tag):
                        return None
                    l = route(lang, p.get(tag + '@'), p[tag])
                    return tr.get(l, {}).get(p[tag]) if l else None
                t_en = en('title')
                stats[0] += 1
                stats[1] += 1 if t_en else 0
                f.write(tag2('title', p['title'], t_en))
                if p.get('sub-title'):
                    f.write('<sub-title>%s</sub-title>' % html.escape(p['sub-title']))
                if p.get('desc'):
                    f.write(tag2('desc', p['desc'], en('desc')))
                if p.get('category'):
                    f.write(tag2('category', p['category'], en('category')))
                if p.get('episode-num'):
                    f.write('<episode-num system="%s">%s</episode-num>' % (
                        html.escape(p['episode-num'][0]), html.escape(p['episode-num'][1])))
                f.write('</programme>\n')
        f.write('</tv>\n')
    if lang:
        print('   English titles: %d/%d (%d%%)' % (stats[1], stats[0], stats[1] * 100 // max(1, stats[0])))
    return kept, written


AR_RE = re.compile('[\u0621-\u064a]')
_CYR = dict(zip('абвгдезийклмнопрстуфхцъыьэіґ', ['a', 'b', 'v', 'g', 'd', 'e', 'z', 'i', 'y', 'k', 'l', 'm', 'n', 'o',
                                                'p', 'r', 's', 't', 'u', 'f', 'h', 'ts', 'a', 'y', '', 'e', 'i', 'g']))
_CYR.update({'ж': 'zh', 'ч': 'ch', 'ш': 'sh', 'щ': 'sht', 'ю': 'yu', 'я': 'ya', 'ё': 'yo', 'є': 'ye', 'ї': 'yi'})
_CYR.update({'α': 'a', 'β': 'v', 'γ': 'g', 'δ': 'd', 'ε': 'e', 'ζ': 'z', 'η': 'i', 'θ': 'th', 'ι': 'i',
             'κ': 'k', 'λ': 'l', 'μ': 'm', 'ν': 'n', 'ξ': 'x', 'ο': 'o', 'π': 'p', 'ρ': 'r', 'σ': 's', 'ς': 's',
             'τ': 't', 'υ': 'y', 'φ': 'f', 'χ': 'ch', 'ψ': 'ps', 'ω': 'o', 'ά': 'a', 'έ': 'e', 'ή': 'i',
             'ί': 'i', 'ό': 'o', 'ύ': 'y', 'ώ': 'o', 'ϊ': 'i', 'ϋ': 'y', 'ΐ': 'i', 'ΰ': 'y'})


def latin_name(n):
    """Cyrillic / Greek channel name -> Latin spelling (БНТ1 -> BNT1, ΒΕΡΓΙΝΑ -> VERGINA)"""
    if not re.search('[\u0400-\u04ff\u0370-\u03ff]', n):
        return None
    out = ''
    for ch in n:
        lo = ch.lower()
        t = _CYR.get(lo, ch)
        out += t.capitalize() if ch != lo and t else t
    return out


def extract(gdir, name, spec):
    """copy the channels that match spec['names'] (or have mostly Arabic titles) out of other guides."""
    pats = [re.compile(r'(^|[^a-z0-9])%s([^a-z0-9]|$)' % re.escape(n.lower())) for n in spec.get('names', [])]
    chans, progs = {}, {}
    for src in spec.get('from', []):
        path = os.path.join(gdir, src + '.xml.gz')
        if not os.path.exists(path):
            continue
        names, plist = {}, {}
        for _ev, el in ET.iterparse(open_any(path), events=('end',)):
            if el.tag == 'channel':
                names[el.get('id')] = [d.text for d in el.findall('display-name') if d.text]
                el.clear()
            elif el.tag == 'programme':
                plist.setdefault(el.get('channel'), []).append(ET.tostring(el, encoding='unicode'))
                el.clear()
        for cid, nm in names.items():
            label = ' '.join(nm + [cid]).lower()
            hit = any(p.search(label) for p in pats)
            if not hit and spec.get('auto_arabic') and plist.get(cid):
                titles = [re.search(r'<title[^>]*>(.*?)</title>', x, re.S) for x in plist[cid][:40]]
                titles = [t.group(1) for t in titles if t]
                hit = bool(titles) and sum(1 for t in titles if AR_RE.search(t)) * 2 >= len(titles)
            if hit and plist.get(cid) and cid not in chans:
                chans[cid] = nm or [cid]
                progs[cid] = plist[cid]
    if not chans:
        return {}, 0
    with gzip.open(os.path.join(gdir, name + '.xml.gz'), 'wt', encoding='utf-8', compresslevel=9) as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="ArabEPG-cloud">\n')
        for cid, nm in chans.items():
            f.write('<channel id="%s">%s</channel>\n' % (html.escape(cid), ''.join(
                '<display-name>%s</display-name>' % html.escape(n) for n in nm[:4])))
        n = 0
        for cid, plist in progs.items():
            for x in plist:
                f.write(x.strip() + '\n')
                n += 1
        f.write('</tv>\n')
    return chans, n


AR_XML = ('sattv_ar', 'ae', 'sa', 'eg', 'bein', 'oe_egypt', 'oe_uae', 'oe_qatar', 'oe_saudi', 'oe_arabia', 'ar_from_eu', 'ar_from_na', 'aljazeera')


def _is_ar_xml(fname):
    return any(fname == p or fname.startswith(p) for p in AR_XML)


def _norm_name(n):
    n = re.sub(r'\.[a-z]{2}$', '', (n or '').lower())
    n = re.sub(r'\[[^\]]*\]|\([^)]*\)', ' ', n)
    n = re.sub(r'[\W_]+', ' ', n)
    n = ' '.join(w for w in n.split() if w not in ('hd', 'fhd', 'uhd', 'sd', 'tv', 'channel', 'hq', 'the', '4k'))
    return n.replace(' ', '')


TIME_ANCHORS = ('oe_egypt1', 'oe_egypt2', 'oe_saudiarabia1', 'oe_saudiarabia2', 'oe_saudiarabia3',
                'oe_saudiarabia4', 'oe_uae1', 'oe_qatar1', 'ar_osn', 'ar_shahid', 'ar_beinsports')
_PROG = re.compile(r'<programme start="([^"]+)" stop="([^"]+)" channel="([^"]+)"><title[^>]*>([^<]*)</title>')


def _tnorm(t):
    return re.sub(u'[^0-9a-zء-ي]', '', html.unescape(t).lower())


def _read_titles(path):
    ch = {}
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        for m in _PROG.finditer(f.read()):
            t = _tnorm(m.group(4))
            st = xtime(m.group(1))
            if len(t) >= 4 and st:
                ch.setdefault(m.group(3), {})[st] = t
    return ch


def fix_time_offsets(gdir, index, anchors=TIME_ANCHORS, log=print):
    """Some sites publish local time but the grabber reads it as another zone (elCinema: Cairo time read as
    UTC; rotana.net: UTC read as Riyadh...). Every Arabic file is compared with the anchor files (they agree
    with each other and with real kick-off times): the same programme title on two channels at a constant
    distance of whole hours. A file whose channels agree on one shift is moved back to real time."""
    files = dict((n, f) for n, f in (index.get('files') or {}).items() if f.get('region') == 'ar')
    data = {}
    for n in files:
        try:
            data[n] = _read_titles(os.path.join(gdir, n + '.xml.gz'))
        except Exception as e:
            log('time check: %s unreadable (%s)' % (n, e))
    anc = [n for n in anchors if n in data]
    occ = {}
    for n in anc:
        for cid, ev in data[n].items():
            for st, t in ev.items():
                occ.setdefault(t, []).append((n, cid, st))
    fixes = {}
    for n, chans in data.items():
        if n in anc:
            continue
        votes = {}
        for cid, ev in chans.items():
            pair = {}
            for st, t in ev.items():
                o = occ.get(t)
                if not o or len(o) > 40:          # news, "Live" ... say nothing
                    continue
                for an, acid, ast in o:
                    d = st - ast
                    if d % 1800 == 0 and abs(d) <= 6 * 3600:
                        k = (an, acid, d)
                        pair[k] = pair.get(k, 0) + 1
            if not pair:
                continue
            (_an, _acid, d), hits = max(pair.items(), key=lambda kv: kv[1])
            if hits >= 4:
                votes[d] = votes.get(d, 0) + 1
        tot = sum(votes.values())
        if tot < 3:
            continue
        d, v = max(votes.items(), key=lambda kv: kv[1])
        if d and v >= 3 and v >= 0.7 * tot:
            fixes[n] = (-d, v, tot)
    for n, (sh, v, tot) in sorted(fixes.items()):
        path = os.path.join(gdir, n + '.xml.gz')

        def mv(m):
            return '<programme start="%s" stop="%s"' % (fmt(xtime(m.group(1)) + sh), fmt(xtime(m.group(2)) + sh))
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            s = f.read()
        s = re.sub(r'<programme start="([^"]+)" stop="([^"]+)"', mv, s)
        with gzip.open(path, 'wt', encoding='utf-8', compresslevel=9) as f:
            f.write(s)
        index['files'][n]['size'] = os.path.getsize(path)
        index['files'][n]['time_fix'] = sh
        log('time fixed: %-16s %+.1f h  (%d of %d channels agree)' % (n, sh / 3600.0, v, tot))
    return dict((n, sh // 60) for n, (sh, _v, _t) in fixes.items())


def remove_copied_schedules(gdir, index, hours=24, min_progs=4):
    now = time.time()
    fps = {}
    for fname, f in (index.get('files') or {}).items():
        path = os.path.join(gdir, fname + '.xml.gz')
        if not os.path.exists(path):
            continue
        names = dict((cid, nm) for cid, nm in f.get('channels') or [])
        progs = {}
        for _ev, el in ET.iterparse(open_any(path), events=('end',)):
            if el.tag == 'programme':
                st = xtime(el.get('start'))
                if st and now <= st < now + hours * 3600:
                    progs.setdefault(el.get('channel'), []).append((st // 60, (el.findtext('title') or '').strip().lower()))
                el.clear()
        for cid, p in progs.items():
            if len(p) >= min_progs:
                nm = names.get(cid) or [cid]
                fps.setdefault(tuple(sorted(p)), []).append((fname, cid, _norm_name(nm[0]) or _norm_name(cid)))
    drop = {}
    for members in fps.values():
        if len(members) < 2 or not any(_is_ar_xml(f) for f, _c, _n in members):
            continue
        clusters = []
        for m in members:
            for cl in clusters:
                n0 = cl[0][2]
                if m[2] and n0 and (m[2] == n0 or (len(m[2]) > 3 and m[2] in n0) or (len(n0) > 3 and n0 in m[2])):
                    cl.append(m)
                    break
            else:
                clusters.append([m])
        if len(clusters) < 2:
            continue           # the same channel in several files: a real schedule
        size = sorted((len(set(f for f, _c, _n in cl)), i) for i, cl in enumerate(clusters))
        keep = clusters[size[-1][1]] if size[-1][0] > size[-2][0] else []
        for cl in clusters:
            if cl is keep:
                continue
            for f, cid, _n in cl:
                if _is_ar_xml(f):
                    drop.setdefault(f, set()).add(cid)
    for f, cids in drop.items():
        ent = index['files'][f]
        ent['channels'] = [c for c in ent['channels'] if c[0] not in cids]
    return dict((f, sorted(c)[:50]) for f, c in drop.items())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', default='work')
    ap.add_argument('--out', default='public')
    ap.add_argument('--catalog', default='plugin/SaadEPG/channels.json')
    ap.add_argument('--skip-download', action='store_true')
    ap.add_argument('--tr', default='', help='folder with en_<lang>.json.gz (foreign titles -> English)')
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(HERE, 'sources.json'), encoding='utf8'))
    days = cfg.get('days', 3)
    gdir = os.path.join(a.out, 'guides')
    os.makedirs(gdir, exist_ok=True)
    index = {'generated': int(time.time()), 'days': days, 'files': {}}
    index_tr = {}
    status = {'generated': time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime()), 'files': {}}

    pairs = {}
    tr = load_tr(a.tr)
    if tr:
        print('English dictionaries: ' + ', '.join('%s %d' % (k, len(v)) for k, v in sorted(tr.items())))
        # small title-only dictionaries for the receiver: it translates the satellite's own guide with them
        for lang, d in tr.items():
            t = dict((k, v) for k, v in d.items() if len(k) <= 120 and len(v) <= 160 and k != v)
            with gzip.open(os.path.join(a.tr, 't_%s.json.gz' % lang), 'wt', encoding='utf-8', compresslevel=9) as f:
                json.dump(t, f, ensure_ascii=False, separators=(',', ':'))
        index_tr = dict((lang, 'tr/t_%s.json.gz' % lang) for lang in tr)
    jobs = [(n, d, 'grab') for n, d in cfg['grab'].items()] + [(n, d, 'xmltv') for n, d in cfg['xmltv'].items()]
    for name, d, kind in jobs:
        t0 = time.time()
        out_path = os.path.join(gdir, name + '.xml.gz')
        if d.get('only_grab') or d.get('disabled'):
            continue       # raw material for another file (sat.tv -> sattv_ar / sattv_en)
        try:
            fdays = d.get('days', days)
            if kind == 'grab':
                srcs = [p for p in sorted(glob.glob(os.path.join(a.work, 'out_%s.*xml' % d['site'])) +
                                          glob.glob(os.path.join(a.work, d['site'] + '.xml*')))
                        if os.path.getsize(p) > 200]
                if not srcs:
                    raise RuntimeError('no grab output')
                idmap, cnames = our_ids(d['site'])
                chans, n = repack([open_any(p) for p in srcs], out_path, fdays, idmap, cnames)
                chans = dict((c, nm) for c, nm in chans.items() if not c.endswith('.en'))
                if a.tr and any(c.endswith('.en') for c in cnames):
                    pr = title_pairs(srcs)
                    pairs.update(pr)
                    print('   real Arabic titles/descriptions from the English copy: %d' % len(pr))
            else:
                if a.skip_download and not d['url'].startswith('local:'):
                    continue
                if d['url'].startswith('local:'):       # made in this run (e.g. sat.tv merged by sattv.py)
                    lp = d['url'][6:]
                    if not os.path.exists(lp) or os.path.getsize(lp) < 200:
                        raise RuntimeError('not made in this run')
                    src = open_any(lp)
                else:
                    src = open_any(download(d['url']))
                chans, n = repack(src, out_path, fdays,
                                  lang=norm_lang(d.get('lang')) or None, tr=tr, aliases=d.get('aliases'))
            if not chans:
                raise RuntimeError('empty guide')
            index['files'][name] = {
                'url': 'guides/%s.xml.gz' % name, 'sat': d.get('sat', []), 'title': d.get('title', name),
                'kind': 'site' if kind == 'grab' else 'xmltv', 'size': os.path.getsize(out_path),
                'region': d['region'] if 'region' in d else (
                    'ar' if (kind == 'grab' or _is_ar_xml(name)) else ('eu' if d.get('lang') else None)),
                'lang': d.get('lang') or ('ar' if (kind == 'grab' or _is_ar_xml(name)) else None),
                'programmes': n, 'channels': [[cid, names] for cid, names in sorted(chans.items())]}
            status['files'][name] = {'ok': True, 'channels': len(chans), 'programmes': n,
                                     'size_kb': os.path.getsize(out_path) // 1024, 'sec': int(time.time() - t0)}
            print('%-14s OK  %5d channels %7d programmes %6d KB' % (name, len(chans), n, os.path.getsize(out_path) // 1024))
        except Exception as e:
            status['files'][name] = {'ok': False, 'error': str(e)[:300]}
            print('%-14s FAIL %s' % (name, e))
            if os.path.exists(out_path):
                os.remove(out_path)

    # Arabic channels that only exist in European files (2M, ART Cinema...): a small file for Arabic satellites
    for name, spec in (cfg.get('extract') or {}).items():
        t0 = time.time()
        try:
            chans, n = extract(gdir, name, spec)
            if not chans:
                raise RuntimeError('nothing found')
            out_path = os.path.join(gdir, name + '.xml.gz')
            index['files'][name] = {
                'url': 'guides/%s.xml.gz' % name, 'sat': spec.get('sat', []), 'title': spec.get('title', name),
                'kind': 'xmltv', 'size': os.path.getsize(out_path), 'programmes': n,
                'channels': [[cid, names] for cid, names in sorted(chans.items())]}
            status['files'][name] = {'ok': True, 'channels': len(chans), 'programmes': n,
                                     'size_kb': os.path.getsize(out_path) // 1024, 'sec': int(time.time() - t0)}
            print('%-14s OK  %5d channels %7d programmes (extracted)' % (name, len(chans), n))
        except Exception as e:
            status['files'][name] = {'ok': False, 'error': str(e)[:300]}
            print('%-14s FAIL %s' % (name, e))

    # files that are only used to pick out Arabic channels are not published themselves
    for name, d in cfg['xmltv'].items():
        if d.get('only_extract'):
            index['files'].pop(name, None)
            try:
                os.remove(os.path.join(gdir, name + '.xml.gz'))
            except OSError:
                pass

    # sites that publish their schedule in the wrong time zone are moved back to real (UTC) time
    try:
        status['time_fixed_min'] = fix_time_offsets(gdir, index)
    except Exception as e:
        print('time check failed: %s' % e)

    # Many channels in the ready-made Arabic files carry a COPY of another channel's schedule (placeholder data).
    # Linking a channel to such a copy shows a wrong guide, so those channels are taken out of the index.
    try:
        removed = remove_copied_schedules(gdir, index)
        status['copied_schedules_removed'] = removed
        print('copied schedules removed: %d channels' % sum(len(v) for v in removed.values()))
    except Exception as e:
        print('copied schedule check failed: %s' % e)

    if a.tr and pairs:
        with gzip.open(os.path.join(a.tr, 'pairs_ar.json.gz'), 'wt', encoding='utf-8', compresslevel=9) as f:
            json.dump(pairs, f, ensure_ascii=False, separators=(',', ':'))
        print('real Arabic titles/descriptions (pairs_ar): %d' % len(pairs))
    index['tr'] = index_tr
    with gzip.open(os.path.join(a.out, 'index.json.gz'), 'wt', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, separators=(',', ':'))
    if os.path.exists(a.catalog):
        with open(a.catalog, 'rb') as src, gzip.open(os.path.join(a.out, 'catalog.json.gz'), 'wb') as dst:
            shutil.copyfileobj(src, dst)
    with open(os.path.join(a.out, 'status.json'), 'w', encoding='utf8') as f:
        json.dump(status, f, ensure_ascii=False, indent=1)
    ok = sum(1 for v in status['files'].values() if v.get('ok'))
    print('done: %d/%d guides' % (ok, len(status['files'])))
    if ok == 0:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
