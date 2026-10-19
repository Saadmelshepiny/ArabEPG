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


def repack(src, out_path, days, idmap=None, catalog_names=None, maxdesc=500):
    """stream-parse an XMLTV file, keep [now-6h, now+days], write compact gz. -> {id: [names]}"""
    now = time.time()
    lo, hi = now - 6 * 3600, now + days * 86400
    chans, progs = {}, {}
    rename = {}
    for _ev, el in ET.iterparse(src, events=('end',)):
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
            chans[new] = names
            el.clear()
        elif el.tag == 'programme':
            cid = rename.get(el.get('channel'), el.get('channel'))
            st, en = xtime(el.get('start')), xtime(el.get('stop'))
            if st and en and en > lo and st < hi:
                p = {'start': st, 'stop': en}
                for tag in ('title', 'sub-title', 'desc'):
                    v = el.findtext(tag)
                    if v:
                        p[tag] = v.strip()[:maxdesc if tag == 'desc' else 200]
                cats = [c.text.strip() for c in el.findall('category') if c.text]
                if cats:
                    p['category'] = cats[0][:60]
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
                '<display-name>%s</display-name>' % html.escape(n) for n in names[:4])))
        for cid, plist in progs.items():
            seen = set()
            for p in sorted(plist, key=lambda x: x['start']):
                if p['start'] in seen:
                    continue
                seen.add(p['start'])
                f.write('<programme start="%s" stop="%s" channel="%s">' % (fmt(p['start']), fmt(p['stop']), html.escape(cid)))
                f.write('<title>%s</title>' % html.escape(p['title']))
                if p.get('sub-title'):
                    f.write('<sub-title>%s</sub-title>' % html.escape(p['sub-title']))
                if p.get('desc'):
                    f.write('<desc>%s</desc>' % html.escape(p['desc']))
                if p.get('category'):
                    f.write('<category>%s</category>' % html.escape(p['category']))
                if p.get('episode-num'):
                    f.write('<episode-num system="%s">%s</episode-num>' % (
                        html.escape(p['episode-num'][0]), html.escape(p['episode-num'][1])))
                f.write('</programme>\n')
        f.write('</tv>\n')
    return kept, sum(len(v) for v in progs.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', default='work')
    ap.add_argument('--out', default='public')
    ap.add_argument('--catalog', default='plugin/ArabEPG/channels.json')
    ap.add_argument('--skip-download', action='store_true')
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(HERE, 'sources.json'), encoding='utf8'))
    days = cfg.get('days', 3)
    gdir = os.path.join(a.out, 'guides')
    os.makedirs(gdir, exist_ok=True)
    index = {'generated': int(time.time()), 'days': days, 'files': {}}
    status = {'generated': time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime()), 'files': {}}

    jobs = [(n, d, 'grab') for n, d in cfg['grab'].items()] + [(n, d, 'xmltv') for n, d in cfg['xmltv'].items()]
    for name, d, kind in jobs:
        t0 = time.time()
        out_path = os.path.join(gdir, name + '.xml.gz')
        try:
            if kind == 'grab':
                src = None
                for ext in ('.xml', '.xml.gz'):
                    p = os.path.join(a.work, d['site'] + ext)
                    if os.path.exists(p) and os.path.getsize(p) > 200:
                        src = p
                        break
                if not src:
                    raise RuntimeError('no grab output')
                idmap, cnames = our_ids(d['site'])
                chans, n = repack(open_any(src), out_path, days, idmap, cnames)
            else:
                if a.skip_download:
                    continue
                chans, n = repack(open_any(download(d['url'])), out_path, days)
            if not chans:
                raise RuntimeError('empty guide')
            index['files'][name] = {
                'url': 'guides/%s.xml.gz' % name, 'sat': d.get('sat', []), 'title': d.get('title', name),
                'kind': 'site' if kind == 'grab' else 'xmltv', 'size': os.path.getsize(out_path),
                'programmes': n, 'channels': [[cid, names] for cid, names in sorted(chans.items())]}
            status['files'][name] = {'ok': True, 'channels': len(chans), 'programmes': n,
                                     'size_kb': os.path.getsize(out_path) // 1024, 'sec': int(time.time() - t0)}
            print('%-14s OK  %5d channels %7d programmes %6d KB' % (name, len(chans), n, os.path.getsize(out_path) // 1024))
        except Exception as e:
            status['files'][name] = {'ok': False, 'error': str(e)[:300]}
            print('%-14s FAIL %s' % (name, e))
            if os.path.exists(out_path):
                os.remove(out_path)

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
