#!/usr/bin/env python3
"""Build the channel catalog from the iptv-org/epg channel lists.

Outputs
  --catalog  plugin catalog (json): [{"src","id","names"}]  -> used by the receiver for auto-linking
  --cloud    folder with one <site>.channels.xml per site      -> used by the cloud grabber (GitHub Actions)

Usage: python3 tools/make_catalog.py --iptv iptv-epg --catalog plugin/ArabEPG/channels.json --cloud cloud/channels
"""
import argparse
import html
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

# site folder in iptv-org/epg  ->  our short source name
SITES = [
    ('shahid.mbc.net', 'shahid', ['ar', 'en']),
    ('osn.com', 'osn', ['en', 'ar']),
    ('elcinema.com', 'elcinema', ['ar', 'en']),
    ('bein.com', 'bein', ['en', 'ar']),
    ('beinsports.com', 'beinsports', ['mena-ar']),
    ('rotana.net', 'rotana', ['ar', 'en']),
    ('aljazeera.com', 'aljazeera', [None]),
    ('roya-tv.com', 'roya', [None]),
    ('artonline.tv', 'art', [None]),
    ('ayn.om', 'ayn', [None]),
    ('snrt.ma', 'snrt', [None]),
]
# sat.tv: satellite 1 lineup 33 = Eutelsat/Nilesat 7-8W (MENA), satellite 2 lineup 55 = Hotbird 13E
SATV_LINEUPS = ('1#33#', '2#55#')


def camel(xid):
    x = xid.split('@')[0].split('.')[0].replace('beIN', 'Bein')
    feed = xid.split('@')[1] if '@' in xid else ''
    name = re.sub(r'(?<=[a-z])(?=[A-Z0-9])|(?<=[0-9])(?=[A-Za-z])', ' ', x).replace('Bein', 'beIN')
    if feed and feed not in ('SD', 'HD', 'FHD', 'UHD', 'MENA', 'MiddleEast', 'Arabic'):
        name += ' ' + feed   # AlJazeera.qa@English -> "Al Jazeera English"
    return name


def bein_name(slug):
    s = slug
    for a, b in (('beINSPORTSXTRA', 'beIN Sports Xtra '), ('beINSPORTSMAX', 'beIN Sports Max '),
                 ('beINSPORTSNews', 'beIN Sports News'), ('beINSPORTS', 'beIN Sports '),
                 ('beINSPORT', 'beIN Sports '), ('beINMOVIES', 'beIN Movies '), ('beINSERIES', 'beIN Series '),
                 ('beINDRAMA', 'beIN Drama'), ('BaraemTV', 'Baraem')):
        if s.startswith(a):
            s = b + s[len(a):]
            break
    s = re.sub(r'(\d)(EN|FR)$', lambda m: m.group(1) + (' English' if m.group(2) == 'EN' else ' French'), s)
    return re.sub(r'\s+', ' ', s).strip()


def read(path):
    out = []
    if not os.path.exists(path):
        return out
    for m in re.finditer(r'<channel ([^>]*)>([^<]*)</channel>', open(path, encoding='utf8').read()):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        out.append({'site_id': html.unescape(attrs.get('site_id', '')), 'lang': attrs.get('lang', ''),
                    'xmltv': attrs.get('xmltv_id', ''), 'name': html.unescape(m.group(2)).strip()})
    return out


def esc(s):
    return html.escape(s, quote=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--iptv', required=True)
    ap.add_argument('--catalog')
    ap.add_argument('--cloud')
    a = ap.parse_args()
    aliases = json.load(open(os.path.join(HERE, 'aliases.json'), encoding='utf8'))
    catalog, cloud = [], {}
    for folder, src, langs in SITES:
        entries = {}
        order = []
        for lang in langs:
            fn = '%s_%s.channels.xml' % (folder, lang) if lang else '%s.channels.xml' % folder
            for ch in read(os.path.join(a.iptv, 'sites', folder, fn)):
                sid = ch['site_id']
                if src == 'satv':
                    if not sid.startswith(SATV_LINEUPS):
                        continue
                    sid = '%s|%s' % (ch['lang'], sid)     # language is part of the request
                key = sid
                if src == 'aljazeera':
                    key = sid
                if key not in entries:
                    entries[key] = {'lang': ch['lang'] or 'ar', 'site_id': ch['site_id'], 'names': [], 'xmltv': ''}
                    order.append(key)
                e = entries[key]
                name = ch['name']
                if src == 'bein':
                    name = bein_name(name) if not name.isdigit() else ''
                if name.endswith('...'):
                    name = ''
                if name and name not in e['names']:
                    e['names'].append(name)
                if ch['xmltv'] and not e['xmltv']:
                    e['xmltv'] = ch['xmltv']
        extra = json.load(open(os.path.join(HERE, 'extra_channels.json'), encoding='utf8')).get(folder, {})
        for sid, names in extra.items():
            if sid not in entries:
                entries[sid] = {'lang': 'ar', 'site_id': sid, 'names': list(names), 'xmltv': ''}
                order.append(sid)
        primary = set(entries[k]['names'][0].lower() for k in order if entries[k]['names'])
        for key in order:
            e = entries[key]
            x = e['xmltv']
            if x and 'USA' not in x:
                cn = re.sub(r'\s+', ' ', camel(x)).strip()
                own = e['names'][0].lower() if e['names'] else ''
                if cn not in e['names'] and (cn.lower() == own or cn.lower() not in primary):
                    e['names'].append(cn)
            if 'USA' in x and e['names']:
                e['names'] = [e['names'][0] + ' USA']
            if x.startswith('MBC1Egypt'):
                e['names'] = ['MBC1 Egypt', 'MBC 1 Masr']
            for n in aliases.get('%s:%s' % (src, key), []):
                if n not in e['names']:
                    e['names'].append(n)
            if not e['names']:
                e['names'] = ['%s %s' % (src, key)]
            catalog.append({'src': src, 'id': key, 'names': e['names']})
            cloud.setdefault(folder, []).append((e, '%s.%s' % (src, key)))

    if a.catalog:
        json.dump(catalog, open(a.catalog, 'w', encoding='utf8'), ensure_ascii=False, separators=(',', ':'))
        print('catalog: %d channels' % len(catalog))
    if a.cloud:
        os.makedirs(a.cloud, exist_ok=True)
        for folder, items in cloud.items():
            lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<channels>']
            for e, cid in items:
                lines.append('  <channel site="%s" site_id="%s" lang="%s" xmltv_id="%s">%s</channel>' % (
                    folder, esc(e['site_id']), e['lang'], esc(cid), esc(e['names'][0])))
            lines.append('</channels>')
            with open(os.path.join(a.cloud, folder + '.channels.xml'), 'w', encoding='utf8') as f:
                f.write('\n'.join(lines) + '\n')
            print('cloud %s: %d channels' % (folder, len(items)))


if __name__ == '__main__':
    main()
