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


# --------------------------------------------------------------------- the guide follows the broadcast
_LIVE = {'last': {}}


def _same_title(a, b):
    from difflib import SequenceMatcher
    from .matcher import norm
    x, y = norm(a), norm(b)
    if not x or not y:
        return False
    if x == y or (len(x) > 4 and (x in y or y in x)):
        return True
    return SequenceMatcher(None, x, y).ratio() >= 0.75


def follow_live(session, epgcache):
    """main thread, every minute while a channel filled by the plugin is watched: the channel sends its own
    "now" with the picture. A programme that started late or runs over (extra time in a match) moves the
    rest of the plugin's guide for this channel, and the plugin's reminders / recordings with it.
    -> message for the screen, or None"""
    try:
        sref = session.nav.getCurrentlyPlayingServiceReference()
        ref = engine.clean_ref(sref.toString()) if sref else None
        if not ref or not engine.is_locked(ref):
            return None
        service = session.nav.getCurrentService()
        info = service and service.info()
        ev = info and info.getEvent(0)
        if not ev:
            return None
        s_title = u(ev.getEventName() or u'')
        s_begin, s_dur = int(ev.getBeginTime() or 0), int(ev.getDuration() or 0)
        now = int(time.time())
        if not s_title or not s_begin or s_dur <= 0:
            return None
        ours = []
        for x in epgcache.lookupEvent(['IBDTSE', (engine._enc(ref), 0, now - 4 * 3600, 10 * 60)]) or []:
            if x and x[1] and x[2]:
                ours.append((int(x[1]), int(x[2]), u(x[3] or u''), u(x[4] or u''), u(x[5] or u''), int(x[0] or 0)))
        ours.sort()
        near = [e for e in ours if abs(e[0] - s_begin) < 3 * 3600 and _same_title(e[2], s_title)]
        if not near:
            return None
        e = min(near, key=lambda x: abs(x[0] - s_begin))
        delta = s_begin - e[0]                                   # started earlier / later than the guide says
        overrun = (s_begin + s_dur) - (e[0] + e[1])               # ends later than the guide says
        if abs(delta) < 180:
            delta = 0
        if delta == 0 and overrun >= 300 and e[0] + e[1] <= now + 600:
            delta = overrun                                      # still on air after its planned end
            move_from = e[0] + e[1]
            new = [(e[0], e[1] + delta) + e[2:]]
        elif delta:
            move_from = e[0]
            new = []
        else:
            return None
        if abs(delta) > 3 * 3600:
            return None
        key = (ref, e[0], delta)
        if _LIVE['last'].get(ref) == key:
            return None
        _LIVE['last'][ref] = key
        limit = now + 8 * 3600
        for x in ours:
            if x[0] >= move_from and x[0] < limit:
                new.append((x[0] + delta,) + x[1:])
        if not new:
            return None
        engine.import_events(epgcache, [ref], [(b, d, t, sh, lo, 0) for b, d, t, sh, lo, _eid in new])
        moved = 0
        for t in plugin_timers(session):
            try:
                if engine.clean_ref(str(t.service_ref)) == ref and move_from - 300 <= t.begin < limit:
                    t.begin += delta
                    t.end += delta
                    session.nav.RecordTimer.timeChanged(t)
                    moved += 1
            except Exception:
                pass
        mins = int(round(delta / 60.0))
        log(u'الدليل يتبع البث: %s %s %d دقيقة - تم تعديل %d برنامج%s' % (
            s_title, u'متأخر' if mins > 0 else u'مبكر', abs(mins), len(new),
            (u' و %d تذكير/تسجيل' % moved) if moved else u''))
        return u'Saad EPG: البث %s %d دقيقة - تم تعديل مواعيد البرامج التالية%s' % (
            u'متأخر' if mins > 0 else u'مبكر', abs(mins), (u' و %d تذكير/تسجيل' % moved) if moved else u'')
    except Exception as e:
        log(u'متابعة البث: %s' % e)
        return None


# --------------------------------------------------------------------- smart bouquets
SMART = (('films', u'أفلام'), ('series', u'مسلسلات ودراما'), ('sport', u'رياضة'), ('news', u'أخبار'),
         ('kids', u'أطفال'), ('religion', u'ديني'), ('docs', u'وثائقي'), ('music', u'موسيقى'))
