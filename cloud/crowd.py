#!/usr/bin/env python3
"""The satellite's own guide, sent by the owner's receiver (optional, off by default on the receiver).

The receiver posts its satellite guide (channels the plugin does not fill) and the names of its channels
without any guide as comments on one issue of this repository, with a token that can only write issues.
Here (publish job, GITHUB_TOKEN):
  - only comments written by the repository owner are read (anyone can comment on a public issue)
  - work/crowd_ar.xml: the Arabic channels among them (channel id crowd.<SID:TSID:ONID:NS>, the receiver links
    it by its service reference, other channels can link it by name); published like any other guide
  - public/tr/wanted.txt: channels still without a guide, with the closest names found in all guides
  - comments older than KEEP_DAYS are deleted
  - index entry: the issue number, so receivers know where to post
Comment format: "SAADEPG1 <batch> <i>/<n>\\n<base64 of gzip(json)>"
"""
import base64
import calendar
import gzip
import html
import json
import os
import re
import sys
import time
import urllib.request

KEEP_DAYS = 4
TITLE = 'SaadEPG: دليل القمر من الريسيفر (رفع تلقائي - لا تعليقات يدوية هنا)'
AR = re.compile('[ء-ي]')
API = 'https://api.github.com'


def gh(method, path, token, body=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                                          'User-Agent': 'SaadEPG-cloud', 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return json.loads(data) if data else None


def find_issue(repo, token, log=print):
    for st in ('open', 'closed'):
        for it in gh('GET', '/repos/%s/issues?state=%s&per_page=100' % (repo, st), token) or []:
            if it.get('title') == TITLE and not it.get('pull_request'):
                if st == 'closed':
                    gh('PATCH', '/repos/%s/issues/%d' % (repo, it['number']), token, {'state': 'open'})
                return it['number']
    it = gh('POST', '/repos/%s/issues' % repo, token, {
        'title': TITLE, 'body': 'يكتب هنا ريسيفر صاحب المستودع (Saad EPG) دليل القمر تلقائيا كل يوم. '
                                'السحابة تقرأ تعليقات صاحب المستودع فقط وتحذف القديم منها.'})
    log('crowd: issue #%d created' % it['number'])
    return it['number']


def read_batches(repo, num, token, owner, now, log=print):
    comments, page = [], 1
    while True:
        got = gh('GET', '/repos/%s/issues/%d/comments?per_page=100&page=%d' % (repo, num, page), token) or []
        comments += got
        if len(got) < 100:
            break
        page += 1
    parts, old = {}, []
    for c in comments:
        t = calendar.timegm(time.strptime(c['created_at'], '%Y-%m-%dT%H:%M:%SZ'))
        if now - t > KEEP_DAYS * 86400:
            old.append(c['id'])
            continue
        if (c.get('user') or {}).get('login', '').lower() != owner.lower():
            continue               # never anybody else's data
        m = re.match(r'SAADEPG1 (\d+) (\d+)/(\d+)\s*\n', c.get('body') or '')
        if not m:
            continue
        b = parts.setdefault(m.group(1), {'n': int(m.group(3)), 'p': {}})
        b['p'][int(m.group(2))] = c['body'][m.end():].strip()
    batches = []
    for bid, b in sorted(parts.items()):
        if len(b['p']) != b['n']:
            continue
        try:
            raw = base64.b64decode(''.join(b['p'][i] for i in range(1, b['n'] + 1)))
            batches.append(json.loads(gzip.decompress(raw).decode('utf-8')))
        except Exception as e:
            log('crowd: batch %s unreadable (%s)' % (bid, e))
    for cid in old:
        try:
            gh('DELETE', '/repos/%s/issues/comments/%d' % (repo, cid), token)
        except Exception:
            pass
    log('crowd: %d comments, %d complete uploads, %d old comments deleted' % (len(comments), len(batches), len(old)))
    return batches


def fmt(ts):
    return time.strftime('%Y%m%d%H%M%S +0000', time.gmtime(ts))


def write_xml(batches, path, now):
    chans = {}
    for b in sorted(batches, key=lambda x: x.get('t', 0)):     # the newest upload of a channel wins
        for c in b.get('ch') or []:
            evs = [e for e in c.get('e') or [] if e[0] + e[1] > now - 6 * 3600]
            titles = ' '.join(e[2] for e in evs)
            if len(evs) < 4 or len(AR.findall(titles)) < 0.3 * len(re.sub(r'\W', '', titles)):
                continue
            chans['crowd.' + c['k']] = (c.get('n') or c['k'], evs)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="SaadEPG receiver">\n')
        for cid, (name, _e) in sorted(chans.items()):
            f.write('<channel id="%s"><display-name>%s</display-name></channel>\n' % (html.escape(cid), html.escape(name)))
        for cid, (_n, evs) in sorted(chans.items()):
            for e in evs:
                st, dur, t = int(e[0]), int(e[1]), e[2]
                f.write('<programme start="%s" stop="%s" channel="%s"><title lang="ar">%s</title>%s%s</programme>\n' % (
                    fmt(st), fmt(st + dur), html.escape(cid), html.escape(t),
                    ('<sub-title lang="ar">%s</sub-title>' % html.escape(e[3])) if len(e) > 3 and e[3] else '',
                    ('<desc lang="ar">%s</desc>' % html.escape(e[4])) if len(e) > 4 and e[4] else ''))
        f.write('</tv>\n')
    return len(chans)


def main():
    work, tr = sys.argv[1], sys.argv[2]
    token, repo = os.environ.get('GITHUB_TOKEN'), os.environ.get('GITHUB_REPOSITORY', '')
    if not token or '/' not in repo:
        print('crowd: no token / repository - skipped')
        return
    now = time.time()
    num = find_issue(repo, token)
    with open(os.path.join(work, 'crowd_issue.json'), 'w') as f:
        json.dump({'issue': num}, f)
    batches = read_batches(repo, num, token, repo.split('/')[0], now)
    n = write_xml(batches, os.path.join(work, 'crowd_ar.xml'), now)
    print('crowd: %d Arabic channels from the receiver\'s satellite guide' % n)
    wanted = []
    for b in batches:
        for name in b.get('missing') or []:
            if name not in wanted:
                wanted.append(name)
    with open(os.path.join(tr, 'wanted.json'), 'w', encoding='utf-8') as f:
        json.dump(wanted, f, ensure_ascii=False)


if __name__ == '__main__':
    main()
