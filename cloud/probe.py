#!/usr/bin/env python3
"""Save the raw pages of official Arabic TV sites (see probe.json) to <out>/probe/ so that readers can be
written from real data. Never fails the build."""
import argparse
import datetime
import gzip
import json
import os
import re
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/130.0 Safari/537.36')
KEYWORDS = re.compile(r'(schedule|guide|grid|programme|program|epg|tv-?guide|%D8%AC%D8%AF%D9%88%D9%84|جدول|البرامج|الدليل)',
                      re.I)


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'ar,en;q=0.8'})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = r.read(4 * 1024 * 1024)
        if r.headers.get('Content-Encoding') == 'gzip':
            data = gzip.decompress(data)
        return r.status, r.geturl(), dict(r.headers), data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='public')
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(HERE, 'probe.json'), encoding='utf8'))
    beirut = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=3)
    tom = beirut + datetime.timedelta(days=1)
    fill = {'dY': beirut.strftime('%Y'), 'dM': beirut.strftime('%m'), 'dD': beirut.strftime('%d'),
            'tY': tom.strftime('%Y'), 'tM': tom.strftime('%m'), 'tD': tom.strftime('%d')}
    outdir = os.path.join(a.out, 'probe')
    os.makedirs(outdir, exist_ok=True)
    report = {'generated_utc': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()), 'pages': {}}
    for name, p in cfg['pages'].items():
        url = p['url'].format(**fill)
        todo = [(name, url)]
        done = 0
        while todo and done < 6:
            n, u = todo.pop(0)
            done += 1
            ent = {'url': u}
            try:
                st, final, headers, data = fetch(u)
                path = os.path.join(outdir, n + '.html')
                with open(path, 'wb') as f:
                    f.write(data)
                ent.update(status=st, final_url=final, bytes=len(data),
                           content_type=headers.get('Content-Type', ''), file='probe/%s.html' % n)
                if p.get('discover') and n == name:
                    text = data.decode('utf-8', 'replace')
                    seen = set()
                    for href in re.findall(r'href=["\']([^"\'#]+)["\']', text):
                        full = urllib.parse.urljoin(final, href)
                        if KEYWORDS.search(full) and full not in seen and full != final and \
                                urllib.parse.urlparse(full).netloc == urllib.parse.urlparse(final).netloc:
                            seen.add(full)
                            todo.append(('%s_link%d' % (name, len(seen)), full))
                        if len(seen) >= 5:
                            break
            except Exception as e:
                ent['error'] = str(e)[:300]
            report['pages'][n] = ent
            print('%-26s %s' % (n, ent.get('status', ent.get('error'))))
            time.sleep(1)
    with open(os.path.join(outdir, 'report.json'), 'w', encoding='utf8') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