_NAME_CAT = (
    ('sport', re.compile(u'(?i)(sport|bein|ssc|alkass|al kass|kass|eurosport|arryadia|رياض|سبورت|الكاس|الكأس|on ?time|yas|dazn|'
                         u'espn|sky sport|polsat sport|nova sport|arena sport|match|futbol|calcio|football|tennis)')),
    ('news', re.compile(u'(?i)(news|akhbar|أخبار|اخبار|الإخبارية|الاخبارية|jazeera|arabiya|العربية|hadath|الحدث|sky ?news|'
                        u'cnn|bbc world|france ?24|euronews|rt |rusiya|alhurra|mayadeen|alghad|الغد|extra news|asharq|الشرق|'
                        u'cnbc|bloomberg|tvn24|ntv|haber)')),
    ('kids', re.compile(u'(?i)(kids|kid|cartoon|toon|junior|baby|spacetoon|majid|ماجد|طيور|toyor|karameesh|كراميش|ajyal|أجيال|'
                        u'nick|disney|jeem|cbeebies|boomerang|duck|minimax|أطفال|اطفال)')),
    ('religion', re.compile(u'(?i)(quran|qur.an|قرآن|القران|القرآن|sunnah|السنة|iqraa|اقرأ|majd|المجد|resalah|الرسالة|azhari|'
                            u'الأزهر|noor|نور|hidayah|الهداية|islam|إسلام|makkah|مكة|madinah|المدينة|ctv|aghapy|miracle|sat-7)')),
    ('docs', re.compile(u'(?i)(nat ?geo|national geographic|discovery|animal planet|history|documentary|وثائقي|الوثائقية|'
                        u'doc|science|travel|viasat (explore|nature|history))')),
    ('music', re.compile(u'(?i)(music|mazzika|مزيكا|rotana clip|روتانا كليب|melody hits|aghani|أغاني|اغاني|tarab|طرب|'
                         u'mtv|vh1|clip|radio|fm\\b)')),
    ('films', re.compile(u'(?i)(cinema|سينما|aflam|أفلام|افلام|movies?|film|mbc ?2|mbc ?max|mbc ?action|bollywood|zee aflam|'
                         u'rotana classic|نايل سينما|hbo|cinemax|kino)')),
    ('series', re.compile(u'(?i)(drama|دراما|series|مسلسلات|mosalsalat|hekayat|حكايات|novela|serie|zaman|زمان|hayat '
                          u'mosalsalat)')),
)


def classify(name, events):
    """-> category of a channel: its name first, otherwise what its guide mostly shows"""
    for cat, rx in _NAME_CAT:
        if rx.search(name):
            return cat
    count = {}
    for e in events:
        g = genre(e[2], e[3])
        if g:
            count[g] = count.get(g, 0) + e[1]
    total = sum(e[1] for e in events) or 1
    if count:
        g, v = max(count.items(), key=lambda kv: kv[1])
        if v >= 0.45 * total:
            return {'film': 'films', 'series': 'series', 'sport': 'sport', 'news': 'news', 'kids': 'kids',
                    'religion': 'religion'}.get(g)
    return None


def smart_plan(epgcache, bouquets):
    """background thread: {category: [(ref, name)]} from the channels of your own bouquets"""
    plan, seen = {}, set()
    for bname, items in bouquets:
        if u(bname).startswith(u'Saad EPG'):
            continue
        for ref, name in items:
            if ref in seen:
                continue
            seen.add(ref)
            evs = engine.read_events(epgcache, ref, 1)
            cat = classify(name, evs)
            if cat:
                plan.setdefault(cat, []).append((ref, name))
    for cat in plan:
        plan[cat].sort(key=lambda x: x[1].lower())
    return plan


BQ_DIR = '/etc/enigma2'
BQ_FILE = 'userbouquet.saadepg_%s.tv'


def _bq_line(fn):
    return '#SERVICE 1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "%s" ORDER BY bouquet' % fn


