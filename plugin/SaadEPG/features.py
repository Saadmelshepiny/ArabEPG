# -*- coding: utf-8 -*-
# Saad EPG - what is built on top of the guide: sports centre, cinema tonight, followed series (reminders and
# recordings), the daily series map (Ramadan), and the links between cloud channels and your bouquets.
from __future__ import absolute_import

import os
import re
import time

from . import engine
from .sources import log, u

SERIES_FILE = os.path.join(engine.DATA_DIR, 'series.json')
CINEMA_FILE = os.path.join(engine.DATA_DIR, 'cinema_channels.json')
TIMER_TAG = 'SaadEPG'

NO_FILM = re.compile(u'(?i)(news|أخبار|اخبار|نشرة|live|مباشر|match|مباراة|\\bvs\\b|ضد|series|مسلسل|episode|حلقة|'
                     u'الحلقة|season|موسم|show|برنامج|concert|حفل|cartoon|كرتون|قرآن|قران|صلاة|اذان|أذان)')
GENERIC = re.compile(u'(?i)^(news|أخبار|الأخبار|نشرة|موجز|قرآن|القرآن|القران|اذان|أذان|صلاة|إعلانات|اعلانات|'
                     u'فواصل|برامج|program|programme|to be announced|tba|no information|لا توجد معلومات|'
                     u'off ?air|closedown|teleshopping|live|مباشر)')
FILM_WORDS = re.compile(u'(?i)(فيلم|أفلام|افلام|movie|film|cinema|سينما)')

# Ramadan (1st day, approximate: the evening before is the first night)
RAMADAN = ((2027, 2, 8), (2028, 1, 28), (2029, 1, 16), (2030, 1, 6), (2031, 12, 15))


def ramadan_now(t=None):
    t = t or time.time()
    for y, m, d in RAMADAN:
        start = time.mktime((y, m, d, 0, 0, 0, 0, 0, -1)) - 86400
        if start <= t < start + 31 * 86400:
            return True
    return False


# --------------------------------------------------------------------- cloud channels -> your channels
def key_refs(services):
    """plugin key (also every source of a golden channel) -> [refs in your bouquets]. Background thread."""
    st = engine.load_state()
    auto = engine.load_auto()
    mp = engine.load_mapping()
    gm = engine.gold_members()
    members = {}
    for k, g in gm.items():
        members.setdefault(g, []).append(k)
    out = {}
    for ref, _n in services:
        keys = set()
        f = st['filled'].get(ref)
        if f and f.get('key'):
            keys.add(u(f['key']))
        cands, _m = engine.resolve(ref, mp, auto)
        for k, _s in cands[:2]:
            if k and k != '-':
                keys.add(u(k))
        for k in list(keys):
            if k.startswith(u'xml:gold/'):
                keys.update(members.get(k[9:], ()))
            elif k in gm:
                keys.add(u'xml:gold/' + gm[k])
        for k in keys:
            out.setdefault(k, []).append(ref)
    return out


def _refs_of(keys, kr):
    refs = []
    for k in keys or ():
        for r in kr.get(k, ()):
            if r not in refs:
                refs.append(r)
    return refs


def matches_for(services):
    d = engine.load_extras('matches')
    kr = key_refs(services)
    names = dict(services)
    rows = []
    for m in d.get('matches') or []:
        refs = _refs_of(m.get('keys'), kr)
        rows.append(dict(m, refs=refs, mine=[names[r] for r in refs if r in names]))
    return rows, d.get('generated')


# --------------------------------------------------------------------- cinema tonight
def cinema_channels():
    return [tuple(x) for x in engine._load(CINEMA_FILE, [])]


def save_cinema_channels(lst):
    engine._save(CINEMA_FILE, [list(x) for x in lst])


