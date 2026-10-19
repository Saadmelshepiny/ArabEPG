# -*- coding: utf-8 -*-
# Online schedule grabbers for Arabic channels (Python 2 and 3 compatible).
#
# Every grabber returns {site_id: [event, ...]} where an event is a tuple
#   (start_utc_ts, duration_sec, title, short_text, long_text, genre_nibble)
from __future__ import absolute_import, print_function

import binascii
import calendar
import gzip
import json
import re
import ssl
import time
from io import BytesIO

try:  # Python 3
    from urllib.request import Request, urlopen
    from urllib.error import URLError
    from urllib.parse import urlencode, quote
    from html import unescape as _unescape
    PY3 = True
    text_type = str
except ImportError:  # Python 2
    from urllib2 import Request, urlopen, URLError
    from urllib import urlencode, quote
    from HTMLParser import HTMLParser
    _unescape = HTMLParser().unescape
    PY3 = False
    text_type = unicode  # noqa: F821

from . import aes

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36')

LOG = []


_REPEAT = {'err': None, 'n': 0}


def log(msg):
    msg = u(msg)
    err = msg.split(': ', 1)[-1] if ': ' in msg and ('Error' in msg or 'error' in msg) else None
    if err and err == _REPEAT['err']:
        _REPEAT['n'] += 1     # same failure again (e.g. no internet): count it instead of flooding the log
        return
    if _REPEAT['n']:
        n, _REPEAT['n'] = _REPEAT['n'], 0
        log(u'(تكرر نفس الخطأ %d مرة)' % n)
    _REPEAT['err'] = err
    line = u(time.strftime('%H:%M:%S ')) + msg
    LOG.append(line)
    del LOG[:-400]
    try:
        with open('/tmp/ArabEPG.log', 'ab') as f:
            f.write((line + u'\n').encode('utf-8'))
    except Exception:
        pass
    try:
        print('[ArabEPG] ' + (msg if PY3 else msg.encode('utf-8')))
    except Exception:
        pass


def u(s):
    if s is None:
        return u''
    if isinstance(s, bytes):
        return s.decode('utf-8', 'replace')
    return s if isinstance(s, text_type) else text_type(s)


# --------------------------------------------------------------------- HTTP
_ctx_unverified = None

# DNS cache: on many receivers the resolver fails for minutes at a time ("Name or service not known").
# Remember every successful lookup and fall back to it when a new lookup fails.
import socket as _socket
_orig_getaddrinfo = _socket.getaddrinfo
_DNS = {}
NET = {'dns_fail': 0}


def _cached_getaddrinfo(host, port, *args, **kw):
    key = (host, port) + tuple(args)
    try:
        res = _orig_getaddrinfo(host, port, *args, **kw)
        _DNS[key] = res
        NET['dns_fail'] = 0
        return res
    except _socket.gaierror:
        if key in _DNS:
            return _DNS[key]
        NET['dns_fail'] += 1
        raise


if getattr(_socket.getaddrinfo, '__name__', '') != '_cached_getaddrinfo':
    _socket.getaddrinfo = _cached_getaddrinfo


def network_ok(host='www.google.com'):
    try:
        _socket.getaddrinfo(host, 443)
        return True
    except Exception:
        return False


def wait_network(stop=None, tries=6, delay=10):
    """after DNS errors: wait for the connection to come back (max ~1 minute)."""
    for _i in range(tries):
        if network_ok():
            return True
        if stop and stop():
            return False
        time.sleep(delay)
    return network_ok()


def _open(url, headers=None, timeout=30, data=None):
    global _ctx_unverified
    h = {'User-Agent': UA, 'Accept-Encoding': 'gzip', 'Accept-Language': 'ar,en;q=0.8'}
    if headers:
        h.update(headers)
    if data is not None and not isinstance(data, bytes):
        data = urlencode(data).encode('utf-8')
    req = Request(url, data=data, headers=h)
    try:
        return urlopen(req, timeout=timeout)
    except (URLError, ssl.SSLError) as e:
        reason = str(getattr(e, 'reason', e))
        if 'CERTIFICATE' not in reason.upper() and 'SSL' not in reason.upper():
            raise
        if _ctx_unverified is None:
            _ctx_unverified = ssl._create_unverified_context()
        return urlopen(req, timeout=timeout, context=_ctx_unverified)


def http_get(url, headers=None, timeout=30, data=None):
    """GET (or POST when data is given) and return bytes, gunzipped when needed.
    Falls back to an unverified TLS context because many receivers ship an outdated CA bundle."""
    resp = _open(url, headers, timeout, data)
    data = resp.read()
    if resp.info().get('Content-Encoding', '') == 'gzip' or data[:2] == b'\x1f\x8b':
        data = gzip.GzipFile(fileobj=BytesIO(data)).read()
    return data