def write_smart(plan):
    """main thread: write the bouquets, add them to the bouquet list and reload. -> {category: count}"""
    idx = os.path.join(BQ_DIR, 'bouquets.tv')
    try:
        with open(idx) as f:
            lines = f.read().splitlines()
    except (IOError, OSError):
        lines = ['#NAME User - Bouquets (TV)']
    out = {}
    for cat, title in SMART:
        fn = BQ_FILE % cat
        path = os.path.join(BQ_DIR, fn)
        items = plan.get(cat) or []
        if not items:
            if os.path.exists(path):
                os.remove(path)
            lines = [x for x in lines if fn not in x]
            continue
        body = [u'#NAME Saad EPG - %s' % title]
        for ref, name in items:
            body.append(u'#SERVICE %s' % ref)
            body.append(u'#DESCRIPTION %s' % name)
        with open(path, 'wb') as f:
            f.write((u'\n'.join(body) + u'\n').encode('utf-8'))
        if not [1 for x in lines if fn in x]:
            lines.append(_bq_line(fn))
        out[cat] = len(items)
    with open(idx, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    reload_bouquets()
    return out


def remove_smart():
    idx = os.path.join(BQ_DIR, 'bouquets.tv')
    try:
        with open(idx) as f:
            lines = f.read().splitlines()
        lines = [x for x in lines if 'userbouquet.saadepg_' not in x]
        with open(idx, 'w') as f:
            f.write('\n'.join(lines) + '\n')
    except (IOError, OSError):
        pass
    for cat, _t in SMART:
        try:
            os.remove(os.path.join(BQ_DIR, BQ_FILE % cat))
        except OSError:
            pass
    reload_bouquets()


def has_smart():
    return any(os.path.exists(os.path.join(BQ_DIR, BQ_FILE % c)) for c, _t in SMART)


def reload_bouquets():
    try:
        from enigma import eDVBDB
        eDVBDB.getInstance().reloadBouquets()
    except Exception as e:
        log(u'إعادة تحميل الباقات: %s' % e)
    try:
        from Screens.InfoBar import InfoBar
        if InfoBar.instance and getattr(InfoBar.instance, 'servicelist', None):
            InfoBar.instance.servicelist.reloadBouquets()
    except Exception:
        pass


# --------------------------------------------------------------------- night scan of the satellite's own guide
class NightScan(object):
    """In standby, every transponder that carries channels without any guide is tuned for a minute: the
    receiver then collects the guide the satellite sends for all its channels (many Nilesat channels send
    several days that the receiver never sees, because nobody watches them)."""
    DWELL = 60
    MAX_TP = 90

    def __init__(self, session, timer_factory):
        self.session = session
        self.timer = timer_factory(self.step)
        self.wait = timer_factory(self.begin)
        self.todo, self.cands, self.active = [], [], False
        self.standby = None

    def entered_standby(self, wait=600):
        try:
            from Screens import Standby
            self.standby = Standby.inStandby
            if self.standby is not None and self.leave not in self.standby.onClose:
                self.standby.onClose.append(self.leave)
        except Exception:
            pass
        self.wait.startLongTimer(wait)

    def leave(self):
        self.wait.stop()
        self.standby = None              # the picture is back: never stop the service now
        if self.active:
            self.finish(u'خرج الريسيفر من الاستاندباي')

    def recording(self):
        try:
            return bool(self.session.nav.getRecordings())
        except Exception:
            return False

    def begin(self):
        from enigma import eEPGCache
        st = engine.load_state()
        if time.time() - (st.get('night') or {}).get('t', 0) < 20 * 3600 or self.recording():
            return
        epg = eEPGCache.getInstance()
        tps, cands = {}, []
        for ref, name in engine.get_services():
            p = ref.split(':')
            if len(p) < 10 or p[0] != '1' or engine.sat_of_ref(ref) in ('iptv', 'terrestrial'):
                continue
            if engine.is_locked(ref) or engine.epg_count(epg, ref, 24) >= 3:
                continue
            cands.append(ref)
            tps.setdefault((p[4], p[5], p[6]), ref)
        self.todo = list(tps.values())[:self.MAX_TP]
        self.cands = cands
        if not self.todo:
            return
        self.active = True
        self.started = time.time()
        log(u'مسح دليل القمر الليلي: %d تردد فيها %d قناة بدون دليل' % (len(self.todo), len(cands)))
        self.step()

    def step(self):
        if not self.active:
            return
        if self.recording():
            return self.finish(u'بدأ تسجيل')
        if not self.todo:
            return self.finish(None)
        ref = self.todo.pop(0)
        try:
            from enigma import eServiceReference
            self.session.nav.playService(eServiceReference(engine._enc(ref)))
        except Exception as e:
            log(u'مسح ليلي: %s' % e)
        self.timer.startLongTimer(self.DWELL)

    def finish(self, why):
        from enigma import eEPGCache
        self.active = False
        self.timer.stop()
        epg = eEPGCache.getInstance()
        try:
            if self.standby is not None:          # still in standby: no picture needed
                self.session.nav.stopService()
        except Exception:
            pass
        gained = len([r for r in self.cands if engine.epg_count(epg, r, 24) >= 3])
        try:
            epg.save()
        except Exception:
            pass
        st = engine.load_state()
        st['night'] = {'t': int(time.time()), 'gained': gained, 'cands': len(self.cands)}
        engine.save_state(st)
        log(u'مسح دليل القمر الليلي: %d قناة صار لها دليل من القمر (من %d بدون دليل)%s' % (
            gained, len(self.cands), (u' - توقف: %s' % why) if why else u''))