def films_for(services, epgcache, only_mine=False):
    """films of the next 30 hours: the cloud's list on your channels + every long programme of the channels
    you added yourself (any channel, also ones the plugin does not fill). Background thread."""
    now = time.time()
    names = dict(services)
    rows, seen = [], set()
    extra = cinema_channels()
    if not only_mine:
        d = engine.load_extras('films')
        kr = key_refs(services)
        for f in d.get('films') or []:
            if f['stop'] <= now:
                continue
            refs = _refs_of(f.get('keys'), kr)
            if not refs:
                continue
            k = (refs[0], f['start'] // 600)
            if k in seen:
                continue
            seen.add(k)
            rows.append(dict(f, refs=refs, mine=[names.get(r, r) for r in refs], src='cloud'))
    for ref, name in extra:
        for b, dur, title, short, desc, _x in engine.read_events(epgcache, ref, 2):
            if b + dur <= now or b > now + 30 * 3600 or dur < 70 * 60 or dur > 4 * 3600:
                continue
            if NO_FILM.search(title) or GENERIC.search(title):
                continue
            k = (ref, b // 600)
            if k in seen:
                continue
            seen.add(k)
            rows.append({'start': b, 'stop': b + dur, 'title': title, 'desc': desc or short, 'cat': u'',
                         'icon': None, 'refs': [ref], 'mine': [name], 'src': 'mine'})
    rows.sort(key=lambda f: (f['start'], f['title']))
    return rows


# --------------------------------------------------------------------- followed series
def load_series():
    d = engine._load(SERIES_FILE, {})
    d.setdefault('items', [])
    d.setdefault('done', {})
    return d


def save_series(d):
    now = time.time()
    d['done'] = dict((k, v) for k, v in (d.get('done') or {}).items() if now - v < 10 * 86400)
    engine._save(SERIES_FILE, d)


def add_series(q, mode='remind', ref=None, name=None):
    q = u(q).strip()
    if not q:
        return False
    d = load_series()
    for it in d['items']:
        if it['q'] == q and it.get('ref') == ref:
            it['mode'] = mode
            save_series(d)
            return True
    d['items'].append({'q': q, 'mode': mode, 'ref': ref, 'name': name, 'added': int(time.time())})
    save_series(d)
    return True


def remove_series(q, ref=None):
    d = load_series()
    d['items'] = [it for it in d['items'] if not (it['q'] == q and it.get('ref') == ref)]
    save_series(d)


def _search(epgcache, q, limit=400):
    try:
        from enigma import eEPGCache
        kind = getattr(eEPGCache, 'PARTIAL_TITLE_SEARCH', 2)
        nocase = getattr(eEPGCache, 'NO_CASE_CHECK', 1)
        return epgcache.search(('RIBDT', limit, kind, engine._enc(q), nocase)) or []
    except Exception as e:
        log(u'بحث الدليل (%s): %s' % (q, e))
        return []


def airings(epgcache, item, services, hours=72):
    """-> [(begin, duration, title, ref, name, eventid)] of a followed series in your bouquets"""
    now = time.time()
    names = dict(services)
    out = []
    for x in _search(epgcache, item['q']):
        try:
            ref = engine.clean_ref(u(x[0]))
            eid, b, dur, title = int(x[1]), int(x[2]), int(x[3]), u(x[4])
        except (TypeError, ValueError, IndexError):
            continue
        if ref not in names or b + dur <= now or b > now + hours * 3600:
            continue
        if item.get('ref') and engine.clean_ref(item['ref']) != ref:
            continue
        out.append((b, dur, title, ref, names[ref], eid))
    out.sort()
    return out


def first_per_day(lst):
    """the first airing of every day (the others are reruns)"""
    days, out = set(), []
    for a in lst:
        day = time.strftime('%Y%m%d', time.localtime(a[0] - 4 * 3600))    # a TV day ends at 4 in the morning
        if day not in days:
            days.add(day)
            out.append(a)
    return out


def _timers(session):
    try:
        return session.nav.RecordTimer.timer_list + session.nav.RecordTimer.processed_timers
    except Exception:
        return []


def has_timer(session, ref, begin):
    for t in _timers(session):
        try:
            if engine.clean_ref(str(t.service_ref)) == ref and abs(t.begin - begin) < 15 * 60 + 600:
                return t
        except Exception:
            continue
    return None


def add_timer(session, ref, begin, dur, name, desc=u'', eit=None, record=False):
    """main thread. -> 'ok' | 'exists' | 'conflict' | error text"""
    if has_timer(session, ref, begin):
        return 'exists'
    try:
        from RecordTimer import RecordTimerEntry, AFTEREVENT
        from ServiceReference import ServiceReference
        if record:
            try:
                from Components.config import config
                mb = int(config.recording.margin_before.value) * 60
                ma = int(config.recording.margin_after.value) * 60
            except Exception:
                mb, ma = 120, 300
            b, e = begin - mb, begin + dur + ma
        else:
            b, e = begin - 30, begin + 30
        args = (ServiceReference(engine._enc(ref)), int(b), int(e), engine._enc(name), engine._enc(desc or u''), eit)
        try:
            entry = RecordTimerEntry(*args, justplay=not record, afterEvent=AFTEREVENT.AUTO, tags=[TIMER_TAG])
        except TypeError:
            entry = RecordTimerEntry(*args, justplay=not record, afterEvent=AFTEREVENT.AUTO)
        res = session.nav.RecordTimer.record(entry)
        if res:
            return 'conflict'
        return 'ok'
    except Exception as ex:
        return u(str(ex))


def plugin_timers(session):
    out = []
    for t in _timers(session):
        try:
            if TIMER_TAG in (getattr(t, 'tags', None) or []):
                out.append(t)
        except Exception:
            pass
    return out


def schedule_series(session, epgcache, services, hours=40):
    """main thread: reminders / recordings for the followed series (first airing of each day)."""
    d = load_series()
    if not d['items']:
        return 0
    n = 0
    for it in d['items']:
        if it.get('mode') not in ('remind', 'record'):
            continue
        for b, dur, title, ref, _name, eid in first_per_day(airings(epgcache, it, services, hours)):
            k = u'%s|%d' % (ref, b)
            if k in d['done']:
                continue
            res = add_timer(session, ref, b, dur, title, u'Saad EPG: %s' % it['q'], eid, it['mode'] == 'record')
            d['done'][k] = int(time.time())
            if res == 'ok':
                n += 1
                log(u'مسلسلاتي: %s %s - %s' % (u'تسجيل' if it['mode'] == 'record' else u'تذكير', title,
                                               time.strftime('%d/%m %H:%M', time.localtime(b))))
            elif res not in ('exists',):
                log(u'مسلسلاتي: لم يُضف %s (%s)' % (title, res))
    save_series(d)
    return n


# --------------------------------------------------------------------- daily series map
_EP = re.compile(u'(?i)\\s*([-:|]\\s*)?(الحلقة|حلقة|ح|episode|ep\\.?|e)\\s*\\d+.*$|\\s+\\d+\\s*$|\\s*\\(\\d+\\)\\s*$')


def series_title(t):
    return _EP.sub(u'', u(t)).strip()


def series_map(epgcache, services, days=3):
    """titles shown every day at about the same time -> [{'title','ref','name','time','reruns','next'}]
    Background thread."""
    now = time.time()
    rows = []
    for ref, name in services:
        evs = engine.read_events(epgcache, ref, days)
        by = {}
        for b, dur, title, _s, desc, _x in evs:
            if dur < 15 * 60 or dur > 100 * 60:
                continue
            t = series_title(title)
            if len(t) < 3 or GENERIC.search(t) or FILM_WORDS.search(t):
                continue
            by.setdefault(t, []).append((b, dur, desc))
        for t, lst in by.items():
            days_seen = {}
            for b, dur, desc in lst:
                day = time.strftime('%Y%m%d', time.localtime(b - 4 * 3600))
                days_seen.setdefault(day, []).append(b)
            if len(days_seen) < 2:
                continue
            firsts = sorted(min(v) for v in days_seen.values())
            mins = [((time.localtime(x).tm_hour - 4) % 24) * 60 + time.localtime(x).tm_min for x in firsts]
            if max(mins) - min(mins) > 60:
                continue          # not at the same time every day
            fut = sorted(b for b, _d, _x in lst if b + 1800 > now)
            first_today = time.strftime('%H:%M', time.localtime(firsts[-1] if firsts[-1] > now - 86400 else firsts[0]))
            reruns = sorted(set(time.strftime('%H:%M', time.localtime(b)) for b, _d, _x in lst
                                if time.strftime('%H:%M', time.localtime(b)) != first_today))
            rows.append({'title': t, 'ref': ref, 'name': name, 'time': first_today, 'reruns': reruns[:4],
                         'next': fut[0] if fut else None,
                         'desc': next((d for _b, _du, d in lst if d), u''),
                         'mins': ((time.localtime(firsts[0]).tm_hour - 4) % 24) * 60 + time.localtime(firsts[0]).tm_min})
    rows.sort(key=lambda r: (r['mins'], r['name']))
    return rows


# --------------------------------------------------------------------- genre colours (grid)
_GENRE = (
    ('film', re.compile(u'(?i)(فيلم|أفلام|افلام|movie|film|cinema)')),
    ('sport', re.compile(u'(?i)(رياض|sport|football|كرة|مباراة|match|\\bvs\\b|ضد|league|دوري|كأس|cup)')),
    ('news', re.compile(u'(?i)(أخبار|اخبار|نشرة|news|حصاد|موجز)')),
    ('series', re.compile(u'(?i)(مسلسل|series|حلقة|الحلقة|episode|season|موسم|\\bep\\s*\\d)')),
    ('kids', re.compile(u'(?i)(أطفال|اطفال|كرتون|رسوم|kids|cartoon|animation)')),
    ('religion', re.compile(u'(?i)(قرآن|القران|القرآن|صلاة|اذان|أذان|ديني|دينية|tafsir|تفسير)')),
)


def genre(title, short=u'', desc=u''):
    s = u'%s %s %s' % (title, short, (desc or u'')[:80])
    for g, rx in _GENRE:
        if rx.search(s):
            return g
    return None