def http_download(url, path, timeout=60, stop=None):
    """stream a (possibly big) file to disk without holding it in memory."""
    resp = _open(url, {'Accept-Encoding': 'identity'}, timeout)
    size = 0
    with open(path, 'wb') as f:
        while True:
            if stop and stop():
                raise IOError('cancelled')
            chunk = resp.read(65536)
            if not chunk:
                break
            f.write(chunk)
            size += len(chunk)
    return size


def open_maybe_gz(path):
    f = open(path, 'rb')
    magic = f.read(2)
    f.seek(0)
    if magic == b'\x1f\x8b':
        return gzip.GzipFile(fileobj=f)
    return f


# --------------------------------------------------------------------- helpers
_GENRES = [
    (0x10, (u'فيلم', u'أفلام', u'افلام', u'movie', u'film', u'cinema', u'سينما')),
    (0x15, (u'مسلسل', u'series', u'drama', u'دراما', u'episode', u'حلقة')),
    (0x20, (u'أخبار', u'اخبار', u'news', u'نشرة', u'موجز')),
    (0x40, (u'رياض', u'sport', u'مباراة', u'كرة', u'football', u'match')),
    (0x50, (u'أطفال', u'اطفال', u'كرتون', u'رسوم', u'kids', u'cartoon', u'anim')),
    (0x60, (u'موسيق', u'أغان', u'اغان', u'music', u'song', u'حفل')),
    (0x90, (u'وثائقي', u'documentar', u'تعليم', u'علوم')),
    (0x30, (u'برنامج', u'show', u'talk', u'منوع', u'حوار', u'entertainment')),
    (0x70, (u'دين', u'قرآن', u'قران', u'religio', u'islam')),
]


def genre_nibble(*texts):
    t = u' '.join(u(x) for x in texts if x).lower()
    if not t:
        return 0
    for nib, keys in _GENRES:
        for k in keys:
            if k in t:
                return nib
    return 0


def clean(s):
    s = _unescape(re.sub(r'<[^>]+>', ' ', u(s)))
    return re.sub(r'\s+', ' ', s).strip()


def day_starts(days):
    """Local midnight timestamps for today + following days."""
    lt = time.localtime()
    t0 = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    out = []
    for i in range(days):
        tt = time.localtime(t0 + i * 86400 + 7200)  # +2h guards DST edges
        out.append(int(time.mktime((tt.tm_year, tt.tm_mon, tt.tm_mday, 0, 0, 0, 0, 0, -1))))
    return out


def iso_to_ts(s):
    """2023-11-11T00:00:00.000+00:00 / ...Z -> UTC timestamp"""
    m = re.match(r'(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d)(?::(\d\d))?(?:\.\d+)?(Z|[+-]\d\d:?\d\d)?', s or '')
    if not m:
        return None
    y, mo, d, hh, mi, ss = [int(x or 0) for x in m.groups()[:6]]
    ts = calendar.timegm((y, mo, d, hh, mi, ss, 0, 0, 0))
    tz = m.group(7)
    if tz and tz != 'Z':
        sign = -1 if tz[0] == '-' else 1
        tz = tz[1:].replace(':', '')
        ts -= sign * (int(tz[:2]) * 3600 + int(tz[2:]) * 60)
    return ts


def finish(events):
    """sort, drop duplicates/overlaps and fill missing durations."""
    events = sorted(set(events), key=lambda e: e[0])
    out = []
    for i, e in enumerate(events):
        start, dur = e[0], e[1]
        nxt = events[i + 1][0] if i + 1 < len(events) else None
        if (not dur or dur <= 0) and nxt:
            dur = nxt - start
        if nxt and dur > nxt - start > 0:
            dur = nxt - start
        if not dur or dur <= 0:
            dur = 1800
        if out and out[-1][0] == start:
            continue
        out.append((start, int(min(dur, 6 * 3600))) + tuple(e[2:]))
    return out


def ev(start, dur, title, short=u'', longd=u'', genre=0):
    return (int(start), int(dur or 0), clean(title)[:250], clean(short)[:250], clean(longd)[:2000], int(genre))


# ===================================================================== Shahid (MBC group)
SHAHID_URL = ('https://api2.shahid.net/proxy/v2.1/shahid-epg-api/?csvChannelIds=%s'
              '&from=%sT00:00:00.000Z&to=%sT23:59:59.999Z&country=%s&language=%s&Accept-Language=%s')


def grab_shahid(ids, days, lang='ar', country='EG', stop=None):
    res = dict((i, []) for i in ids)
    ids = list(ids)
    dates = []
    t = time.time() - 86400  # yesterday (UTC) covers late-night local programmes
    for i in range(days + 1):
        dates.append(time.strftime('%Y-%m-%d', time.gmtime(t + i * 86400)))
    for k in range(0, len(ids), 10):
        chunk = ids[k:k + 10]
        for d in dates:
            if stop and stop():
                return res
            url = SHAHID_URL % (','.join(chunk), d, d, country, lang, lang)
            try:
                data = json.loads(http_get(url, {'Referer': 'https://shahid.mbc.net/',
                                                 'Origin': 'https://shahid.mbc.net',
                                                 'Accept': 'application/json'}).decode('utf-8'))
            except Exception as e:
                log('Shahid %s %s: %s' % (','.join(chunk), d, e))
                continue
            for block in data.get('items') or []:
                cid = u(block.get('channelId'))
                if cid not in res:
                    continue
                for it in block.get('items') or []:
                    if it.get('emptySlot'):
                        continue
                    st = iso_to_ts(it.get('actualFrom') or it.get('from'))
                    en = iso_to_ts(it.get('actualTo') or it.get('to'))
                    if not st:
                        continue
                    genres = it.get('genres') or []
                    if not isinstance(genres, list):
                        genres = [genres]
                    short = u' | '.join(u(g) for g in genres if g)
                    ep = it.get('episodeNumber')
                    se = it.get('seasonNumber')
                    if ep:
                        short = (u'الموسم %s الحلقة %s' % (se, ep) if se else u'الحلقة %s' % ep) + \
                            (u' - ' + short if short else u'')
                    res[cid].append(ev(st, (en - st) if en else 0, it.get('title'), short,
                                       it.get('description'), genre_nibble(short, it.get('title'))))
    for k in res:
        res[k] = finish(res[k])
    return res


# ===================================================================== OSN
_OSN_KEY = bytearray(binascii.unhexlify('65a04b9b5591f27c837fac433274b494403e0eec5b43698060c28e1162d4460f'))
_OSN_IV = bytearray(binascii.unhexlify('b7cc0a48d6d023bc1a2a670953ec5622'))
OSN_URL = 'https://www.osn.com/apidata/tv-schedule-timeline?t=batch%d-time%d-%d-boxAndroid'


def osn_decode(raw):
    data = json.loads(u(raw))
    if isinstance(data, dict) and data.get('encrypted'):
        data = json.loads(aes.decrypt_b64(data['encrypted'], _OSN_KEY, _OSN_IV))
    return data


def _loc(obj, lang):
    if isinstance(obj, dict):
        return obj.get(lang) or obj.get('en') or obj.get('ar') or u''
    return u(obj)


def osn_parse(data, wanted, lang, res):
    for entry in (data or {}).get('entries') or []:
        gid = u(entry.get('guid'))
        if gid not in wanted:
            continue
        for li in entry.get('listings') or []:
            p = li.get('program') or {}
            st, en = li.get('startTime'), li.get('endTime')
            try:
                st, en = int(st) // 1000, int(en) // 1000
            except Exception:
                st, en = iso_to_ts(u(st)), iso_to_ts(u(en))
            if not st:
                continue
            title = _loc(p.get('titleLocalized'), lang) or p.get('title')
            series = _loc(p.get('osnprogram$seriesTitleLocalized'), lang)
            sub = u''
            if series:
                sub, title = title, series
            tags = [_loc(t.get('titleLocalized'), lang) for t in (p.get('tags') or []) if isinstance(t, dict)]
            short = []
            if p.get('tvSeasonNumber') or p.get('tvSeasonEpisodeNumber'):
                short.append(u'م%s ح%s' % (p.get('tvSeasonNumber') or '-', p.get('tvSeasonEpisodeNumber') or '-'))
            if sub:
                short.append(sub)
            if tags:
                short.append(u' / '.join(x for x in tags if x))
            if p.get('year'):
                short.append(u'(%s)' % p.get('year'))
            desc = _loc(p.get('descriptionLocalized'), lang) or p.get('description')
            res.setdefault(gid, []).append(
                ev(st, (en - st) if en else 0, title, u' - '.join(short), desc, genre_nibble(u' '.join(tags), title)))


def grab_osn(ids, days, lang='ar', stop=None):
    res = dict((i, []) for i in ids)
    ids = list(ids)
    wanted = set(ids)
    for d0 in day_starts(days):
        st_ms, en_ms = d0 * 1000, (d0 + 86400) * 1000
        n = 0
        for k in range(0, len(ids), 5):  # the API accepts at most 5 channels per call
            if stop and stop():
                return res
            n += 1
            payload = json.dumps({'channelGuid': '|'.join(ids[k:k + 5]), 'startTime': st_ms,
                                  'endTime': en_ms}, separators=(',', ':'))
            hdr = {'X-Encrypted-Data': aes.encrypt_b64(payload, _OSN_KEY, _OSN_IV),
                   'Referer': 'https://www.osn.com/', 'Accept': 'application/json'}
            try:
                osn_parse(osn_decode(http_get(OSN_URL % (n, st_ms, en_ms), hdr)), wanted, lang, res)
            except Exception as e:
                log('OSN batch %d: %s' % (n, e))
            time.sleep(1.5)  # avoid HTTP 406 throttling
    for k in res:
        res[k] = finish(res[k])
    return res


# ===================================================================== elCinema
_AR_MONTHS = {
    u'يناير': 1, u'فبراير': 2, u'مارس': 3, u'ابريل': 4, u'أبريل': 4, u'إبريل': 4, u'مايو': 5,
    u'يونيو': 6, u'يونيه': 6, u'يوليو': 7, u'يوليه': 7, u'اغسطس': 8, u'أغسطس': 8, u'سبتمبر': 9,
    u'اكتوبر': 10, u'أكتوبر': 10, u'نوفمبر': 11, u'ديسمبر': 12,
    u'january': 1, u'february': 2, u'march': 3, u'april': 4, u'may': 5, u'june': 6, u'july': 7,
    u'august': 8, u'september': 9, u'october': 10, u'november': 11, u'december': 12}

ELCINEMA_URL = 'https://elcinema.com/tvguide/%s/'


def _local_ts(y, mo, d, hh, mi, offset_min):
    if offset_min is None:  # page time == receiver local time
        return int(time.mktime((y, mo, d, hh, mi, 0, 0, 0, -1)))
    return calendar.timegm((y, mo, d, hh, mi, 0, 0, 0, 0)) - offset_min * 60


def elcinema_parse(html, offset_min=None, now=None):
    html = u(html)
    now = now or time.time()
    cur = time.localtime(now)
    events = []
    heads = list(re.finditer(r'class="\s*dates"\s*>\s*([^<]+?)\s*<', html))
    for hi, hm in enumerate(heads):
        seg = html[hm.end(): heads[hi + 1].start() if hi + 1 < len(heads) else len(html)]
        mday = re.search(r'(\d{1,2})\s+(\S+)', hm.group(1))
        if not mday:
            continue
        day, mon = int(mday.group(1)), _AR_MONTHS.get(mday.group(2).strip().lower())
        if not mon:
            continue
        year = cur.tm_year
        if mon - cur.tm_mon > 6:
            year -= 1
        elif cur.tm_mon - mon > 6:
            year += 1
        prev_min, day_shift = -1, 0
        for item in re.split(r'class="[^"]*padded-half[^"]*"', seg)[1:]:
            tm = re.search(r'<li>\s*(\d{1,2}):(\d{2})\s*([^<\[]*)', item)
            if not tm:
                continue
            hh, mi, ampm = int(tm.group(1)), int(tm.group(2)), tm.group(3).strip().lower()
            if ampm.startswith(u'م') or ampm.startswith('pm'):
                if hh < 12:
                    hh += 12
            elif (ampm.startswith(u'ص') or ampm.startswith('am')) and hh == 12:
                hh = 0
            minutes = hh * 60 + mi
            if minutes < prev_min - 60:  # list crossed midnight
                day_shift += 1
            prev_min = minutes
            st = _local_ts(year, mon, day, hh, mi, offset_min) + day_shift * 86400
            dm = re.search(r'\[\s*(\d+)', item)
            dur = int(dm.group(1)) * 60 if dm else 0
            t = re.search(r'<a href="[^"]*/work/\d+/?"[^>]*>\s*([^<]+?)\s*</a>', item)
            title = t.group(1) if t else u''
            info = u''
            if t:
                im = re.search(r'</a>\s*</li>\s*<li>\s*([^<]+)</li>', item[t.end() - 10:])
                info = clean(im.group(1)) if im else u''
            if not title:
                lis = [clean(x) for x in re.findall(r'<li[^>]*>(.*?)</li>', item, re.S)]
                lis = [x for x in lis if x and not re.match(r'^[\d:\s]+', x) and not x.startswith('[')]
                title = lis[0] if lis else u''
            if not title:
                continue
            desc = u''
            dm2 = re.search(r'<li>([^<]*)<a[^>]*read-more[^>]*>[^<]*</a>\s*<span[^>]*>([^<]*)</span>', item)
            if dm2:
                desc = dm2.group(1) + dm2.group(2)
            else:
                dm3 = re.search(r'large-6.*?<li>\s*([^<]{25,})</li>', item, re.S)
                desc = dm3.group(1) if dm3 else u''
            cast = re.findall(r'<a href="[^"]*/person/\d+/?"[^>]*>([^<]+)</a>', item)
            if cast:
                desc = (desc + u'\n' if desc else u'') + u'بطولة: ' + u'، '.join(clean(c) for c in cast[:6])
            events.append(ev(st, dur, title, info, desc, genre_nibble(info, title)))
    return finish(events)


def grab_elcinema(ids, days, offset_min=None, stop=None, workers=1):
    """one page per channel holds the whole week; fetched with a few parallel workers."""
    import threading
    res = {}
    limit = time.time() + days * 86400
    lower = time.time() - 6 * 3600
    queue = list(ids)
    lock = threading.Lock()

    def worker():
        while True:
            with lock:
                if not queue or (stop and stop()):
                    return
                sid = queue.pop(0)
            evs = []
            for attempt in (1, 2):
                try:
                    evs = elcinema_parse(http_get(ELCINEMA_URL % sid, timeout=25), offset_min)
                    break
                except Exception as e:
                    if attempt == 2:
                        log('elCinema %s: %s' % (sid, e))
                    time.sleep(2)
            with lock:
                res[sid] = [e for e in evs if e[0] < limit and e[0] + e[1] > lower]
            time.sleep(0.3)

    threads = [threading.Thread(target=worker) for _ in range(max(1, min(workers, len(queue))))]
    for t in threads:
        t.daemon = True
        t.start()
    for t in threads:
        t.join()
    return res


# ===================================================================== generic XMLTV (epgshare01, etc.)
def _xmltv_time(s):
    m = re.match(r'(\d{4})(\d\d)(\d\d)(\d\d)(\d\d)(\d\d)?\s*([+-]\d{4})?', s or '')
    if not m:
        return None
    y, mo, d, hh, mi, ss = [int(x or 0) for x in m.groups()[:6]]
    ts = calendar.timegm((y, mo, d, hh, mi, ss, 0, 0, 0))
    if m.group(7):
        z = m.group(7)
        ts -= (1 if z[0] == '+' else -1) * (int(z[1:3]) * 3600 + int(z[3:5]) * 60)
    return ts


def _episode(el):
    for e in el.findall('episode-num'):
        t = (e.text or '').strip()
        if not t:
            continue
        if e.get('system') == 'xmltv_ns':
            p = t.split('.')
            try:
                se = int(p[0].split('/')[0]) + 1 if p[0].strip() else None
                ep = int(p[1].split('/')[0]) + 1 if len(p) > 1 and p[1].strip() else None
            except ValueError:
                continue
            if ep:
                return (u'S%d E%d' % (se, ep)) if se else (u'E%d' % ep)
        else:
            return u(t)
    return u''


def parse_xmltv_file(path, wanted, days, stop=None, names_only=False):
    """stream-parse an XMLTV file (plain or gz) from disk.
    -> ({channel_id: events}, {channel_id: [display names]})"""
    import xml.etree.ElementTree as ET
    names, res = {}, {}
    limit = time.time() + days * 86400
    lower = time.time() - 6 * 3600
    f = open_maybe_gz(path)
    try:
        for _evt, el in ET.iterparse(f, events=('end',)):
            if el.tag == 'channel':
                names[u(el.get('id'))] = [u(d.text) for d in el.findall('display-name') if d.text]
                el.clear()
            elif el.tag == 'programme':
                if names_only:
                    el.clear()
                    continue
                if stop and stop():
                    break
                cid = u(el.get('channel'))
                if wanted is None or cid in wanted:
                    st, en = _xmltv_time(el.get('start')), _xmltv_time(el.get('stop'))
                    if st and lower < (en or st) and st < limit:
                        title = el.findtext('title') or u''
                        sub = el.findtext('sub-title') or u''
                        desc = el.findtext('desc') or u''
                        cats = u' / '.join(c.text for c in el.findall('category') if c.text)
                        epi = _episode(el)
                        short = u' - '.join(x for x in (epi, sub, cats) if x)
                        res.setdefault(cid, []).append(ev(st, (en - st) if en else 0, title, short, desc,
                                                          genre_nibble(cats, title)))
                el.clear()
    finally:
        f.close()
    for k in res:
        res[k] = finish(res[k])
    return res, names


def grab_xmltv(url, wanted, days, stop=None, tmp='/tmp/ArabEPG_dl.xml'):
    """download an XMLTV url to disk, then stream-parse it (local paths are read directly)."""
    import os
    if url.startswith('file://'):
        url = url[7:]
    if url.startswith('/'):
        return parse_xmltv_file(url, wanted, days, stop)
    try:
        http_download(url, tmp, timeout=120, stop=stop)
        return parse_xmltv_file(tmp, wanted, days, stop)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def arabic_ratio(events):
    """share of programmes whose title is written in Arabic."""
    n = a = 0
    for e in events or ():
        t = e[2]
        ar = len(re.findall(u'[؀-ۿ]', t))
        la = len(re.findall(u'[A-Za-z]', t))
        if ar or la:
            n += 1
            a += 1 if ar >= la else 0
    return float(a) / n if n else 0.0


# ===================================================================== beIN (also Alkass / Baraem)
BEIN_URL = ('https://www.bein.com/%s/epg-ajax-template/?action=epg_fetch&category=%s&cdate=%s&language=%s'
            '&loadindex=0&mins=00&offset=0&postid=%s&serviceidentity=bein.net')
QATAR = 3 * 3600


def bein_parse(html, day_ts, wanted=None):
    """day_ts: UTC midnight of the requested date. Times on the page are Qatar time and
    the list starts the evening before. -> {channel number: events}"""
    html = u(html)
    out = {}
    heads = list(re.finditer(r"id=channels_(\d+)", html))
    for i, h in enumerate(heads):
        cid = h.group(1)
        if wanted is not None and cid not in wanted:
            continue
        seg = html[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(html)]
        ul = re.search(r'<ul[^>]*>(.*?)</ul>', seg, re.S)
        if not ul:
            continue
        base = day_ts - 86400 - QATAR
        prev = None
        evs = []
        for li in re.split(r'<li\s', ul.group(1))[1:]:
            t = re.search(r'class=title>([^<]*)<', li)
            tm = re.search(r'class=time>(\d\d):(\d\d)(?:&nbsp;|\s)*-(?:&nbsp;|\s)*(\d\d):(\d\d)', li)
            if not t or not tm or not t.group(1).strip():
                continue
            fm = re.search(r'class=format>([^<]*)<', li)
            h1, m1, h2, m2 = [int(x) for x in tm.groups()]
            st = base + h1 * 3600 + m1 * 60
            if prev is not None and st < prev:
                st += 86400
                base += 86400
            en = base + h2 * 3600 + m2 * 60
            if en <= st:
                en += 86400
            prev = st
            cat = clean(fm.group(1)) if fm else u''
            evs.append(ev(st, en - st, t.group(1), cat, u'', genre_nibble(cat, t.group(1))))
        out[cid] = evs
    return out


def grab_bein(keys, days, lang='ar', stop=None):
    """keys: 'category#number' (sports#1, entertainment#3 ...)."""
    groups = {}
    for k in keys:
        cat, _, num = k.partition('#')
        groups.setdefault(cat, {})[num] = k
    res = dict((k, []) for k in keys)
    lng = 'ar' if lang == 'ar' else 'en'
    postid = '25344' if lng == 'ar' else '25356'
    for cat, nums in groups.items():
        for d0 in day_starts(days):
            if stop and stop():
                return res
            lt = time.localtime(d0 + 7200)
            date = time.strftime('%Y-%m-%d', lt)
            day_utc = calendar.timegm((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, 0))
            try:
                page = http_get(BEIN_URL % (lng, cat, date, lng.upper(), postid), timeout=40)
                for num, evs in bein_parse(page, day_utc, set(nums)).items():
                    res[nums[num]].extend(evs)
            except Exception as e:
                log('beIN %s %s: %s' % (cat, date, e))
    for k in res:
        res[k] = finish(res[k])
    return res


# ===================================================================== Rotana
ROTANA_URL = 'https://rotana.net/%s/streams?channel=%s&tz='
RIYADH = 3 * 3600


def rotana_parse(html):
    html = u(html)
    events, day = [], None
    for m in re.finditer(r'class="bg" id="item-(\d\d)-(\d\d)-(\d{4})"|'
                         r'<h5[^>]*big-title[^>]*>\s*<span>\s*(\d\d):(\d\d)\s*</span>\s*<span>(.*?)</span>', html, re.S):
        if m.group(1):
            day = (int(m.group(3)), int(m.group(2)), int(m.group(1)))
        elif day:
            st = calendar.timegm(day + (int(m.group(4)), int(m.group(5)), 0, 0, 0, 0)) - RIYADH
            events.append(ev(st, 0, m.group(6), u'', u'', genre_nibble(m.group(6))))
    return finish(events)


def grab_rotana(ids, days, lang='ar', stop=None):
    res = {}
    lng = 'ar' if lang == 'ar' else 'en'
    limit = time.time() + days * 86400
    for sid in ids:
        if stop and stop():
            break
        try:
            res[sid] = [e for e in rotana_parse(http_get(ROTANA_URL % (lng, sid), timeout=30))
                        if e[0] < limit and e[0] + e[1] > time.time() - 6 * 3600]
        except Exception as e:
            log('Rotana %s: %s' % (sid, e))
            res[sid] = []
    return res


# ===================================================================== Al Jazeera
AJ_URL = ('https://www.aljazeera.com/graphql?wp-site=%s&operationName=ArchipelagoSchedulePageQuery'
          '&variables=%s&extensions=%%7B%%7D')


def aljazeera_parse(raw):
    data = json.loads(u(raw))
    sched = (((data or {}).get('data') or {}).get('post') or {}).get('schedule') or []
    events = []
    for it in sched:
        try:
            day = int(it.get('startDate'))
            hh, mi = [int(x) for x in (it.get('showTimeslot') or '0:0').split(':')[:2]]
            p = [int(x) for x in (it.get('duration') or '0:0').split(':')]
            dur = p[0] * 3600 + p[1] * 60 + (p[2] if len(p) > 2 else 0)
        except (TypeError, ValueError):
            continue
        events.append(ev(day + hh * 3600 + mi * 60, dur, it.get('showName'), u'', it.get('showDescription'), 0x20))
    return finish(events)


def grab_aljazeera(ids, days, stop=None):
    res = {}
    limit = time.time() + days * 86400
    for sid in ids:
        site, _, suffix = sid.partition('#')
        var = quote(json.dumps({'postName': 'schedule-' + suffix if suffix else 'schedule', 'preview': ''},
                               separators=(',', ':')))
        try:
            raw = http_get(AJ_URL % (site, var), {'wp-site': site}, timeout=30)
            res[sid] = [e for e in aljazeera_parse(raw) if e[0] < limit and e[0] + e[1] > time.time() - 6 * 3600]
        except Exception as e:
            log('Al Jazeera %s: %s' % (sid, e))
            res[sid] = []
    return res


# ===================================================================== Roya (Jordan)
def grab_roya(ids, days, stop=None):
    wanted = set(str(i) for i in ids)
    res = dict((i, []) for i in ids)
    for d in range(days):
        if stop and stop():
            break
        try:
            data = json.loads(http_get('https://backend.roya.tv/api/v01/channels/schedule-pagination?day_number=%d' % d,
                                       timeout=30).decode('utf-8'))
        except Exception as e:
            log(u'Roya: %s' % e)
            continue
        for day in data.get('data') or []:
            for ch in day.get('channel') or []:
                cid = str(ch.get('id'))
                if cid not in wanted:
                    continue
                for p in ch.get('programs') or []:
                    try:
                        st, en = int(p['start_timestamp']), int(p['end_timestamp'])
                    except (KeyError, TypeError, ValueError):
                        continue
                    res[cid].append(ev(st, en - st, p.get('name'), u'', p.get('description'), genre_nibble(p.get('name'))))
    for k in res:
        res[k] = finish(res[k])
    return res


# ===================================================================== ART (Arab Radio and Television)
RIYADH_TZ = 3 * 3600


def art_parse(raw):
    try:
        items = json.loads(u(raw)) if raw else []
    except ValueError:
        log(u'ART: الموقع لم يرجع بيانات (ربما تغيّر شكل الصفحة)')
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        try:
            mo, d, y = [int(x) for x in re.match(r'(\d+)/(\d+)/(\d+)', it['adddate']).groups()]
            hh, mi = [int(x) for x in it['start_Time'].split(':')[:2]]
            p = [int(x) for x in it['duration'].split(':')]
            dur = p[0] * 3600 + p[1] * 60 + (p[2] if len(p) > 2 else 0)
        except Exception:
            continue
        st = calendar.timegm((y, mo, d, hh, mi, 0, 0, 0, 0)) - RIYADH_TZ
        short = u''
        if it.get('season_Number') or it.get('epoisode_Number'):
            short = u'الموسم %s الحلقة %s' % (it.get('season_Number') or '-', it.get('epoisode_Number') or '-')
        out.append(ev(st, dur, it.get('title'), short, it.get('description') or it.get('short_description'),
                      genre_nibble(it.get('title'), short)))
    return out


def grab_art(ids, days, stop=None):
    res = {}
    for sid in ids:
        suffix = sid.split('#', 1)[1] if '#' in sid else ''
        evs = []
        for d in range(days):
            if stop and stop():
                break
            try:
                evs.extend(art_parse(http_get('https://www.artonline.tv/Home/Tvlist%s' % suffix,
                                              {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                                               'X-Requested-With': 'XMLHttpRequest',
                                               'Referer': 'https://www.artonline.tv/Home/Tv',
                                               'Accept': 'application/json, text/javascript, */*; q=0.01'},
                                              timeout=30, data={'objId': str(d)})))
            except Exception as e:
                log(u'ART %s: %s' % (sid, e))
                break
        res[sid] = finish(evs)
    return res


# ===================================================================== Oman TV (ayn.om)
MUSCAT = 4 * 3600
_AR_DAYS = {u'السبت': 5, u'الأحد': 6, u'الاحد': 6, u'الإثنين': 0, u'الاثنين': 0, u'الثلاثاء': 1,
            u'الأربعاء': 2, u'الاربعاء': 2, u'الخميس': 3, u'الجمعة': 4}


def ayn_parse(html, now=None):
    html = u(html)
    now = now or time.time()
    today = time.gmtime(now + MUSCAT)
    base = calendar.timegm((today.tm_year, today.tm_mon, today.tm_mday, 0, 0, 0, 0, 0, 0)) - MUSCAT
    out = []
    parts = re.split(r'class="epg_timeline_aside"', html)
    for part in parts[1:]:
        h = re.search(r'<h3>\s*([^<]+?)\s*</h3>', part)
        if not h:
            continue
        label = h.group(1).strip()
        if label == u'اليوم':
            off = 0
        elif label in _AR_DAYS:
            off = (_AR_DAYS[label] - today.tm_wday) % 7
        else:
            continue
        for m in re.finditer(r'class="epg_tl_item[^"]*"\s*title="([^"]+)".*?<p>\s*(\d\d):(\d\d)', part, re.S):
            title = _unescape(m.group(1))
            if u'لا يوجد جدول' in title:
                continue
            h1, m1 = int(m.group(2)), int(m.group(3))
            out.append(ev(base + off * 86400 + h1 * 3600 + m1 * 60, 0, title, u'', u'', genre_nibble(title)))
    return finish(out)


def grab_ayn(ids, days, stop=None):
    res = {}
    limit = time.time() + days * 86400
    for sid in ids:
        if stop and stop():
            break
        try:
            num, _, slug = sid.partition('/')
            url = 'https://ayn.om/schedule/%s/%s' % (num, quote(slug.encode('utf-8') if not PY3 else slug))
            res[sid] = [e for e in ayn_parse(http_get(url, timeout=30)) if e[0] < limit]
        except Exception as e:
            log(u'Oman TV %s: %s' % (sid, e))
            res[sid] = []
    return res


# ===================================================================== SNRT Morocco (snrt.ma, official)
CASABLANCA = 3600   # Morocco keeps UTC+1 (it moves to UTC+0 during Ramadan only)


def snrt_parse(html):
    """one page holds several days: <div class="... grille-line 20250113 ..."> 12H00 ... program-title-sm"""
    html = u(html)
    out = []
    for part in re.split(r'(?=<div[^>]+class="[^"]*grille-line)', html)[1:]:
        d = re.search(r'grille-line\s+(\d{4})(\d\d)(\d\d)', part)
        t = re.search(r'grille-time.*?(\d{1,2})\s*[Hh:]\s*(\d\d)', part, re.S)
        title = re.search(r'program-title-sm[^>]*>\s*(.*?)\s*</', part, re.S)
        if not (d and t and title):
            continue
        desc = re.search(r'program-description-sm[^>]*>\s*(.*?)\s*</', part, re.S)
        start = calendar.timegm((int(d.group(1)), int(d.group(2)), int(d.group(3)),
                                 int(t.group(1)), int(t.group(2)), 0, 0, 0, 0)) - CASABLANCA
        name = re.sub(r'\s+', u' ', _unescape(re.sub(r'<[^>]+>', u'', title.group(1)))).strip()
        if name:
            out.append(ev(start, 0, name, u'', _unescape(re.sub(r'<[^>]+>', u'', desc.group(1))).strip() if desc else u'',
                          genre_nibble(name)))
    return finish(out)


def grab_snrt(ids, days, stop=None):
    res = {}
    limit = time.time() + days * 86400
    for sid in ids:
        if stop and stop():
            break
        try:
            res[sid] = [e for e in snrt_parse(http_get('https://www.snrt.ma/ar/node/%s' % sid, timeout=30))
                        if e[0] < limit]
        except Exception as e:
            log(u'SNRT %s: %s' % (sid, e))
            res[sid] = []
    return res


# ===================================================================== beIN SPORTS (beinsports.com, MENA)
def grab_beinsports(ids, days, stop=None):
    res = dict((i, []) for i in ids)
    t0 = calendar.timegm(time.gmtime()[:3] + (0, 0, 0, 0, 0, 0)) - 86400
    fmt = '%Y-%m-%dT%H:%M:%S.000Z'
    for k in range(0, len(ids)):
        if stop and stop():
            break
        part = ids[k:k + 1]          # the API answers 400 when several channels are asked at once
        url = 'https://www.beinsports.com/api/opta/tv-event?&startBefore=%s&endAfter=%s&channelIds=%s' % (
            time.strftime(fmt, time.gmtime(t0 + (days + 1) * 86400)), time.strftime(fmt, time.gmtime(t0)), ','.join(part))
        try:
            data = json.loads(http_get(url, timeout=40).decode('utf-8'))
        except Exception as e:
            log(u'beIN SPORTS: %s' % e)
            continue
        for r in data.get('rows') or []:
            cid = r.get('channelId')
            if cid not in res:
                continue
            st, en = iso_to_ts(r.get('startDate')), iso_to_ts(r.get('endDate'))
            if not st:
                continue
            sub = ((r.get('data') or {}).get('title')) or u''
            res[cid].append(ev(st, (en - st) if en else 0, r.get('title'), sub, r.get('description'), 0x40))
    for k in res:
        res[k] = finish(res[k])
    return res
