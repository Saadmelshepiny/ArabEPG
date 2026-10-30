# -*- coding: utf-8 -*-
# Saad EPG engine: satellites, cloud guides, smart linking, fallback, backup cache, update job.
from __future__ import absolute_import, print_function

import gzip
import json
import os
import pickle
import shutil
import re
import threading
import time

from . import sources
from .matcher import Matcher, norm, source_of
from .sources import log, u, PY3

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = '/etc/enigma2/SaadEPG'
OLD_DATA_DIR = '/etc/enigma2/ArabEPG'      # earlier name of the plugin (ArabEPG): settings are taken over


def _migrate_old_name():
    """the plugin was called ArabEPG before: take over its links, learning and caches once."""
    try:
        import shutil
        if os.path.isdir(OLD_DATA_DIR) and not os.path.isdir(DATA_DIR):
            shutil.copytree(OLD_DATA_DIR, DATA_DIR)
        for m in ('/media/hdd', '/media/usb', '/media/mmc'):
            old, new = os.path.join(m, 'ArabEPG'), os.path.join(m, 'SaadEPG')
            if os.path.isdir(old) and not os.path.exists(new):
                os.rename(old, new)
    except Exception:
        pass


_migrate_old_name()
MAP_FILE = os.path.join(DATA_DIR, 'mapping.json')     # manual choices (always win)
AUTO_FILE = os.path.join(DATA_DIR, 'automap.json')    # cached automatic links
STATE_FILE = os.path.join(DATA_DIR, 'state.json')
INDEX_FILE = os.path.join(DATA_DIR, 'cloud_index.json')
CATALOG_FILE = os.path.join(DATA_DIR, 'catalog.json')  # online catalog (newer than the built-in one)
REPO_RAW = 'https://raw.githubusercontent.com/Saadmelshepiny/ArabEPG'
CLOUD_URL = REPO_RAW + '/epg/'
VERSION_URL = REPO_RAW + '/main/version.json'

SOURCE_ORDER = ('shahid', 'osn', 'bein', 'beinsports', 'rotana', 'aljazeera', 'roya', 'art', 'ayn', 'snrt',
                'lbci', 'tunisia', 'trt', 'presstv', 'elcinema', 'xml', 'xmltv')
SOURCE_NAMES = {'shahid': 'Shahid/MBC', 'osn': 'OSN', 'bein': 'beIN', 'rotana': 'Rotana', 'aljazeera': 'Al Jazeera',
                'elcinema': 'elCinema', 'beinsports': 'beIN SPORTS', 'roya': 'Roya', 'art': 'ART',
                'ayn': 'Oman TV', 'snrt': 'SNRT', 'lbci': 'LBCI', 'tunisia': 'Tunisia TV', 'trt': 'TRT Arabi', 'presstv': 'Press TV', 'xml': 'XMLTV', 'xmltv': 'XMLTV'}
SITE_FILE = {'shahid': 'ar_shahid', 'osn': 'ar_osn', 'elcinema': 'ar_elcinema', 'beinsports': 'ar_beinsports',
             'roya': 'ar_roya', 'art': 'ar_art', 'ayn': 'ar_ayn', 'snrt': 'ar_snrt', 'lbci': 'ar_lbci', 'tunisia': 'ar_tunisia', 'trt': 'ar_trt', 'presstv': 'ar_presstv',
             'bein': 'ar_bein', 'rotana': 'ar_rotana', 'aljazeera': 'ar_aljazeera'}
MATCHER_VERSION = 9
BULK_SOURCES = ('bein', 'xml', 'roya', 'beinsports')   # one request/file serves many channels

# orbital positions (tenths of a degree; west = 3600 - x)
SATELLITES = (
    ('nilesat', u'نايل سات 7W / 8W', ((3515, 3540),)),
    ('arabsat', u'عرب سات / بدر / سهيل', ((195, 210), (250, 265), (300, 310))),
    ('hotbird', u'هوت بيرد 13E', ((128, 132),)),
    ('eutelsat16', u'يوتلسات 16E', ((158, 162),)),
    ('bulgariasat', u'البلغاري 1.9E', ((18, 20),)),
    ('amos', u'آموس 4W', ((3555, 3565),)),
    ('astra19', u'أسترا 19.2E', ((189, 194),)),
    ('astra23', u'أسترا 23.5E', ((233, 237),)),
    ('astra28', u'أسترا 28.2E', ((280, 286),)),
    ('astra48', u'أسترا 4.8E', ((46, 52),)),
    ('eutelsat3', u'يوتلسات 3E', ((28, 32),)),
    ('eutelsat7', u'يوتلسات 7E', ((68, 72),)),
    ('eutelsat9', u'يوتلسات 9E', ((88, 92),)),
    ('eutelsat36', u'يوتلسات 36E', ((358, 362),)),
    ('hellas39', u'هيلاس سات 39E', ((388, 392),)),
    ('turksat42', u'تركسات 42E', ((418, 422),)),
    ('thor', u'ثور 0.8W / 1W', ((3588, 3593),)),
    ('eutelsat5w', u'يوتلسات 5W', ((3548, 3552),)),
    ('hispasat', u'هيسباسات 30W', ((3298, 3302),)),
)
SAT_TITLES = dict((k, t) for k, t, _r in SATELLITES)


# --------------------------------------------------------------------- storage
def _load(path, default):
    try:
        with open(path, 'rb') as f:
            data = f.read()
        if data[:2] == b'\x1f\x8b':
            data = gzip.GzipFile(fileobj=__import__('io').BytesIO(data)).read()
        return json.loads(data.decode('utf-8'))
    except Exception:
        return default


def _save(path, obj):
    try:
        d = os.path.dirname(path)
        if not os.path.isdir(d):
            os.makedirs(d)
        tmp = path + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
        os.rename(tmp, path)
    except Exception as e:
        log('save %s: %s' % (path, e))


SHIFT_FILE = os.path.join(DATA_DIR, 'shifts.json')     # hours added to a channel's guide, set by hand


def load_shifts():
    return _load(SHIFT_FILE, {})


def set_shift(ref, hours):
    d = load_shifts()
    if hours:
        d[ref] = int(hours)
    else:
        d.pop(ref, None)
    _save(SHIFT_FILE, d)
    ls = _load(os.path.join(DATA_DIR, 'live_shifts.json'), {})
    if ls.pop(ref, None) is not None:          # your own correction replaces the learned one
        _save(os.path.join(DATA_DIR, 'live_shifts.json'), ls)


LIVE_SHIFT_FILE = os.path.join(DATA_DIR, 'live_shifts.json')   # learned from the broadcast itself
LIVE_SHIFT_DAYS = 4


def live_shifts():
    return _load(LIVE_SHIFT_FILE, {})


def _tkey(t):
    from .matcher import norm
    return norm(t or u'')


def live_shift_seen(ref, hours, our_start, title, now=None):
    """follow_live: the channel's own "now" is the same programme as ours, whole hours away.
    Learned for good after two different programmes say the same; -> (confirmed, total hours)"""
    now = int(now or time.time())
    d = live_shifts()
    e = d.get(ref) or {}
    applied = int(e.get('sh', 0)) if e.get('on') else 0
    total = applied + int(hours)
    src = int(our_start) - applied * 3600            # the start as the source writes it
    probe = [_tkey(title), src]
    c = e.get('cand')
    confirmed = False
    if total == 0:
        d.pop(ref, None)
    elif c and int(c.get('sh', 0)) == total and c.get('probe') and c['probe'][1] != src:
        e = {'sh': total, 'on': 1, 't': now, 'probes': [c['probe'], probe]}
        d[ref] = e
        confirmed = True
    else:
        e['cand'] = {'sh': total, 'probe': probe, 't': now}
        d[ref] = e
    _save(LIVE_SHIFT_FILE, d)
    return confirmed, total


def live_shift_ok(ref, our_start, title, now=None):
    """the guide (with its learned shift) matches the broadcast: the shift is still right"""
    d = live_shifts()
    e = d.get(ref)
    if not e:
        return
    if not e.get('on'):
        d.pop(ref, None)                  # a single odd observation: forgotten
    else:
        e['t'] = int(now or time.time())
        e['probes'] = (e.get('probes') or [])[-3:] + [[_tkey(title), int(our_start) - int(e['sh']) * 3600]]
    _save(LIVE_SHIFT_FILE, d)


def live_shift_check(entry, events, now=None):
    """-> 'keep' | 'fixed' (the source itself moved its times: the shift is not needed any more) | 'old'"""
    now = now or time.time()
    sh = int(entry.get('sh', 0)) * 3600
    at = {}
    for e in events or ():
        at.setdefault(_tkey(e[2]), set()).add(int(e[0]))
    keep = fixed = 0
    for t, src in entry.get('probes') or []:
        got = at.get(t) or ()
        if any(abs(x - src) <= 120 for x in got):
            keep += 1
        elif any(abs(x - src - sh) <= 120 for x in got):
            fixed += 1
    if fixed > keep:
        return 'fixed'
    if keep or now - entry.get('t', 0) < LIVE_SHIFT_DAYS * 86400:
        return 'keep'
    return 'old'


def load_mapping():
    return _load(MAP_FILE, {})


def save_mapping(m):
    _save(MAP_FILE, m)


def load_state():
    st = _load(STATE_FILE, {})
    st.setdefault('filled', {})
    return st


_ST = {'sig': None, 'data': None}


def state_ro():
    """the state for screens that only read it (kept in memory until the file changes)"""
    sig = (_mtime(STATE_FILE), os.path.getsize(STATE_FILE) if os.path.exists(STATE_FILE) else 0)
    if _ST['sig'] != sig:
        _ST['sig'], _ST['data'] = sig, load_state()
    return _ST['data']


def save_state(st):
    _save(STATE_FILE, st)


def chunk_cache_dir():
    """copies of the cloud's per-channel files: on the hard disk, otherwise in RAM (never the flash)"""
    for d in ('/media/hdd', '/media/usb', '/media/mmc'):
        if os.path.ismount(d) and os.access(d, os.W_OK):
            return os.path.join(d, 'SaadEPG', 'ch')
    return '/tmp/SaadEPG_ch'


def cache_dir():
    for d in ('/media/hdd', '/media/usb', '/media/mmc'):
        if os.path.ismount(d) and os.access(d, os.W_OK):
            return os.path.join(d, 'SaadEPG')
    return os.path.join(DATA_DIR, 'cache')


# --------------------------------------------------------------------- satellites
def sat_of_ref(ref):
    p = u(ref).split(':')
    try:
        if p[0] != '1' or len(p) < 10:
            return 'iptv'
        ns = int(p[6], 16)
    except (ValueError, IndexError):
        return 'other'
    pos = ns >> 16
    if pos in (0xEEEE, 0xFFFF) or pos == 0:
        return 'terrestrial'
    for key, _t, ranges in SATELLITES:
        for a, b in ranges:
            if a <= pos <= b:
                return key
    return 'other'


def user_satellites(services):
    out = {}
    for ref, _n in services:
        s = sat_of_ref(ref)
        out[s] = out.get(s, 0) + 1
    return out


# --------------------------------------------------------------------- catalog
_CATALOG = None
_ENTRY = {}


def catalog():
    global _CATALOG
    if _CATALOG is None:
        built_in = _load(os.path.join(PLUGIN_DIR, 'channels.json'), [])
        online = _load(CATALOG_FILE, [])
        _CATALOG = online if len(online) > len(built_in) // 2 else built_in
        _CATALOG = list(_CATALOG) + _load(os.path.join(DATA_DIR, 'channels_extra.json'), [])
    return _CATALOG


def reset_catalog():
    global _CATALOG, _MATCHER
    _CATALOG, _MATCHER = None, None
    _ENTRY.clear()


def catalog_entry(key):
    if not _ENTRY:
        for c in catalog():
            _ENTRY['%s:%s' % (c['src'], c['id'])] = c
        for e in cloud_xml_entries(None):
            _ENTRY['xml:%s' % e['id']] = e
    return _ENTRY.get(key)


OFFICIAL = ('shahid', 'osn', 'bein', 'beinsports', 'rotana', 'aljazeera', 'roya', 'art', 'ayn', 'snrt', 'lbci', 'tunisia',
            'trt', 'presstv')


def source_kind(key):
    """short label of where a guide comes from (shown next to every channel)"""
    src = u(key or u'').split(':', 1)[0]
    if src in OFFICIAL:
        return u'رسمي'
    if src == 'elcinema':
        return u'عربي'
    if src in ('copy', 'chan'):
        return u'منسوخ'
    if src == 'xml':
        return u'ذهبي' if u(key).startswith(u'xml:gold/') else u'ملف'
    return u''


def display_key(key):
    if not key:
        return u''
    if key == '-':
        return u'(معطل)'
    if key.startswith('copy:'):
        return u'منسوخ من نسخة أخرى للقناة'
    if key.startswith('chan:'):
        return u'دليل قناة أخرى: %s' % key[5:].partition('|')[2]
    c = catalog_entry(key)
    src = source_of(key)
    if src.startswith('xml:'):
        f = (load_index().get('files') or {}).get(src[4:], {})
        label = u'Cloud %s' % f.get('title', src[4:])
    else:
        label = SOURCE_NAMES.get(src, src)
    name = c['names'][0] if c and c.get('names') else key.split(':', 1)[-1].split('|')[-1].split('/')[-1]
    return u'%s: %s' % (label, name)


# --------------------------------------------------------------------- cloud
# small Arabic XMLTV files that the receiver can also take straight from epgshare01 (no cloud needed)
EPGSHARE = 'https://epgshare01.online/epgshare01/epg_ripper_%s.xml.gz'
OPENEPG = 'https://www.open-epg.com/files/%s.xml'
DIRECT_XML = (
    ('ae', EPGSHARE % 'AE1', ('nilesat', 'arabsat', 'hotbird'), u'الإمارات: دبي، أبوظبي، الشارقة...'),
    ('bein', EPGSHARE % 'BEIN1', ('nilesat', 'arabsat', 'hotbird'), u'beIN'),
    ('eg', EPGSHARE % 'EG1', ('nilesat', 'arabsat'), u'مصر'),
    ('sa', EPGSHARE % 'SA2', ('nilesat', 'arabsat'), u'السعودية'),
    ('oe_qatar6', OPENEPG % 'qatar6', ('nilesat', 'arabsat'), u'open-epg: الكأس'),
    ('oe_qatar3', OPENEPG % 'qatar3', ('nilesat', 'arabsat'), u'open-epg: beIN Sports'),
)
IMPORT_DIRS = ('/media/hdd/SaadEPG/import', '/media/usb/SaadEPG/import', '/media/mmc/SaadEPG/import',
               '/media/hdd/ArabEPG/import', '/media/usb/ArabEPG/import',
               os.path.join(DATA_DIR, 'import'))
LOCAL_INDEX = os.path.join(DATA_DIR, 'local_index.json')
LEARN_FILE = os.path.join(DATA_DIR, 'learned.json')
_IDX = {'sig': None, 'idx': {}}


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0


def load_index():
    """cloud index + directly downloaded / imported XMLTV files (the cloud copy wins)."""
    sig = (_mtime(INDEX_FILE), _mtime(LOCAL_INDEX))
    if _IDX['sig'] != sig:
        idx = dict(_load(INDEX_FILE, {}))
        files = dict(idx.get('files') or {})
        for k, f in (_load(LOCAL_INDEX, {}).get('files') or {}).items():
            if k not in files:
                files[k] = f
        idx['files'] = files
        _IDX['sig'], _IDX['idx'] = sig, idx
    return _IDX['idx']


def _names_of(path):
    _ev, names = sources.parse_xmltv_file(path, set(), 1, names_only=True)
    return [[cid, nl or [cid]] for cid, nl in sorted(names.items())]


def refresh_local(cloud_files, stop=None, force=False):
    """index the direct epgshare01 files that the cloud does not provide, and files the user
    dropped into an import folder (…/SaadEPG/import/*.xml[.gz])."""
    loc = _load(LOCAL_INDEX, {})
    files = dict(loc.get('files') or {})
    changed = False
    for name, url, sats, title in DIRECT_XML:
        if name in cloud_files:
            files.pop(name, None)
            continue
        f = files.get(name)
        if f and not force and time.time() - f.get('_t', 0) < 24 * 3600:
            continue
        if stop and stop():
            break
        path = '/tmp/SaadEPG_direct_%s.xml.gz' % name
        try:
            sources.http_download(url, path, timeout=120, stop=stop)
            files[name] = {'url': url, 'sat': list(sats), 'title': title, 'kind': 'xmltv',
                           'direct': True, '_t': int(time.time()), 'channels': _names_of(path)}
            changed = True
            log(u'مصدر مباشر %s: %d قناة' % (title, len(files[name]['channels'])))
        except Exception as e:
            log(u'تعذر تحميل %s (%s)' % (title, e))
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    seen = set()
    for d in IMPORT_DIRS:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.lower().endswith(('.xml', '.xml.gz', '.xmltv', '.gz')):
                continue
            p = os.path.join(d, fn)
            key = 'imp_' + re.sub(r'[^A-Za-z0-9_]', '_', fn.split('.')[0])[:30]
            seen.add(key)
            f = files.get(key)
            if f and f.get('_m') == _mtime(p) and not force:
                continue
            try:
                files[key] = {'url': 'file://' + p, 'sat': ['*'], 'title': u'ملف مستورد ' + u(fn), 'kind': 'xmltv',
                              'direct': True, '_m': _mtime(p), 'channels': _names_of(p)}
                changed = True
                log(u'ملف EPG مستورد %s: %d قناة' % (u(fn), len(files[key]['channels'])))
            except Exception as e:
                log(u'تعذر قراءة الملف المستورد %s (%s)' % (u(fn), e))
    for key in [k for k in files if k.startswith('imp_') and k not in seen]:
        del files[key]
        changed = True
    if changed:
        _save(LOCAL_INDEX, {'files': files})
    return files


def refresh_cloud(base=CLOUD_URL, max_age=6 * 3600, force=False, use_cloud=True, stop=None):
    """cloud index + online catalog (when older than max_age), then the direct / imported files."""
    cloud = _load(INDEX_FILE, {})
    if use_cloud and (force or not cloud or time.time() - cloud.get('_fetched', 0) >= max_age):
        try:
            new = json.loads(sources.http_get(base + 'index.json.gz', timeout=60).decode('utf-8'))
            new['_fetched'] = int(time.time())
            _save(INDEX_FILE, new)
            cloud = new
            log(u'السحابة: %d ملف (آخر بناء %s)' % (
                len(new.get('files', {})), time.strftime('%d/%m %H:%M', time.localtime(new.get('generated', 0)))))
        except Exception as e:
            if '404' in str(e):
                log(u'السحابة غير جاهزة بعد (المستودع لم يُرفع أو لم يعمل Actions) - سيتم استخدام المصادر المباشرة')
            else:
                log(u'السحابة: تعذر تحميل الفهرس (%s)' % e)
        try:
            if force or time.time() - _mtime(CATALOG_FILE) > 24 * 3600:
                cat = json.loads(sources.http_get(base + 'catalog.json.gz', timeout=60).decode('utf-8'))
                if isinstance(cat, list) and len(cat) > 100:
                    _save(CATALOG_FILE, cat)
                    reset_catalog()
                    log(u'تم تحديث قائمة القنوات أونلاين: %d قناة' % len(cat))
        except Exception:
            pass
        try:
            if force or time.time() - _mtime(REFS_FILE) > 24 * 3600:
                refs = json.loads(sources.http_get(base + 'refs.json.gz', timeout=60).decode('utf-8'))
                if isinstance(refs, dict) and len(refs) > 100:
                    _save(REFS_FILE, refs)
        except Exception:
            pass
    if use_cloud:
        # Arabic made in the cloud: real titles of bilingual sites, machine titles, machine descriptions
        for kind, name, label in (('', 'tr_ar.json.gz', u'عناوين آلية'), ('real', 'tr_ar_real.json.gz', u'عناوين عربية حقيقية'),
                                  ('desc', 'tr_ar_desc.json.gz', u'وصف')):
            try:
                tp = cloud_tr_path(kind)
                if force or time.time() - _mtime(tp) > 12 * 3600:
                    raw = sources.http_get(base + name, timeout=90)
                    tr = json.loads(raw.decode('utf-8'))
                    if isinstance(tr, dict) and tr:
                        d = os.path.dirname(tp)
                        if not os.path.isdir(d):
                            os.makedirs(d)
                        with gzip.open(tp + '.tmp', 'wb') as f:
                            f.write(json.dumps(tr, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
                        os.rename(tp + '.tmp', tp)
                        log(u'السحابة: قاموس %s (%d)' % (label, len(tr)))
            except Exception:
                pass
        for name in EXTRAS:      # sports centre and cinema tonight (made in the cloud every few hours)
            try:
                if force or time.time() - _mtime(extras_path(name)) > 2 * 3600:
                    d = json.loads(sources.http_get(base + name + '.json.gz', timeout=60).decode('utf-8'))
                    if isinstance(d, dict) and d.get('generated'):
                        _save(extras_path(name), d)
            except Exception:
                pass
        try:
            if force or time.time() - _mtime(LINKED_REFS_FILE) > 24 * 3600:
                lr = json.loads(sources.http_get(base + 'linked_refs.json', timeout=30).decode('utf-8'))
                if isinstance(lr, dict):
                    _save(LINKED_REFS_FILE, lr)
        except Exception:
            pass
    refresh_local(set((cloud.get('files') or {}) if use_cloud else ()), stop, force)
    return load_index()


def confidence(ref, state=None, idx=None):
    """how sure the plugin is of a channel's guide -> (0-100 or None, label, colour)"""
    state = state or state_ro()
    f = (state.get('filled') or {}).get(ref)
    if not f:
        return None, u'', 'dim'
    if f.get('v'):
        return 100, u'مؤكد بدليل القمر', 'accent'
    key = u(f.get('key', ''))
    if key.startswith(u'xml:est/'):
        return 40, u'جدول تقديري (المصدر متوقف - من تكرار البث)', 'amber'
    if key.startswith(u'xml:crowd_ar/'):
        return 90, u'دليل القمر نفسه (من السحابة)', 'accent'
    if f.get('est'):
        return 55, u'جزء منه تقديري (المصدر ينتهي مبكرا)', 'amber'
    if key.startswith(u'xml:gold/'):
        c = (((idx or load_index()).get('gold') or {}).get('conf') or {}).get(key[9:])
        if c is not None:
            if c >= 80:
                return c, u'المصادر متفقة %d%%' % c, 'accent'
            if c >= 50:
                return c, u'المصادر متفقة جزئيا %d%%' % c, 'amber'
            return c, u'المصادر مختلفة %d%%' % c, 'red'
    if key.startswith(u'copy:'):
        return 60, u'منسوخ من نسخة أخرى للقناة', 'amber'
    return 70, u'مصدر واحد (لم يُقارن)', 'amber'


def gold_members():
    """{plugin key of a source channel: golden channel id} from the cloud index"""
    return (load_index().get('gold') or {}).get('members') or {}


EXTRAS = ('matches', 'films')


def extras_path(name):
    return os.path.join(DATA_DIR, '%s.json' % name)


def load_extras(name):
    return _load(extras_path(name), {})


def cloud_tr_path(kind=''):
    """'' machine titles, 'real' real Arabic titles/descriptions, 'desc' machine descriptions"""
    return os.path.join(cache_dir(), 'tr_cloud%s.json.gz' % (('_' + kind) if kind else ''))


def _sat_ok(f, sats):
    fs = set(f.get('sat') or [])
    return '*' in fs or sats is None or bool(fs & set(sats))


def cloud_xml_entries(user_sats, every=False):
    """extra XMLTV channels (cloud / direct / imported) limited to the user's satellites (every: all of them)"""
    out = []
    for fname, f in (load_index().get('files') or {}).items():
        if f.get('kind') != 'xmltv' or not (every or _sat_ok(f, user_sats)):
            continue
        for cid, names in f.get('channels') or []:
            names = [re.sub(u'\\.[a-z]{2}$', u'', u(n)).strip() for n in (names or [cid])]
            out.append({'src': 'xml', 'id': u'%s/%s' % (fname, cid), 'names': names})
    return out


# --------------------------------------------------------------------- satellite guide -> English
LANG_ALIAS = {'gr': 'el', 'sr': 'sl', 'hr': 'sl', 'bs': 'sl', 'me': 'sl', 'no': 'nb', 'cz': 'cs', 'iw': 'he'}
LANG_EXTRA = {'sl': ('ru',), 'uk': ('ru',)}     # Serbian in Cyrillic, Russian on Ukrainian channels
_EN_WORDS = re.compile(r'(?i)\b(the|and|of|with|news|live|show|in|to|on|for)\b')
_NON_ASCII = re.compile(u'[^\x00-\x7f]')


def load_title_dicts(base, sats, stop=None):
    """English title dictionaries made in the cloud, for the languages of the foreign guides on your satellites.
    -> ({original title: english}, [languages])"""
    idx = load_index()
    avail = idx.get('tr') or {}
    langs = set()
    for f in (idx.get('files') or {}).values():
        if f.get('kind') == 'xmltv' and f.get('region') == 'eu' and f.get('lang') and _sat_ok(f, sats):
            l = LANG_ALIAS.get(f['lang'], f['lang'])
            langs.add(l)
            langs.update(LANG_EXTRA.get(l, ()))
    langs = sorted(l for l in langs if l in avail)
    d = os.path.join(cache_dir(), 'tr')
    merged = {}
    for l in langs:
        p = os.path.join(d, 't_%s.json.gz' % l)
        if not os.path.exists(p) or time.time() - _mtime(p) > 20 * 3600:
            try:
                if not os.path.isdir(d):
                    os.makedirs(d)
                sources.http_download(base + avail[l], p + '.part', timeout=120, stop=stop)
                os.rename(p + '.part', p)
            except Exception as e:
                log(u'قاموس الترجمة %s: %s' % (l, e))
        for k, v in _load(p, {}).items():
            merged.setdefault(k, v)
    return merged, langs


def translate_guide(events, d):
    """satellite events in a foreign language -> (events with English titles, number translated) or (None, 0)
    when the channel is English already, Arabic, or in a language without a dictionary."""
    titles = [e[2] for e in events if e[2]]
    if len(titles) < 3 or text_lang(titles) == 'ar':
        return None, 0
    eng = len([t for t in titles if _EN_WORDS.search(t)])
    accents = len([t for t in titles if _NON_ASCII.search(t)])
    if eng * 10 >= len(titles) * 3 and accents * 20 < len(titles):
        return None, 0               # an English channel: leave it alone
    out, hits = [], 0
    for e in events:
        t = (e[2] or u'').strip()
        en = d.get(t) or d.get(t.rstrip(u'.').strip())
        sh = d.get((e[3] or u'').strip()) if e[3] else None
        hits += 1 if en else 0
        out.append((e[0], e[1], en or e[2], sh or e[3], e[4], e[5]))
    if not hits or hits * 5 < len(events):
        return None, 0               # under 20% known titles: not a language we have
    return out, hits


def xml_files_for_sat(sat):
    return set('xml:' + fn for fn, f in (load_index().get('files') or {}).items()
               if f.get('kind') == 'xmltv' and _sat_ok(f, [sat]))


_MATCHER = None
_MATCHER_SIG = None


def matcher(user_sats=None):
    global _MATCHER, _MATCHER_SIG
    xml = cloud_xml_entries(user_sats)
    sig = (len(catalog()), len(xml), tuple(sorted(user_sats or [])))
    if _MATCHER is None or _MATCHER_SIG != sig:
        _MATCHER = Matcher(list(catalog()) + xml, SOURCE_ORDER)
        _MATCHER_SIG = sig
    return _MATCHER


_MATCHER_ALL = {'sig': None, 'm': None}


def matcher_every():
    """every cloud channel, whatever satellite its guide was made for"""
    xml = cloud_xml_entries(None, every=True)
    sig = (len(xml), load_index().get('generated'))
    if _MATCHER_ALL['sig'] != sig:
        _MATCHER_ALL['m'] = Matcher([x for x in xml if not x['id'].startswith('est/')], SOURCE_ORDER)
        _MATCHER_ALL['sig'] = sig
    return _MATCHER_ALL['m']


# countries that share the same channels (one guide each): Czech/Slovak, ex-Yugoslav, German speaking
_SAME_MARKET = {'cs': 'cs', 'cz': 'cs', 'sk': 'cs', 'sr': 'yu', 'rs': 'yu', 'hr': 'yu', 'bs': 'yu', 'ba': 'yu',
                'sl': 'yu', 'si': 'yu', 'me': 'yu', 'mk': 'yu', 'de': 'de', 'at': 'de'}


def _file_country(key, idx):
    fn = key[4:].split('/', 1)[0]
    f = (idx.get('files') or {}).get(fn) or {}
    c = f.get('lang') or re.sub(u'^oe_|\\d+$', u'', fn)
    return _SAME_MARKET.get(c, c)


def wide_match(name, idx, rejected=(), sat=None):
    """a channel nobody links: the guide made for another satellite (Bulgarian channels on Hot Bird, Slovak
    ones on Eutelsat 16E...). Only a (nearly) exact name, only guides of the same world (Arabic satellites:
    Arabic guides; European satellites: European guides - "Nat Geographic" on 16E is not the Saudi one), and
    only when every file that has it is of one country ("Sport 1" exists in several and is left alone)."""
    n = norm(name)
    core = re.sub(u'(hd|fhd|uhd|4k)$', u'', n)
    if len(core) < 3 or (len(core) < 4 and not re.search(u'\\d', core)):
        return []          # "TA3" is a name, "Rai" is too short to be sure of
    files = idx.get('files') or {}
    arabic = sat in ('nilesat', 'arabsat') or bool(re.search(u'[\u0621-\u064a]', name))

    def ok(k):
        if not k.startswith('xml:'):
            return False
        return (files.get(k[4:].split('/', 1)[0], {}).get('region') == 'ar') == arabic
    short = re.sub(u'(?i)[\\s._-]*(hd|fhd|uhd|4k)\\s*$', u'', name).strip()
    wide = {}
    for nm in set([name, short]):
        for k, sc in matcher_every().match_all(nm, ok, min_score=0.97, limit=8):
            if (n, k) not in rejected:
                wide[k] = max(sc, wide.get(k, 0))
    if not wide or len(set(_file_country(k, idx) for k in wide)) != 1:
        return []
    return [(k, 0.93) for k, _sc in sorted(wide.items(), key=lambda kv: -kv[1])[:3]]


# --------------------------------------------------------------------- learning from the user
def load_learned():
    d = _load(LEARN_FILE, {})
    d.setdefault('alias', {})
    d.setdefault('reject', [])
    d.setdefault('refs', {})        # '<orbital>:<SID>:<TSID>:<ONID>' -> key  (manual or verified links)
    return d


LINKED_REFS_FILE = os.path.join(DATA_DIR, 'linked_refs.json')   # shared list from the cloud


def linked_refs():
    """confirmed service-reference links: cloud list + what this receiver learned (local wins)."""
    out = dict(_load(LINKED_REFS_FILE, {}))
    out.update(load_learned()['refs'])
    return out


def learn_refs(pairs):
    """remember confirmed links by service reference: [(ref, key), ...]"""
    d = load_learned()
    n = 0
    for ref, key in pairs:
        rk = ref_key(ref)
        if rk and key and key != '-' and d['refs'].get(rk) != key:
            d['refs'][rk] = key
            n += 1
    if n:
        _save(LEARN_FILE, d)
    return n


def learn(name, key, ref=None):
    """the user linked this channel name to key: use it for every channel with that name."""
    d = load_learned()
    n = norm(name)
    if not n or not key or key in ('-', 'auto'):
        return
    if ref and ref_key(ref):
        d['refs'][ref_key(ref)] = key
    d['alias'][n] = key
    d['reject'] = [r for r in d['reject'] if r != [n, key]]
    _save(LEARN_FILE, d)


def reject_auto(name, key):
    """the satellite guide proved this automatic link wrong: do not use it for 7 days, then check again."""
    d = load_learned()
    n = norm(name)
    if n and key:
        d.setdefault('auto_reject', {})['%s|%s' % (n, key)] = int(time.time())
        _save(LEARN_FILE, d)


def mark_wrong(ref, name):
    """the user says the guide on this channel is wrong. -> 'plugin' (a plugin guide was removed and its
    source will not be used again for this name) or 'satellite' (the guide comes from the satellite)."""
    st = load_state()
    f = st['filled'].get(ref)
    if not f:
        return 'satellite'
    key = u(f.get('key', ''))
    if key.startswith('copy:'):
        st['no_copy'] = list(set(st.get('no_copy', []) + [ref]))
    elif key:
        reject(name, key)
        unlearn_ref(ref, key)
        m = load_mapping()
        if m.get(ref) == key:
            del m[ref]
            save_mapping(m)
    st['filled'].pop(ref, None)
    save_state(st)
    log(u'أبلغت أن دليل %s خطأ: تم إلغاء المصدر %s' % (name, display_key(key)))
    return 'plugin'


def unlearn_ref(ref, key):
    """forget an automatic confirmation of this service (the satellite guide proved it wrong)."""
    d = load_learned()
    rk = ref_key(ref)
    if rk and d['refs'].get(rk) == key:
        del d['refs'][rk]
        _save(LEARN_FILE, d)


def reject(name, key):
    """the user replaced this automatic link: never suggest it again for that name."""
    d = load_learned()
    n = norm(name)
    if n and key and [n, key] not in d['reject']:
        d['reject'].append([n, key])
        if d['alias'].get(n) == key:
            del d['alias'][n]
        _save(LEARN_FILE, d)


# --------------------------------------------------------------------- service reference database
REFS_FILE = os.path.join(DATA_DIR, 'refs.json')
_REFS = {'sig': None, 'data': {}}


def load_refs():
    """{'<orbital>:<SID>:<TSID>:<ONID>': [epg ids]} - built-in snapshot, or the newer online copy."""
    sig = _mtime(REFS_FILE)
    if _REFS['sig'] != sig:
        built_in = _load(os.path.join(PLUGIN_DIR, 'refs.json'), {})
        online = _load(REFS_FILE, {})
        _REFS['data'] = online if len(online) >= len(built_in) // 2 and online else built_in
        _REFS['sig'] = sig
    return _REFS['data']


def ref_key(ref):
    p = u(ref).rstrip(':').split(':')
    try:
        if p[0] != '1' or len(p) < 7:
            return None
        return u'%X:%X:%X:%X' % (int(p[6], 16) >> 16, int(p[3], 16), int(p[4], 16), int(p[5], 16))
    except (ValueError, IndexError):
        return None


def clean_epg_id(x):
    x = re.sub(r'\.[a-z]{2}$', '', u(x).strip())
    x = re.sub(r'^(logos-_|20\d\d[ _])', '', x)
    x = re.sub(r'[_.\-]+', ' ', x)
    return re.sub(r'\s+', ' ', x).strip()


# --------------------------------------------------------------------- smart linking
def _allow_for(enabled, sat):
    enabled = set(enabled)
    xml_ok = xml_files_for_sat(sat) if 'xml' in enabled else set()
    if 'xml' in enabled and sat in ('iptv', 'other', 'terrestrial'):
        xml_ok = set('xml:' + f for f in (load_index().get('files') or {}))

    def ok(key):
        src = source_of(key)
        if src.startswith('xml:'):
            return src in xml_ok
        return src in enabled
    return ok


def load_auto():
    """the cached automatic links -> {ref: [(key, score), ...]}"""
    out = {}
    for k, v in _load(AUTO_FILE, {}).items():
        if k != '_stamp' and isinstance(v, list) and len(v) > 1 and isinstance(v[1], list):
            out[k] = [tuple(x) for x in v[1] if isinstance(x, list) and len(x) == 2]
    return out


def _home_rank(idx, key, sat):
    if not key.startswith('xml:'):
        return 0
    f = (idx.get('files') or {}).get(key[4:].split('/', 1)[0]) or {}
    sats = f.get('sat') or []
    return 0 if sats and sats[0] == sat else 1


def smart_link(services, enabled, force=False, min_score=0.86):
    """automatic candidates for every service -> {ref: [(key, score), ...]} (cached)."""
    sats = list(user_satellites(services))
    cache = {} if force else _load(AUTO_FILE, {})
    idx = load_index()
    learned = load_learned()
    stamp = u'%d|%s|%d|%d|%s|%s|%d|%d' % (
        MATCHER_VERSION, u','.join(sorted(enabled)), len(catalog()), len(idx.get('files') or {}),
        idx.get('generated', 0) // 86400, u','.join(sorted(sats)), int(_mtime(LEARN_FILE)), int(min_score * 100))
    stamp += u'|%d' % int(_mtime(LINKED_REFS_FILE))
    stamp += u'|%d' % len(load_refs())
    # every entry carries its own stamp: an update of a few channels must not throw away the others
    m = matcher(sats)
    refs = load_refs()
    xml_ids = {}
    for fname, f in (idx.get('files') or {}).items():
        if f.get('kind') == 'xmltv':
            for cid, _n in f.get('channels') or []:
                xml_ids.setdefault(cid, u'xml:%s/%s' % (fname, cid))
    rejected = set(tuple(r) for r in learned['reject'])
    for k, t in (learned.get('auto_reject') or {}).items():
        if time.time() - t < 7 * 86400:
            rejected.add(tuple(k.split('|', 1)))
    confirmed_refs = linked_refs()
    manual_keys = set(learned['alias'].values()) | set(v for v in load_mapping().values() if v != '-')
    out, changed = {}, False
    allow_cache = {}
    for ref, name in services:
        hit = cache.get(ref)
        if hit and hit[0] == name and len(hit) > 2 and hit[2] == stamp:
            out[ref] = [tuple(x) for x in hit[1]]
            continue
        sat = sat_of_ref(ref)
        if sat not in allow_cache:
            allow_cache[sat] = _allow_for(enabled, sat)
        ok = allow_cache[sat]
        n = norm(name)
        cands = m.match_all(name, ok, min_score=min_score, limit=5)
        # exact help from the service reference database (same SID/TSID/ONID on the same satellite)
        ids = refs.get(ref_key(ref) or '', ())
        if ids:
            by_ref = {}
            for i in ids:
                k = xml_ids.get(i)
                if k and ok(k):
                    by_ref[k] = 1.0
            for cn in set(clean_epg_id(i) for i in ids):
                for k, sc in m.match_all(cn, ok, min_score=0.99, limit=4):
                    if sc >= 1.0 and k not in by_ref:
                        by_ref[k] = 0.99
            if by_ref:
                confirmed = [(k, 1.0) for k, _s in cands if k in by_ref]
                rest = [c for c in cands if c[0] not in by_ref]
                # the database is old for many Nilesat services (frequencies get reused by other channels):
                # its link is only used when the channel name also looks like that online channel
                extra = sorted(((k, sc) for k, sc in by_ref.items() if k not in dict(cands) and name_close(name, k)),
                               key=lambda kv: (-kv[1], SOURCE_ORDER.index(kv[0].split(':')[0])
                                               if kv[0].split(':')[0] in SOURCE_ORDER else 99))
                cands = confirmed + extra + rest   # the service id beats a name that only looks alike
        cands = [c for c in cands if (n, c[0]) not in rejected]
        # equal scores: the guide made for this satellite first (Israeli file before the Ukrainian one on Amos)
        cands = sorted(cands, key=lambda c: (-round(c[1], 3), _home_rank(idx, c[0], sat)))
        lk = learned['alias'].get(n)
        if lk and ok(lk) and (not lk.startswith('xml:') or catalog_entry(lk)):
            cands = [(lk, 1.0)] + [c for c in cands if c[0] != lk]
        rk = confirmed_refs.get(ref_key(ref) or '')
        if rk and rk.startswith('xml:') and not catalog_entry(rk):
            rk = None       # that channel is no longer offered (e.g. removed by the cloud as a copied schedule)
        if rk and rk not in manual_keys and not name_close(name, rk):
            rk = None       # an old automatic confirmation that does not even look like this channel
        if rk and ok(rk):   # this exact service was confirmed before (by you, by the satellite guide, or the cloud)
            cands = [(rk, 1.0)] + [c for c in cands if c[0] != rk]
        if not cands and 'xml' in enabled and sat not in ('iptv', 'other', 'terrestrial'):
            cands = wide_match(name, idx, rejected, sat)
        cands = cands[:4]
        cache[ref] = [name, [list(x) for x in cands], stamp]
        out[ref] = cands
        changed = True
    if changed:
        cache['_stamp'] = stamp
        _save(AUTO_FILE, cache)
    return out


FP_FILE = os.path.join(DATA_DIR, 'fp_links.json')


def title_hash(t):
    import hashlib
    n = norm(t)
    return hashlib.md5(n.encode('utf-8')).hexdigest()[:6] if n else u''


class FPLinker(object):
    """links a channel that has no name match by its schedule: the satellite's own programmes of the next hours
    start at the same minutes (with the same titles, when the language is the same) as a channel in the cloud."""

    def __init__(self, cfg, enabled):
        self.cfg, self.enabled = cfg, enabled
        self.cache = _load(FP_FILE, {})
        self.by_min = None
        self.allow = {}
        self.changed = False
        self.found = 0

    def _index(self):
        if self.by_min is None:
            self.by_min = {}
            try:
                if (load_index().get('fp') and self.cfg.get('cloud') and
                        u(self.cfg.get('cloud_url', '')).startswith('http')):
                    data = json.loads(sources.http_get(self.cfg['cloud_url'] + 'fp.json.gz', timeout=90).decode('utf-8'))
                    for key, marks in (data.get('fp') or {}).items():
                        for m, h in marks:
                            self.by_min.setdefault(m, []).append((key, h))
            except Exception as e:
                log(u'بصمة الجدول: %s' % e)
        return self.by_min

    def find(self, ref, name, native):
        now = time.time()
        c = self.cache.get(ref)
        if c and now - c[2] < 7 * 86400 and c[3] == name:
            return c[0]
        if not native or len(native) < 4:
            return None
        idx = self._index()
        if not idx:
            return None
        sat = sat_of_ref(ref)
        if sat not in self.allow:
            self.allow[sat] = _allow_for(self.enabled, sat)
        ok = self.allow[sat]
        nat = [(int(b // 60), title_hash(t)) for b, _d, t in native if b + 600 > now]
        if len(nat) < 4:
            return None
        score = {}
        for m, h in nat:
            seen = set()
            for dm in (-2, -1, 0, 1, 2):
                for key, kh in idx.get(m + dm, ()):
                    if key in seen:
                        continue
                    seen.add(key)
                    x = score.setdefault(key, [0, 0, 0])
                    x[0] += 1
                    if 3 <= m % 30 <= 27:
                        x[1] += 1
                    if h and h == kh:
                        x[2] += 1
        best, passing = None, []
        for key, (hit, dist, tit) in score.items():
            if hit < max(4, 0.6 * len(nat)) or (dist < 3 and tit < 0.4 * hit) or not ok(key):
                continue
            passing.append(key)
            val = hit + 2 * tit + dist
            if best is None or val > best[1]:
                best = (key, val, hit, tit)
        if not best:
            return None
        if best[3] < 0.4 * best[2]:
            # times only (the titles are in another language): several different channels with these times
            # (copied schedules in some files) -> not sure which one it is
            names = set(norm(display_key(k).split(u': ')[-1]) for k in passing)
            if len(names) > 1:
                return None
        self.cache[ref] = [best[0], best[1], int(now), name]
        self.changed = True
        self.found += 1
        log(u'ربط بالجدول (نفس البرامج في نفس المواعيد): %s ← %s (%d برنامج، %d عنوان)' % (
            name, display_key(best[0]), best[2], best[3]))
        return best[0]

    def save(self):
        if self.changed:
            now = time.time()
            _save(FP_FILE, dict((r, v) for r, v in self.cache.items() if now - v[2] < 14 * 86400))
        if self.found:
            log(u'قنوات رُبطت ببصمة الجدول: %d' % self.found)


def resolve(ref, mapping, auto):
    """-> (candidates [(key, score)], manual)"""
    k = mapping.get(ref)
    if k in ('-', '!'):     # '-' plugin off for this channel, '!' also hide the satellite's own (wrong) guide
        return [('-', 0)], True
    if k and k.startswith('chan:'):     # use the guide of another channel of the receiver (chosen by hand)
        return [(k, 1.0)], True
    if k and source_of(k).split(':')[0] in SOURCE_ORDER:
        return [(k, 1.0)], True
    out = [c for c in (auto.get(ref) or []) if c[0].split(':')[0] in SOURCE_ORDER]
    if not out:
        fp = _fp_links().get(ref)
        if fp:
            out = [(fp[0], 0.93)]
    if not out:      # the satellite's own guide of this very service, kept by the cloud (sent by your receiver)
        k = ref_key(ref)
        if k and (u'crowd.' + k) in crowd_ids():
            out = [(u'xml:crowd_ar/crowd.' + k, 0.99)]
    return out, False


_CROWD = {'sig': None, 'ids': set()}


def crowd_ids():
    idx = load_index()
    sig = idx.get('generated')
    if _CROWD['sig'] != sig:
        f = (idx.get('files') or {}).get('crowd_ar') or {}
        _CROWD['sig'], _CROWD['ids'] = sig, set(c for c, _n in f.get('channels') or [])
    return _CROWD['ids']


_FPL = {'sig': None, 'data': {}}


def _fp_links():
    sig = _mtime(FP_FILE)
    if _FPL['sig'] != sig:
        _FPL['sig'], _FPL['data'] = sig, _load(FP_FILE, {})
    return _FPL['data']


# --------------------------------------------------------------------- enigma2 side
def clean_ref(ref):
    parts = u(ref).split(':')
    if len(parts) < 10:
        return None
    return u':'.join(parts[:10]) + u':'


def get_bouquets():
    """[(bouquet name, [(ref, name), ...]), ...] for all TV bouquets. Main thread only."""
    from enigma import eServiceCenter, eServiceReference
    sc = eServiceCenter.getInstance()
    out = []
    root = eServiceReference('1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "bouquets.tv" ORDER BY bouquet')
    lst = sc.list(root)
    for bref, bname in (lst and lst.getContent('SN', True) or []):
        items, seen = [], set()
        sl = sc.list(eServiceReference(bref))
        for sref, sname in (sl and sl.getContent('SN', True) or []):
            p = u(sref).split(':')
            try:
                if len(p) < 10 or int(p[1] or 0) & 0x40 or p[1] == '7' or p[3] in ('0', ''):
                    continue
            except ValueError:
                continue
            r = clean_ref(sref)
            if r and r not in seen:
                seen.add(r)
                items.append((r, u(sname).replace(u'\x86', u'').replace(u'\x87', u'').strip()))
        out.append((u(bname).replace(u'\x86', u'').replace(u'\x87', u'').strip(), items))
    return out


def get_services(bouquets=None):
    """All TV services from the user's bouquets: [(ref, name)] (deduplicated). Main thread only."""
    out, seen = [], set()
    for _b, items in (bouquets if bouquets is not None else get_bouquets()):
        for r, n in items:
            if r not in seen:
                seen.add(r)
                out.append((r, n))
    return out


def bouquet_stats(bouquets, state, auto=None, mapping=None, rich=None):
    """per bouquet: channels, linked, updated by the plugin, programmes imported."""
    filled = state.get('filled', {})
    rows = []
    for bname, items in bouquets:
        n = len(items)
        upd = [filled[r] for r, _n in items if r in filled]
        ev = sum(x.get('n', 0) for x in upd)
        linked = None
        if auto is not None:
            linked = 0
            for r, _n in items:
                c, _m = resolve(r, mapping or {}, auto)
                linked += 1 if c and c[0][0] != '-' else 0
        sat = len([1 for r, _n in items if rich and rich.get(r)]) if rich is not None else None
        rows.append({'name': bname, 'channels': n, 'updated': len(upd), 'events': ev,
                     'linked': linked, 'native': sat})
    return rows


def _enc(s):
    if PY3:
        return s
    return s.encode('utf-8') if isinstance(s, unicode) else s  # noqa: F821


def epg_count(epgcache, ref, hours=12):
    try:
        res = epgcache.lookupEvent(['B', (_enc(ref), 0, int(time.time()), hours * 60)])
        return len([x for x in (res or []) if x and x[0]])
    except Exception:
        return 0


def now_next(epgcache, ref):
    """-> ((title, begin, duration), (title, begin, duration)) or None parts"""
    try:
        res = epgcache.lookupEvent(['TBD', (_enc(ref), 0, -1, -1), (_enc(ref), 1, -1, -1)])
    except Exception:
        return None, None
    out = []
    for x in (res or [])[:2]:
        out.append((u(x[0]), x[1], x[2]) if x and x[0] else None)
    while len(out) < 2:
        out.append(None)
    return out[0], out[1]


def epg_triple(ref):
    """the receiver keeps the guide per (service id, transport id, network id) - NOT per frequency."""
    p = u(ref).split(':')
    try:
        return int(p[3], 16), int(p[4], 16), int(p[5], 16)
    except (ValueError, IndexError):
        return None


def triple_conflicts(services):
    """services that share their guide slot with a DIFFERENT channel (or have no service id):
    a guide imported for one of them would also appear on the others."""
    slots = {}
    for ref, name in services:
        t = epg_triple(ref)
        if t:
            slots.setdefault(t, []).append(name)
    shared = {}
    for t, names in slots.items():
        uniq = []
        for n in names:
            if PLACEHOLDER.match(u(n or u'')):
                continue          # "<n/a>", "Service-1", "Test": not a channel anyone watches
            if not any(name_like(n, x) for x in uniq):    # "beIN SPORTS" and "beIN Sports HD" are one channel
                uniq.append(n)
        if len(uniq) > 1:
            shared[t] = uniq
    bad = set()
    CONFLICT_WITH.clear()
    for ref, n in services:
        t = epg_triple(ref)
        if not t or t[0] == 0 or t in shared:
            bad.add(ref)
            if t in shared:
                CONFLICT_WITH[ref] = [x for x in shared[t] if not name_like(x, n)]
    return bad


PLACEHOLDER = re.compile(u'(?i)^\\s*(<?n/a>?|service[\\s_-]*\\d*|test([\\s_-]*\\w{0,3})?|\\(\\.\\.\\.\\)|\\.+|-+|)\\s*$')
CONFLICT_WITH = {}       # ref -> names of the other channels in its guide slot


def import_events(epgcache, refs, events):
    if not events:
        return 0
    evs = [(int(e[0]), int(e[1]), _enc(e[2]), _enc(e[3]), _enc(e[4]), int(e[5])) for e in events]
    refs = [_enc(r) for r in refs]
    if hasattr(epgcache, 'importEvents'):
        epgcache.importEvents(refs, evs)
    else:
        for r in refs:
            epgcache.importEvent(r, evs)
    return len(evs)


# --------------------------------------------------------------------- backup cache
TIMES_FILE = os.path.join(DATA_DIR, 'our_times.json')


def our_times(key):
    """(begin, duration) of what the plugin imported for an online channel; without reading the big backup"""
    if _OT['sig'] != _mtime(TIMES_FILE):
        _OT['sig'], _OT['data'] = _mtime(TIMES_FILE), _load(TIMES_FILE, None)
    if _OT['data'] is None:
        return None
    return set((b, d) for b, d in _OT['data'].get(key) or [])


_OT = {'sig': None, 'data': None}


class Backup(object):
    """last good schedule per online channel, used when every source fails.
    One small file per channel source (cache/backup/), read only when needed: the old single big file
    took seconds to read, and the receiver froze meanwhile."""

    def __init__(self):
        self.dir = os.path.join(cache_dir(), 'backup')
        self.data, self.dirty = {}, set()
        old = os.path.join(cache_dir(), 'events.json.gz')
        if os.path.exists(old):
            try:
                os.remove(old)      # the old big file: the next updates fill the new folder
            except OSError:
                pass

    def _path(self, key):
        import hashlib
        return os.path.join(self.dir, hashlib.md5(u(key).encode('utf-8')).hexdigest()[:16] + '.json')

    def _all(self, key):
        if key not in self.data:
            self.data[key] = _load(self._path(key), [])
        return self.data[key]

    def get(self, key):
        now = time.time()
        return [tuple(e) for e in self._all(key) if e[0] + e[1] > now]

    def put(self, key, events):
        now = time.time()
        old = [e for e in self._all(key) if e[0] + e[1] > now and e[0] < events[0][0]] if events else []
        self.data[key] = old + [[e[0], e[1], e[2], e[3], e[4][:300], e[5]] for e in events]
        self.dirty.add(key)

    def save(self):
        if not self.dirty:
            return
        now = time.time()
        times = _load(TIMES_FILE, {})
        try:
            if not os.path.isdir(self.dir):
                os.makedirs(self.dir)
            for k in self.dirty:
                evs = [e for e in self.data.get(k) or [] if e[0] + e[1] > now]
                p = self._path(k)
                if evs:
                    _save(p, evs)
                    times[k] = [[int(e[0]), int(e[1])] for e in evs]
                else:
                    times.pop(k, None)
                    if os.path.exists(p):
                        os.remove(p)
            for fn in os.listdir(self.dir):          # files of channels not updated for 8 days
                fp = os.path.join(self.dir, fn)
                if now - _mtime(fp) > 8 * 86400:
                    os.remove(fp)
        except Exception as e:
            log(u'تعذر حفظ النسخة الاحتياطية: %s' % e)
        # only the times, for the link check at the start of every update
        _save(TIMES_FILE, dict((k, v) for k, v in times.items() if v and v[-1][0] + v[-1][1] > now))
        self.dirty = set()


# --------------------------------------------------------------------- fetching (cloud first, then direct)
class Fetcher(object):
    def __init__(self, cfg, stop=None, step=None):
        self.cfg = cfg
        self.stop = stop
        self._tl = threading.local()
        self._default_step = step or (lambda n=1: None)
        self._locks = {}
        self._lock = threading.Lock()
        self.cloud_files = {}   # fname -> local path
        self.index = load_index()
        self.cloud_ids = {}
        for fname, f in (self.index.get('files') or {}).items():
            self.cloud_ids[fname] = set(c[0] for c in f.get('channels') or [])

    @property
    def step(self):
        return getattr(self._tl, 'step', None) or self._default_step

    @step.setter
    def step(self, fn):
        self._tl.step = fn

    def _cloud_file(self, fname):
        with self._lock:
            lk = self._locks.setdefault(fname, threading.Lock())
        with lk:
            return self._cloud_file_locked(fname)

    def _cloud_file_locked(self, fname):
        if fname in self.cloud_files:
            return self.cloud_files[fname]
        f = (self.index.get('files') or {}).get(fname)
        path = None
        if f:
            url = f['url']
            if url.startswith('file://'):
                self.cloud_files[fname] = url[7:]
                return url[7:]
            if not url.startswith('http'):
                url = self.cfg['cloud_url'] + url
            d = os.path.join(cache_dir(), 'files')
            path = os.path.join(d, fname + '.xml.gz')
            have = os.path.getsize(path) if os.path.exists(path) else 0
            if f.get('size'):   # the cloud says how big its current file is: a new build = a new download
                fresh = have == f['size']
            else:
                fresh = have > 100 and time.time() - _mtime(path) < 6 * 3600
            if not fresh:
                try:
                    if not os.path.isdir(d):
                        os.makedirs(d)
                    sources.http_download(url, path + '.part', timeout=180, stop=self.stop)
                    os.rename(path + '.part', path)
                except Exception as e:
                    log(u'تعذر تحميل ملف %s (%s)' % (fname, e))
                    path = path if os.path.exists(path) else None   # an older copy is better than none
        self.cloud_files[fname] = path
        return path

    # ---- per-channel files: only your channels are downloaded (a few KB each, several at a time)
    def has_chunks(self, fname):
        f = (self.index.get('files') or {}).get(fname) or {}
        return bool(f.get('ch') and self.index.get('chunks') and
                    u(self.cfg.get('cloud_url', '')).startswith('http'))

    def chunks(self, pairs, progress=True):
        """[(fname, cid)] -> ({(fname, cid): events}, set of files that should be read whole instead)"""
        import hashlib
        try:
            from urllib.parse import quote
        except ImportError:
            from urllib import quote
        base = self.cfg['cloud_url']
        pat = (self.index.get('chunks') or {}).get('path') or 'ch/%s/%s.json.gz'
        days = self.cfg['days']
        lower, limit = time.time() - 6 * 3600, time.time() + days * 86400
        prefer_en = sources.XML_PREFER_EN
        todo = list(pairs)
        res, errors, lock = {}, {}, threading.Lock()
        step = self.step
        # only what changed: the cloud lists an md5 per channel, the copies of the last update are kept
        mpat = (self.index.get('chunks') or {}).get('manifest')
        cdir = chunk_cache_dir()
        remote, local = {}, {}
        for fname in sorted(set(f for f, _c in todo)):
            if not mpat or not (self.index.get('files') or {}).get(fname, {}).get('chm'):
                continue
            try:
                remote[fname] = json.loads(sources.http_get(
                    base + mpat % quote(fname.encode('utf-8') if not PY3 else fname), timeout=30).decode('utf-8'))
                local[fname] = _load(os.path.join(cdir, fname, '_m.json'), {})
            except Exception as e:
                log(u'السحابة: قائمة التغييرات %s: %s' % (fname, e))
        stats = self.stats_chunks = {'same': 0, 'new': 0}

        def one(fname, cid):
            h = hashlib.sha1(cid.encode('utf-8') if not isinstance(cid, bytes) else cid).hexdigest()[:16]
            want = (remote.get(fname) or {}).get(h)
            obj = None
            if want and (local.get(fname) or {}).get(h) == want:
                try:
                    with open(os.path.join(cdir, fname, h + '.json'), 'rb') as fh:
                        obj = json.loads(fh.read().decode('utf-8'))
                    with lock:
                        stats['same'] += 1
                except Exception:
                    obj = None
            if obj is None:
                if fname in remote and not want:
                    return []         # not in the list: no programmes for this channel in the cloud
                url = base + pat % (quote(fname.encode('utf-8') if not PY3 else fname), h)
                try:
                    body = sources.http_get(url, timeout=40)
                except Exception as e:
                    if '404' in str(e):
                        return []         # no programmes for this channel in the cloud
                    raise
                obj = json.loads(body.decode('utf-8'))
                with lock:
                    stats['new'] += 1
                if want:
                    try:
                        d = os.path.join(cdir, fname)
                        if not os.path.isdir(d):
                            os.makedirs(d)
                        with open(os.path.join(d, h + '.json'), 'wb') as fh:
                            fh.write(body)
                        with lock:
                            local.setdefault(fname, {})[h] = hashlib.md5(body).hexdigest()[:12]
                    except Exception:
                        pass
            en = obj.get('en') or {}
            out = []
            for i, e in enumerate(obj.get('e') or []):
                st, dur = int(e[0]), int(e[1])
                if st + dur <= lower or st >= limit:
                    continue
                title, desc = e[2], e[4]
                if prefer_en and str(i) in en:
                    title, desc = en[str(i)][0] or title, en[str(i)][1] or desc
                out.append((st, dur, title, e[3], desc, int(e[5] if len(e) > 5 else 0)))
            return out

        def worker():
            while True:
                with lock:
                    if not todo or (self.stop and self.stop()):
                        return
                    fname, cid = todo.pop()
                try:
                    evs = one(fname, cid)
                    with lock:
                        res[(fname, cid)] = evs
                except Exception as e:
                    with lock:
                        errors.setdefault(fname, []).append(str(e)[:80])
                if progress:
                    step(1)
        threads = [threading.Thread(target=worker) for _i in range(min(8, max(1, len(todo))))]
        for t in threads:
            t.daemon = True
            t.start()
        for t in threads:
            t.join()
        for fname, m in local.items():
            if fname in remote:
                _save(os.path.join(cdir, fname, '_m.json'), dict((h, v) for h, v in m.items() if h in remote[fname]))
                try:
                    for x in os.listdir(os.path.join(cdir, fname)):
                        if x.endswith('.json') and x != '_m.json' and x[:-5] not in remote[fname]:
                            os.remove(os.path.join(cdir, fname, x))
                except Exception:
                    pass
        if stats['same']:
            log(u'السحابة: %d قناة لم يتغير دليلها (من النسخة المحفوظة)، %d تم تنزيلها' % (stats['same'], stats['new']))
        per_file = {}
        for fname, _c in pairs:
            per_file[fname] = per_file.get(fname, 0) + 1
        bad = set(f for f, errs in errors.items() if len(errs) > max(2, per_file.get(f, 0) // 5))
        if errors:
            log(u'السحابة (ملفات القنوات): تعذر تحميل %d قناة%s' % (
                sum(len(v) for v in errors.values()),
                (u' - سيُقرأ الملف كاملا: %s' % u'، '.join(sorted(bad))) if bad else u''))
        return res, bad

    def from_cloud(self, fname, ids):
        if self.has_chunks(fname):
            got, bad = self.chunks([(fname, c) for c in ids], progress=False)
            if fname not in bad:
                return dict((c, evs) for (_f, c), evs in got.items())
        path = self._cloud_file(fname)
        if not path:
            return {}
        try:
            res, _n = sources.parse_xmltv_file(path, set(ids), self.cfg['days'], self.stop)
            return res
        except Exception as e:
            log(u'السحابة: خطأ في قراءة %s (%s)' % (fname, e))
            return {}

    def cleanup(self):
        pass   # files stay in the cache folder for 6 hours

    def fetch(self, src, ids):
        """-> {id: events} for one source; cloud first, missing ones from the site itself."""
        cfg, days, res = self.cfg, self.cfg['days'], {}
        ids = list(ids)
        if src == 'xml':
            byfile = {}
            for i in ids:
                fname, _, cid = i.partition('/')
                byfile.setdefault(fname, []).append(cid)
            fast = [(f, c) for f, cids in byfile.items() if self.has_chunks(f) for c in cids]
            got, bad = self.chunks(fast) if fast else ({}, set())
            for (f, c), evs in got.items():
                if f not in bad:
                    res[f + '/' + c] = evs
            for fname, cids in byfile.items():
                if self.has_chunks(fname) and fname not in bad:
                    continue
                path = self._cloud_file(fname)       # the old way: the whole file
                if path:
                    try:
                        whole, _n = sources.parse_xmltv_file(path, set(cids), self.cfg['days'], self.stop)
                    except Exception as e:
                        log(u'السحابة: خطأ في قراءة %s (%s)' % (fname, e))
                        whole = {}
                    for cid, evs in whole.items():
                        res[fname + '/' + cid] = evs
                if not self.has_chunks(fname):
                    self.step(len(cids))
            return res
        if src == 'xmltv':
            for url in cfg['xmltv_urls']:
                try:
                    got, _n = sources.grab_xmltv(url, set(ids), days, self.stop)
                    res.update(got)
                except Exception as e:
                    log(u'XMLTV خطأ (%s): %s' % (url, e))
            self.step(len(ids))
            return res
        # Arabic sites: cloud copy first
        fname = SITE_FILE.get(src)
        if cfg.get('cloud') and fname in self.cloud_ids:
            want = [i for i in ids if '%s.%s' % (src, i) in self.cloud_ids[fname]]
            if want:
                for cid, evs in self.from_cloud(fname, ['%s.%s' % (src, i) for i in want]).items():
                    if evs:
                        res[cid.split('.', 1)[1]] = evs
                self.step(len([i for i in ids if i in res]))
        missing = [i for i in ids if not res.get(i)]
        if missing and src in cfg['sources']:
            res.update(self.direct(src, missing))
        return res

    def direct(self, src, ids):
        cfg, days, stop, res = self.cfg, self.cfg['days'], self.stop, {}

        def chunks(lst, n):
            for i in range(0, len(lst), n):
                yield lst[i:i + n]

        if src == 'shahid':
            for part in chunks(ids, 10):
                res.update(sources.grab_shahid(part, days, cfg['lang'], cfg['country'], stop))
                self.step(len(part))
        elif src == 'osn':
            for part in chunks(ids, 5):
                res.update(sources.grab_osn(part, days, cfg['lang'], stop))
                self.step(len(part))
        elif src == 'elcinema':
            for part in chunks(ids, 8):
                res.update(sources.grab_elcinema(part, days, cfg['elcinema_offset'], stop, workers=4))
                self.step(len(part))
        elif src == 'beinsports':
            res.update(sources.grab_beinsports(ids, days, stop))
            self.step(len(ids))
        elif src == 'roya':
            res.update(sources.grab_roya(ids, days, stop))
            self.step(len(ids))
        elif src == 'art':
            res.update(sources.grab_art(ids, days, stop))
            self.step(len(ids))
        elif src == 'ayn':
            res.update(sources.grab_ayn(ids, days, stop))
            self.step(len(ids))
        elif src == 'snrt':
            res.update(sources.grab_snrt(ids, days, stop))
            self.step(len(ids))
        elif src == 'lbci':
            res.update(sources.grab_lbci(ids, days, stop))
            self.step(len(ids))
        elif src == 'tunisia':
            res.update(sources.grab_tunisia(ids, days, stop))
            self.step(len(ids))
        elif src == 'trt':
            res.update(sources.grab_trt(ids, days, stop))
        elif src == 'presstv':
            res.update(sources.grab_presstv(ids, days, stop))
            self.step(len(ids))
        elif src == 'bein':
            res.update(sources.grab_bein(ids, days, cfg['lang'], stop))
            self.step(len(ids))
        elif src == 'rotana':
            for part in chunks(ids, 3):
                res.update(sources.grab_rotana(part, days, cfg['lang'], stop))
                self.step(len(part))
        elif src == 'aljazeera':
            res.update(sources.grab_aljazeera(ids, days, stop))
            self.step(len(ids))
        return res


ARABIC_SOURCES = ('shahid', 'osn', 'bein', 'beinsports', 'rotana', 'aljazeera', 'roya', 'art', 'ayn', 'snrt',
                  'lbci', 'tunisia', 'trt', 'elcinema')


def _is_arabic_channel(ref, name, keys):
    """channels whose guide should be in Arabic (not the English / French feeds)."""
    low = u(name).lower()
    if re.search(u'(english|french|\\ben\\b|\\bfr\\b|anglais|إنجليزي|انجليزي)', low) or \
            any(k == 'aljazeera:aje#' for k in keys[:1]):
        return False
    if re.search(u'[\u0600-\u06ff]', name):
        return True
    if sat_of_ref(ref) in ('nilesat', 'arabsat'):
        return True
    if not keys:
        return False
    return keys[0].split(':')[0] in ARABIC_SOURCES


_FLUSH = {'how': None, 'logged': False}


def clear_plugin_epg(epgcache, refs, everything=False):
    """remove the guide of the given services (or the whole EPG cache). -> (cleared, method)
    The way to clear ONE service differs between images; the first one that works is remembered."""
    if everything:
        epgcache.flushEPG()
        return len(refs), 'all'
    if not _FLUSH['logged']:
        _FLUSH['logged'] = True
        try:
            log(u'flushEPG: %s' % (u(getattr(epgcache.flushEPG, '__doc__', '') or u'')[:300].replace(u'\n', u' ')))
        except Exception:
            pass
    from enigma import eServiceReference

    def ways(r):
        p = r.split(':')
        sid, tsid, onid = int(p[3], 16), int(p[4], 16), int(p[5], 16)
        return (('ints', lambda: epgcache.flushEPG(sid, onid, tsid)),
                ('sref', lambda: epgcache.flushEPG(eServiceReference(_enc(r)))),
                ('str', lambda: epgcache.flushEPG(_enc(r))))
    done = 0
    for r in refs:
        try:
            options = ways(r)
        except (ValueError, IndexError):
            continue
        ok = False
        for name, fn in options:
            if _FLUSH['how'] not in (None, name):
                continue
            try:
                fn()
                _FLUSH['how'] = name
                ok = True
                break
            except Exception:
                continue
        if not ok:
            return done, None   # this image cannot clear a single service
        done += 1
    return done, 'service'


# --------------------------------------------------------------------- lock: the plugin's guide stays
# The satellite sends two things: its schedule (EIT), which overwrote the imported guide in the cache, and the
# now/next of the channel being watched, which the infobar shows before the cache. Channels filled by the
# plugin are put in the receiver's own EIT blacklist (/etc/enigma2/blacklist.eit, read by enigma2 itself),
# and the infobar is told to take now/next from the cache for them (see plugin.patch_now_next).
EIT_BLACKLIST = '/etc/enigma2/blacklist.eit'
EIT_OURS = os.path.join(DATA_DIR, 'eit_locked.json')
LOCK = {'refs': None, 'dirty': False, 'on': True, 'ok': None}


def locked_refs():
    if LOCK['refs'] is None:
        LOCK['refs'] = set(_load(EIT_OURS, [])) if LOCK['on'] else set()
    return LOCK['refs']


def is_locked(ref):
    return bool(ref) and LOCK['on'] and clean_ref(ref) in locked_refs()


def lock_wanted(state, now=None):
    """channels whose guide comes from the plugin and still runs for a while"""
    now = now or time.time()
    return sorted(r for r, f in (state.get('filled') or {}).items()
                  if f.get('until', 0) > now + 3600 and f.get('n', 1))


def eit_lock(refs, enabled=True):
    """write our channels into blacklist.eit (lines written by others stay). -> number locked"""
    ours_old = set(_load(EIT_OURS, []))
    try:
        with open(EIT_BLACKLIST) as f:
            lines = [u(x.strip()) for x in f.read().splitlines()]
    except (IOError, OSError):
        lines = []
    others = [x for x in lines if x and x not in ours_old]
    want = sorted(set(clean_ref(r) for r in refs if clean_ref(r))) if enabled else []
    keep = set(others)
    new = others + [r for r in want if r not in keep]
    LOCK['on'] = enabled
    LOCK['refs'] = set(want)
    if new != [x for x in lines if x]:
        try:
            tmp = EIT_BLACKLIST + '.tmp'
            with open(tmp, 'wb') as f:
                f.write((u'\n'.join(new) + (u'\n' if new else u'')).encode('utf-8'))
            os.rename(tmp, EIT_BLACKLIST)
            LOCK['dirty'] = True
        except Exception as e:
            log(u'تثبيت الدليل: تعذرت كتابة %s (%s)' % (EIT_BLACKLIST, e))
    _save(EIT_OURS, want)
    return len(want)


def eit_reload():
    """main thread: enigma2 reads blacklist.eit again. -> True / False (image without this function)"""
    LOCK['dirty'] = False
    try:
        from enigma import eEPGCache
        e = eEPGCache.getInstance()
        if hasattr(e, 'reloadEITConfig'):
            e.reloadEITConfig(getattr(eEPGCache, 'BLACKLIST', 2))
            LOCK['ok'] = True
            return True
    except Exception as ex:
        log(u'تثبيت الدليل: %s' % ex)
    LOCK['ok'] = False
    return False


def export_linked_refs(path='/tmp/linked_refs.json'):
    """all confirmed links by service reference (manual + verified), ready to upload to the cloud repo."""
    out = dict(load_learned()['refs'])
    for ref, key in load_mapping().items():
        rk = ref_key(ref)
        if rk and key and key != '-':
            out[rk] = key
    with open(path, 'wb') as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=0, sort_keys=True).encode('utf-8'))
    return path, len(out)


BACKUP_MOUNTS = ('/media/hdd', '/media/usb', '/media/mmc', '/media/usb2')


def backup_dir(existing=False):
    for d in BACKUP_MOUNTS:
        if os.path.ismount(d) and os.access(d, os.W_OK):
            for name in (('SaadEPG_backup', 'ArabEPG_backup') if existing else ('SaadEPG_backup',)):
                p = os.path.join(d, name)
                if not existing or os.path.exists(os.path.join(p, 'settings.json')):
                    return p
    return None


def backup_all(settings):
    """settings: {name: value}. The copy before this one is kept in previous/. -> folder or None"""
    d = backup_dir()
    if not d:
        return None
    if not os.path.isdir(d):
        os.makedirs(d)
    if os.path.exists(os.path.join(d, 'settings.json')):
        prev = os.path.join(d, 'previous')
        if not os.path.isdir(prev):
            os.makedirs(prev)
        for fn in ('settings.json', 'mapping.json', 'learned.json', 'info.json'):
            try:
                shutil.copy2(os.path.join(d, fn), os.path.join(prev, fn))
            except Exception:
                pass
    _save(os.path.join(d, 'settings.json'), settings)
    for f in (MAP_FILE, LEARN_FILE):
        if os.path.exists(f):
            _save(os.path.join(d, os.path.basename(f)), _load(f, {}))
    _save(os.path.join(d, 'info.json'), {'t': int(time.time())})
    return d


def backup_info():
    """-> (folder, time of the last backup) or None"""
    d = backup_dir(existing=True)
    if not d:
        return None
    t = _load(os.path.join(d, 'info.json'), {}).get('t')
    if not t:
        t = int(_mtime(os.path.join(d, 'settings.json')))
    return d, t


def fresh_install():
    """True when this receiver has no Saad EPG data yet (new install or after flashing the image)."""
    return not any(os.path.exists(p) for p in (MAP_FILE, LEARN_FILE, STATE_FILE))


def restore_all():
    """-> (folder, settings dict) or (None, None)"""
    d = backup_dir(existing=True)
    if not d:
        return None, None
    for f in (MAP_FILE, LEARN_FILE):
        b = os.path.join(d, os.path.basename(f))
        if os.path.exists(b):
            _save(f, _load(b, {}))
    try:
        os.remove(AUTO_FILE)      # link again with the restored choices
    except OSError:
        pass
    return d, _load(os.path.join(d, 'settings.json'), {})


def reset_plugin_data():
    """forget what was imported, the backup copy and the automatic links (manual links stay)."""
    st = load_state()
    st['filled'] = {}
    st.pop('last_stats', None)
    save_state(st)
    for p in (AUTO_FILE, os.path.join(cache_dir(), 'events.json.gz'), TIMES_FILE):
        try:
            os.remove(p)
        except OSError:
            pass
    shutil.rmtree(os.path.join(cache_dir(), 'backup'), ignore_errors=True)
    eit_lock([], LOCK['on'])        # the satellite may fill these channels again until the next update


def _event_at(events, t):
    for e in events or ():
        if e[0] <= t + 60 < e[0] + e[1]:
            return e[2]
    return None


def _title_sim(a, b):
    """similarity of two programme titles (None when one is missing)."""
    if not a or not b:
        return None
    from difflib import SequenceMatcher
    from .matcher import latin
    x, y = latin(a), latin(b)
    if not x or not y:
        return None
    if x in y or y in x:
        return 1.0
    return SequenceMatcher(None, x, y).ratio()


# --------------------------------------------------------------------- copy between copies of one channel
# Many channels exist several times in one bouquet (SD/HD, Nilesat/Arabsat, backup feeds). Often only
# one copy carries a guide from the satellite. The guide is copied to the empty copy only when:
#   * both are in the SAME bouquet (a name can mean a different feed in another bouquet)
#   * the names are identical after removing HD/SD/FHD/4K/backup... and the numbers agree
#   * the reference database does not say they are different channels
#   * if the empty copy shows a "now" title, it matches the donor's "now" title
#   * if several copies carry a guide, they agree with each other (or the "now" title picks one)
_COPY_BAD = re.compile(u'(\\+\\s*\\d|plus\\s*\\d|\\bfeed\\b|\\btest\\b|time\\s*shift|\\bpip\\b|'
                       u'\\b(east|west|europe|usa|us|canada|australia|asia|africa)\\b|اوروبا|أوروبا|امريكا|أمريكا)',
                       re.I)


def placeholder_name(name):
    """'<n/a>', 'N/A', '', '...' : services whose name the receiver does not know."""
    n = u(name or u'').strip().lower()
    return not n or bool(re.match(u'^[<\\[(]?\\s*n\\s*/?\\s*a\\s*[>\\])]?$', n)) or \
        not re.search(u'[a-z\u0621-\u064a]', n)


def name_close(name, key):
    """does the channel name look like one of the names of the online channel? (guards automatic links)"""
    from difflib import SequenceMatcher
    from .matcher import latin, distinct
    c = catalog_entry(key)
    names = (c or {}).get('names') or [key.split(':', 1)[-1].split('/')[-1]]
    a = latin(name)
    da = distinct(a)
    for n in names:
        b = latin(n)
        if not a or not b:
            continue
        if a == b or (len(a) > 3 and a in b) or (len(b) > 3 and b in a):
            return True
        db = distinct(b)
        if da and db:     # the distinctive word must match (Polsat Sport is not Palestine Sport)
            if SequenceMatcher(None, da, db).ratio() >= 0.8:
                return True
        elif SequenceMatcher(None, a, b).ratio() >= 0.8:
            return True
    return False


# --------------------------------------------------------------------- language-independent checks
AR_FILES = ('ae', 'sa', 'eg', 'bein', 'oe_egypt', 'oe_uae', 'oe_qatar', 'oe_saudi', 'oe_arabia', 'ar_from_na', 'aljazeera')
EU_FILES = ('it', 'pl', 'gr', 'fr', 'ro', 'ua', 'rs', 'hr', 'ba', 'al', 'bg', 'hu', 'il', 'oe_poland')
_EU_CHARS = re.compile(u'[Ѐ-ӿͰ-Ͽ֐-׿ąćęłńśźżăâîșțşţőűčšžđřůěýáíéóúöüäß]', re.I)
_AR_CHARS = re.compile(u'[ء-ي]')


def native_events(epgcache, ref, hours=15, ours=None, name=None):
    """what the satellite itself says: [(begin, duration, title)] without the plugin's own events
    (ours = set of (begin, duration) imported by the plugin) and without dummy events."""
    now = int(time.time())
    try:
        res = epgcache.lookupEvent(['BDT', (_enc(ref), 0, now - 3 * 3600, hours * 60 + 180)])
    except Exception:
        return []
    out, nn = [], norm(name) if name else None
    for x in res or []:
        try:
            b, d, t = int(x[0]), int(x[1]), u(x[2] or u'')
        except (TypeError, ValueError, IndexError):
            continue
        if not t or d <= 0 or d >= 12 * 3600 or (ours and (b, d) in ours):
            continue
        if nn and len(t) < len(name) + 12 and norm(t) == nn:   # a title that is only the channel name
            continue
        out.append((b, d, t))
    return out


def text_lang(titles):
    """'ar' (Arabic guide), 'eu' (a European language that is not English) or None"""
    titles = [t for t in titles if t]
    if len(titles) < 2:
        return None
    ar = len([t for t in titles if _AR_CHARS.search(t)])
    eu = len([t for t in titles if _EU_CHARS.search(t)])
    if ar * 2 >= len(titles):
        return 'ar'
    if eu * 5 >= len(titles) and not ar:
        return 'eu'
    return None


def key_region(key):
    src, _, rest = u(key or u'').partition(':')
    if src in ARABIC_SOURCES:
        return 'ar'
    if src == 'xml':
        f = rest.split('/', 1)[0]
        reg = ((load_index().get('files') or {}).get(f) or {}).get('region')
        if reg in ('ar', 'eu'):
            return reg
        if f in AR_FILES or [1 for p in AR_FILES if f.startswith(p + '_') or (p.startswith('oe_') and f.startswith(p))]:
            return 'ar'
        if f in EU_FILES or f.startswith('oe_poland'):
            return 'eu'
    return None


def _distinct(b):
    """a programme start that is not on a round :00/:30 (round times match between any two channels)"""
    m = (b // 60) % 30
    return 3 <= m <= 27


def align(native, events, shift=0, tol=300, only_distinct=False):
    """share of the satellite's programme starts that the online guide also has (+-5 min).
    -> (ratio, number of starts compared). Works whatever the language of the titles."""
    now = time.time()
    starts = sorted(set(b for b, _d, _t in native if now - 3 * 3600 <= b <= now + 15 * 3600 and
                        (not only_distinct or _distinct(b))))
    if not starts or not events:
        return 0.0, 0
    src = sorted(e[0] + shift for e in events)
    import bisect
    hit = 0
    for b in starts:
        i = bisect.bisect_left(src, b - tol)
        if i < len(src) and src[i] <= b + tol:
            hit += 1
    return float(hit) / len(starts), len(starts)


def align_back(native, events, shift=0, tol=300, only_distinct=False):
    """the other direction: share of the online guide's starts (inside the satellite's time span)
    that the satellite also has. A short online list (main shows only) is still fully confirmed."""
    if not native or not events:
        return 0.0, 0
    lo = min(b for b, _d, _t in native)
    hi = max(b + d for b, d, _t in native)
    fake = [(e[0] + shift, e[1], e[2]) for e in events if lo <= e[0] + shift < hi and
            (not only_distinct or _distinct(e[0] + shift))]
    if not fake:
        return 0.0, 0
    r, n = align([(b, d, t) for b, d, t in fake], [(b,) for b, _d, _t in native], 0, tol)
    return r, n


def _titles_at(native, events, shift):
    """how many satellite programmes start at the same (shifted) time with the same title online"""
    starts = {}
    for e in events:
        starts.setdefault((e[0] + shift) // 300, []).append(e[2])
    n = 0
    for b, _d, t in native:
        for k in (b // 300 - 1, b // 300, b // 300 + 1):
            if [1 for x in starts.get(k, ()) if (_title_sim(t, x) or 0) >= 0.7]:
                n += 1
                break
    return n


def check_link(native, events):
    """compare one online guide with the satellite guide.
    -> ('ok'|'wrong'|'offset'|'unknown', hours, details)
    'offset': the two guides are the same schedule shifted by whole hours. Which side is wrong
    cannot be known (many satellite guides carry a wrong time zone), so nothing is changed."""
    if not native or not events:
        return 'unknown', 0, u''
    now = time.time()
    cur = [t for b, d, t in native if b <= now < b + d]
    tsim = _title_sim(cur[0], _event_at(events, now)) if cur else None
    al, nb = align(native, events)
    bk, nk = align_back(native, events)
    ald, nd = align(native, events, only_distinct=True)
    bkd, nkd = align_back(native, events, only_distinct=True)
    if (tsim is not None and tsim >= 0.5) or _titles_at(native, events, 0) >= 2 or \
            (nd >= 2 and ald >= 0.8) or (nkd >= 2 and bkd >= 0.8):
        return 'ok', 0, u'%d/%d' % (int(al * nb + 0.5), nb)
    contradiction = (nb >= 4 and al <= 0.34 and (nk < 3 or bk <= 0.34)) or \
        (nd >= 3 and ald == 0 and (nkd < 2 or bkd == 0))
    if not contradiction:
        return 'unknown', 0, u''
    for sh in (3600, -3600, 7200, -7200, 10800, -10800):
        a2, _n2 = align(native, events, sh)
        b2, n3 = align_back(native, events, sh)
        if (a2 >= 0.8 and (n3 < 3 or b2 >= 0.6)) or _titles_at(native, events, sh) >= 2:
            return 'offset', sh // 3600, u'فرق %+d ساعة' % (sh // 3600)
    if tsim is None or tsim < 0.3:
        return 'wrong', 0, u'المواعيد %d/%d' % (int(al * nb + 0.5), nb)
    return 'unknown', 0, u''


def name_like(a, b):
    """two receiver names of the same channel (HD/SD/backup copies, Arabic or Latin spelling)"""
    from difflib import SequenceMatcher
    from .matcher import latin
    ka, kb = copy_key(a), copy_key(b)
    if ka and ka == kb:
        return True
    la, lb = latin(a), latin(b)
    return bool(la and lb and SequenceMatcher(None, la, lb).ratio() >= 0.85)


def copy_key(name):
    """identity of a channel for copying (None when the name is not safe to compare)."""
    n = u(name)
    if placeholder_name(n) or _COPY_BAD.search(n):
        return None
    k = norm(n)
    return k if len(k) >= 2 else None


def read_events(epgcache, ref, days=3):
    """the receiver's own guide of a service -> [(begin, duration, title, short, long, 0)]"""
    now = int(time.time())
    try:
        res = epgcache.lookupEvent(['BDTSE', (_enc(ref), 0, now - 3 * 3600, int(days * 1440) + 180)])
    except Exception:
        return []
    out = []
    for x in res or []:
        try:
            if x and x[0] and x[1] and x[2] and int(x[0]) + int(x[1]) > now:
                out.append((int(x[0]), int(x[1]), u(x[2]), u(x[3] or u''), u(x[4] or u''), 0))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def find_copies(epgcache, bouquets, targets=None, days=3, mapping=None):
    """-> {target ref: {'from': donor ref, 'from_name', 'name', 'bouquet', 'events'}}  (main thread)"""
    counts, nows, plan = {}, {}, {}
    refdb = load_refs()
    nocopy = set(load_state().get('no_copy', []))
    mapping = mapping or {}

    def cnt(r):
        if r not in counts:
            counts[r] = epg_count(epgcache, r, 24)
        return counts[r]

    def now_title(r):
        if r not in nows:
            nw, _nx = now_next(epgcache, r)
            nows[r] = nw[0] if nw and nw[0] else None
        return nows[r]

    def ids(r):
        return set(clean_epg_id(x).lower() for x in (refdb.get(ref_key(r) or '') or []))

    for bname, items in bouquets:
        groups = {}
        for r, n in items:
            k = copy_key(n)
            if k:
                groups.setdefault(k, []).append((r, n))
        for _k, members in groups.items():
            if len(members) < 2:
                continue
            if targets is not None and not [1 for r, _n in members if r in targets]:
                continue
            empty = [m for m in members if (targets is None or m[0] in targets) and m[0] not in plan and
                     m[0] not in nocopy and
                     mapping.get(m[0]) != '-' and cnt(m[0]) < 3]
            if not empty:
                continue
            donors = [m for m in members if cnt(m[0]) >= 6]
            if not donors:
                continue
            for tref, tname in empty:
                cands = list(donors)
                tid = ids(tref)
                if tid:   # the reference database knows this service: never copy from a different channel
                    cands = [d for d in cands if not ids(d[0]) or ids(d[0]) & tid]
                tnow = now_title(tref)
                if tnow and norm(tnow) != norm(tname):
                    cands = [d for d in cands if (_title_sim(tnow, now_title(d[0])) or 0) >= 0.5]
                    why = u'البرنامج الحالي مختلف'
                else:
                    # nothing to compare with: the copies that carry a guide must agree with each other
                    titles = [now_title(d[0]) for d in cands if now_title(d[0])]
                    if any((_title_sim(a, b) or 0) < 0.5 for a in titles for b in titles):
                        cands = []
                        why = u'النسخ الأخرى تعرض برامج مختلفة'
                    else:
                        why = u''
                if not cands:
                    if why:
                        log(u'لم يُنسخ دليل %s (%s): %s' % (tname, bname, why))
                    continue
                same_sat = [d for d in cands if sat_of_ref(d[0]) == sat_of_ref(tref)]
                donor = max(same_sat or cands, key=lambda d: cnt(d[0]))
                evs = read_events(epgcache, donor[0], days)
                if len(evs) >= 3:
                    plan[tref] = {'from': donor[0], 'from_name': donor[1], 'name': tname, 'bouquet': bname,
                                  'events': evs}
    return plan


# --------------------------------------------------------------------- update job
class Stage(object):
    def __init__(self, sid, label, total=1, weight=1.0):
        self.id, self.label, self.total, self.done = sid, label, max(1, total), 0
        self.weight, self.state, self.info = weight, 'wait', u''

    def frac(self):
        return 1.0 if self.state in ('ok', 'fail', 'skip') else min(1.0, float(self.done) / self.total)


WORK_DIR = '/tmp/SaadEPG_job'


def worker_python():
    """the python of the image, to run worker.py beside enigma2 (None: work in a thread as before)"""
    import sys
    names = ('python3',) if PY3 else ('python2', 'python')
    for d in ('/usr/bin', '/bin', '/usr/local/bin'):
        for n in names:
            p = os.path.join(d, n)
            if os.access(p, os.X_OK):
                return p
    exe = getattr(sys, 'executable', '') or ''
    return exe if 'python' in os.path.basename(exe) else None


class Updater(object):
    running = None

    def __init__(self, cfg, refs=None, extra=None, quiet=False):
        self.cfg = cfg
        self.only_refs = refs
        self.extra = extra or []
        self.quiet = quiet
        self.done = False
        self.cancel = False
        self.stages = []
        self.started = time.time()
        self._pct = 0.0
        self.stats = {'channels': 0, 'events': 0, 'failed': 0, 'backup': 0, 'skipped': 0}
        self.health = {'sources': {}}
        self.native_full = {}
        self.conflicts = set()
        self.summary = []

    # ---- progress API (read by the screen)
    @property
    def progress(self):
        st = [s for s in self.stages if s.state == 'run']
        return st[-1].label if st else (u'تم' if self.done else u'...')

    def percent(self):
        tw = sum(s.weight for s in self.stages) or 1.0
        p = sum(s.weight * s.frac() for s in self.stages) / tw * 100.0
        self._pct = max(self._pct, min(p, 100.0 if self.done else 99.0))
        return int(self._pct)

    def eta(self):
        p = self.percent()
        if self.done or p < 3:
            return None
        el = time.time() - self.started
        return int(el * (100 - p) / p)

    def stage(self, sid, label, total=1, weight=1.0, wait=False):
        for s in self.stages:
            if s.id == sid:
                if s.state != 'wait':
                    s.total += max(0, total)
                s.state = 'wait' if wait else 'run'
                return s
        s = Stage(sid, label, total, weight)
        s.state = 'wait' if wait else 'run'
        self.stages.append(s)
        return s

    def stop(self):
        return self.cancel

    def _translate_native(self, imported, state, now):
        cands = dict((r, ev) for r, ev in self.native_full.items() if r not in imported and r not in state['filled'])
        if not cands:
            return
        for r in list(cands):
            ev = read_events(self.epgcache, r, self.cfg.get('days', 3))
            if len(ev) >= 3:
                cands[r] = ev
            else:
                del cands[r]
        st = self.stage('trnative', u'ترجمة دليل القمر للقنوات الأجنبية إلى الإنجليزية', len(cands), 2)
        d, langs = load_title_dicts(self.cfg['cloud_url'], list(user_satellites(self.services)), self.stop)
        if not d:
            st.state, st.info = 'skip', u'لا يوجد قاموس بعد'
            return
        trn = state.setdefault('tr_native', {})
        n_ch = n_ev = 0
        for ref, evs in cands.items():
            if self.cancel:
                break
            st.done += 1
            out, hits = translate_guide(evs, d)
            if not out:
                continue
            try:
                import_events(self.epgcache, [ref], out)
                trn[ref] = {'t': int(now), 'n': hits}
                n_ch += 1
                n_ev += hits
            except Exception as e:
                log(u'ترجمة دليل القمر %s: %s' % (ref, e))
        for r in [r for r, v in trn.items() if now - v.get('t', 0) > 7 * 86400]:
            del trn[r]
        st.state, st.info = 'ok', u'%d قناة' % n_ch
        self.stats['tr_native'] = n_ch
        log(u'ترجمة دليل القمر إلى الإنجليزية (%s): %d قناة، %d برنامج' % (u'، '.join(langs), n_ch, n_ev))

    def _flush(self, refs, why):
        """remove the plugin's guide from these services. -> 'one' | 'all' | None
        Images that cannot clear one service get the whole cache cleared (the satellite guide comes back
        by itself and this update imports everything again right after)."""
        refs = list(refs)
        if not refs:
            return None
        try:
            n, how = clear_plugin_epg(self.epgcache, refs)
        except Exception as e:
            n, how = 0, None
            log(u'مسح (%s): %s' % (why, e))
        if how == 'service':
            log(u'تم مسح %s من %d قناة' % (why, n))
            return 'one'
        # keep every other channel's guide (mostly the satellite's own): read it now, put it back after clearing
        bad_sids = set((epg_triple(r) or (None,))[0] for r in refs)   # the leaked guide sits under these numbers
        keep = {}
        for r, _n in getattr(self, 'all_services', ()):
            if r in refs or (epg_triple(r) or (None,))[0] in bad_sids:
                continue
            evs = read_events(self.epgcache, r, self.cfg.get('days', 3))
            if evs:
                keep[r] = evs
        try:
            self.epgcache.flushEPG()
            back = 0
            for r, evs in keep.items():
                try:
                    import_events(self.epgcache, [r], evs)
                    back += 1
                except Exception:
                    pass
            log(u'تم مسح %s (%d قناة): الصورة لا تمسح قناة واحدة، فمُسح الدليل كله ثم أعيد دليل %d قناة أخرى كما كان' % (
                why, len(refs), back))
            return 'all'
        except Exception as e:
            log(u'تعذر المسح: %s' % e)
        return None

    def _find_leaks(self, imported):
        """services that were NOT given a guide but now show exactly a guide that was imported for another
        channel (same programme, same start, same next programme): the receiver mixes the two.
        -> {importing ref: [names of the channels it leaked to]}"""
        now = time.time()
        sig = {}
        for ref, evs in imported.items():
            cur = [e for e in evs if e[0] <= now < e[0] + e[1]]
            if not cur:
                continue
            c = cur[0]
            nxt = [e for e in evs if e[0] >= c[0] + c[1] - 60]
            nx = min(nxt, key=lambda e: e[0]) if nxt else None
            sig.setdefault((int(c[0]), u(c[2])), []).append((ref, (int(nx[0]), u(nx[2])) if nx else None))
        if not sig:
            return {}
        names = dict(self.all_services)
        leaks, shown = {}, 0
        for ref, name in self.all_services:
            if ref in imported:
                continue
            nw, nx = now_next(self.epgcache, ref)
            if not nw or not nw[0]:
                continue
            hits = sig.get((int(nw[1]), u(nw[0])))
            if not hits:
                continue
            t_ref = epg_triple(ref)
            for src, nsig in hits:
                sname = names.get(src, u'')
                if name_like(sname, name):
                    continue        # the same channel (another copy) - not a leak
                if nsig and nx and (int(nx[1]), u(nx[0])) != nsig:
                    continue
                t_src = epg_triple(src)
                if not t_src or not t_ref or t_src[0] != t_ref[0]:
                    continue        # a leak always keeps the service id
                leaks.setdefault(src, []).append(name)
                if shown < 12:
                    shown += 1
                    log(u'تسرب: دليل %s [%X:%X:%X] ظهر على %s [%X:%X:%X]' % (
                        sname, t_src[0], t_src[1], t_src[2], name, t_ref[0], t_ref[1], t_ref[2]))
        if leaks:
            log(u'دليل %d قناة كان يظهر على قنوات أخرى - لن يُضاف لها دليل بعد الآن (30 يوما)' % len(leaks))
        return leaks

    def _translate(self, tjobs, results):
        """English guides of Arabic channels -> Arabic titles (cached, limited per day by the provider)."""
        if not tjobs:
            return
        from .translate import Translator
        cfg = self.cfg
        mode = cfg.get('ar_tr_mode', 'desc')
        tr = Translator(os.path.join(cache_dir(), 'translations.json.gz'), cfg.get('tr_provider', 'mymemory'),
                        cfg.get('tr_email', ''), cfg.get('tr_url', ''), cfg.get('tr_key', ''), cloud_tr_path(),
                        cloud_tr_path('real'), cloud_tr_path('desc'), mode)
        keys = list(set(j['key'] for j in tjobs if j['key']))
        now = time.time()
        texts = []
        for k in keys:   # nearest programmes first, so today's guide is translated first
            for e in sorted(results.get(k) or [], key=lambda e: e[0]):
                if e[0] + e[1] > now:
                    if mode == 'all':        # online translation of titles only when titles are translated
                        texts.append((e[0], e[2]))
                    if e[3] and len(e[3]) < 80:
                        texts.append((e[0], e[3]))
        texts = [t for _s, t in sorted(texts)]
        st = self.stage('translate', u'العربي للدليل الإنجليزي (العناوين العربية الحقيقية والوصف)', 1, 3)
        added = tr.translate_many(texts, cfg.get('tr_limit', 1500), self.stop) if mode != 'none' else 0
        done = 0
        for k in keys:
            results[k], n = tr.apply(results.get(k) or [])
            done += n
        tr.save()
        self.stats['translated'] = done
        st.done, st.state = 1, 'ok'
        st.info = u'%d عنوان' % done
        log(u'العربي للدليل الإنجليزي (%s): %d برنامج | قواميس: عناوين عربية حقيقية %d، وصف %d، عناوين آلية %d، '
            u'ترجمات أونلاين جديدة %d' % ({'desc': u'الوصف فقط', 'all': u'العنوان والوصف', 'none': u'بدون'}.get(mode, mode),
                                          done, len(tr.real), len(tr.cdesc), len(tr.cloud), added))

    def _repair(self):
        """once after updating from 4.0/4.1: remove guides copied between unnamed services and re-import
        everything, so titles like "INVALID EMAIL PROVIDED" are replaced."""
        st = load_state()
        if not st.get('repair51'):
            d = load_learned()
            if d.get('reject'):
                log(u'إصلاح: إلغاء %d رفض قديم (الإصدار 5.0 كان يرفض نهائيا، الآن الرفض لأسبوع ثم يعاد التحقق)' %
                    len(d['reject']))
                d['reject'] = []
                _save(LEARN_FILE, d)
            st['repair51'] = True
            save_state(st)
        if st.get('repair', 0) >= 42:
            return
        bad = [r for r, f in st['filled'].items()
               if u(f.get('key', '')).startswith('copy:') and placeholder_name(f.get('from_name'))]
        if bad:
            try:
                clear_plugin_epg(self.epgcache, bad)
            except Exception as e:
                log(u'إصلاح: %s' % e)
            for r in bad:
                del st['filled'][r]
            log(u'إصلاح: تم مسح دليل %d قناة بدون اسم نُسخ لها دليل خطأ' % len(bad))
        st['repair'] = 42
        save_state(st)
        self.cfg = dict(self.cfg, smart=False)   # re-import every guide once
        log(u'إصلاح: إعادة تحميل كل الأدلة مرة واحدة لتصحيح العناوين')

    # ---- start (main thread: enigma2 objects only)
    def start(self):
        """main thread: only what needs enigma2's service lists. Everything else runs in the background."""
        if Updater.running and not Updater.running.done:
            log(u'تحديث آخر قيد التشغيل بالفعل')
            return False
        try:
            if os.path.getsize('/tmp/SaadEPG.log') > 300000:
                os.remove('/tmp/SaadEPG.log')
        except Exception:
            pass
        try:
            from enigma import eEPGCache
            self.epgcache = eEPGCache.getInstance()
            self.all_bouquets = get_bouquets()
        except Exception as e:
            log(u'خطأ في قراءة القنوات: %s' % e)
            return False
        Updater.running = self
        t = threading.Thread(target=self._run, name='SaadEPG')
        t.daemon = True
        t.start()
        return True

    def _prepare(self):
        """(update thread) channels, what the satellite says, copies between channels..."""
        st = self.stage('read', u'قراءة القنوات ودليل القمر', 1, 1)
        self._prepare_inner()
        st.done, st.state = 1, 'ok'

    def _prepare_inner(self):
        if self.only_refs is None:
            self._repair()
        self.all_services = get_services(self.all_bouquets) + [x for x in self.extra]
        self.conflicts = triple_conflicts(self.all_services)
        for r, v in (load_state().get('leak') or {}).items():
            if time.time() - v.get('t', 0) < 30 * 86400:
                self.conflicts.add(r)
        wanted = self.cfg.get('bouquets') or []
        self.bouquets = [b for b in self.all_bouquets if not wanted or b[0] in wanted]
        self.services = get_services(self.bouquets)
        known = set(r for r, _n in self.services)
        for r, n in self.extra:            # e.g. a channel being watched that is in no bouquet
            if r not in known:
                self.services.append((r, n))
        if self.only_refs is not None:
            self.services = [s for s in self.services if s[0] in self.only_refs]
        # what the satellite itself says (without what the plugin imported): used to verify the links
        self.native = {}
        # (not for the quick update of the channel you just opened: it has no guide at all)
        if self.cfg.get('verify', True) and not self.quiet:
            filled = load_state()['filled']
            bk = None
            for ref, name in self.services:
                f = filled.get(ref)
                ours = None
                if f:
                    k = u(f.get('key', ''))
                    if k.startswith('copy:'):
                        continue
                    sh = int(f.get('shift', 0))
                    times = our_times(k)
                    if times is None:      # first run after updating the plugin: the old way, once
                        bk = bk or Backup()
                        times = set((int(e[0]), int(e[1])) for e in bk.get(k))
                    ours = set((b + sh, d) for b, d in times)
                ev = native_events(self.epgcache, ref, 15, ours, name)
                if ev:
                    self.native[ref] = ev
            bk = None
        self.present = {}
        if self.cfg.get('smart'):
            for r in load_state()['filled']:
                self.present[r] = epg_count(self.epgcache, r, 24) > 0
        self.copies = {}
        if self.cfg.get('copy_epg', True):
            try:
                self.copies = find_copies(self.epgcache, self.bouquets,
                                          set(r for r, _n in self.services) if self.only_refs is not None
                                          else None, self.cfg.get('days', 3), load_mapping())
            except Exception as e:
                log(u'نسخ الدليل بين نسخ القناة: %s' % e)
        # channels that take the guide of another channel (chosen by hand)
        mp = load_mapping()
        for ref, name in self.services:
            v = mp.get(ref) or ''
            if v.startswith('chan:'):
                donor, _, dname = v[5:].partition('|')
                evs = read_events(self.epgcache, donor, self.cfg.get('days', 3))
                if evs:
                    self.copies[ref] = {'from': donor, 'from_name': dname or donor, 'name': name,
                                        'bouquet': u'اختيار يدوي', 'events': evs}
                else:
                    log(u'%s: القناة المختارة (%s) ليس لها دليل الآن' % (name, dname))
        # the satellite's own guide of foreign channels, to translate it to English (only channels the plugin
        # does not fill from an online guide; read here because enigma2 objects belong to the main thread)
        # which channels' own satellite guide may be translated to English (read later, in the thread)
        self.native_full = {}
        if self.cfg.get('foreign_lang', 'en') == 'en' and not self.quiet:
            filled = load_state()['filled']
            mp = load_mapping()
            for ref, _n in self.services:
                if ref in filled or mp.get(ref) in ('-', '!') or sat_of_ref(ref) in (
                        'nilesat', 'arabsat', 'iptv', 'terrestrial'):
                    continue
                self.native_full[ref] = None
        self.rich = {}
        if self.cfg['target'] == 'missing':
            filled = load_state()['filled']
            for ref, _n in self.services:
                if ref not in filled:
                    self.rich[ref] = epg_count(self.epgcache, ref) >= 4

    def _run(self):
        try:
            self._prepare()
            ctx = None
            if self.cfg.get('worker', True) and not self.cancel:
                ctx = self._compute_in_process()
            if ctx is None and not self.cancel:
                ctx = self._compute()
            if ctx is not None:
                self._apply(ctx)
        except Exception as e:
            import traceback
            traceback.print_exc()
            log(u'خطأ عام: %s' % e)
            self.summary = [u'خطأ: %s' % e]
        finally:
            for s in self.stages:
                if s.state == 'run':
                    s.state = 'ok' if not self.cancel else 'skip'
            self.done = True

    # ---- steps 1-5 in a separate process: the receiver's interface never waits for this work
    def _compute_in_process(self):
        py = worker_python()
        if not py:
            return None
        d = WORK_DIR
        try:
            if not os.path.isdir(d):
                os.makedirs(d)
            for fn in ('progress.json', 'result.pkl', 'cancel', 'err.txt'):
                try:
                    os.remove(os.path.join(d, fn))
                except OSError:
                    pass
            job = {'cfg': self.cfg, 'services': self.services, 'native': self.native, 'rich': self.rich,
                   'present': self.present, 'quiet': self.quiet}
            with open(os.path.join(d, 'job.pkl'), 'wb') as f:
                pickle.dump(job, f, 2)
            import subprocess
            with open(os.devnull, 'rb') as dn, open(os.path.join(d, 'err.txt'), 'wb') as err:
                proc = subprocess.Popen([py, os.path.join(PLUGIN_DIR, 'worker.py'), d], stdin=dn, stdout=err,
                                        stderr=err, close_fds=True)
        except Exception as e:
            log(u'تعذر تشغيل العملية المنفصلة (%s) - يكمل البلوجين بالطريقة العادية' % e)
            return None
        seen = [0]
        asked = False
        while proc.poll() is None:
            time.sleep(0.5)
            if self.cancel and not asked:
                asked = True
                open(os.path.join(d, 'cancel'), 'w').close()
            self._progress_from(d, seen)
        self._progress_from(d, seen)
        out = os.path.join(d, 'result.pkl')
        if proc.returncode != 0 or not os.path.exists(out):
            try:
                with open(os.path.join(d, 'err.txt'), 'rb') as f:
                    tail = u(f.read()[-600:].decode('utf-8', 'replace'))
            except Exception:
                tail = u''
            log(u'العملية المنفصلة توقفت (%s) - يكمل البلوجين بالطريقة العادية: %s' % (proc.returncode, tail))
            return None
        try:
            with open(out, 'rb') as f:
                ctx = pickle.load(f)
                results = {}
                n = 0
                while True:
                    item = pickle.load(f)        # one channel at a time: never a long pause
                    if item is None:
                        break
                    results[item[0]] = item[1]
                    n += 1
                    if n % 25 == 0:
                        time.sleep(0.001)
            ctx['results'] = results
            self.stats.update(ctx.pop('stats', {}))
            self.health.update(ctx.pop('health', {}))
            self.offline = ctx.pop('offline', False)
            return ctx
        except Exception as e:
            log(u'نتيجة العملية المنفصلة غير مقروءة (%s) - يكمل البلوجين بالطريقة العادية' % e)
            return None

    def _progress_from(self, d, seen):
        try:
            with open(os.path.join(d, 'progress.json'), 'rb') as f:
                pr = json.loads(f.read().decode('utf-8'))
        except Exception:
            return
        have = dict((s.id, s) for s in self.stages)
        for sid, label, total, done, state, info, weight in pr.get('stages') or []:
            s = have.get(sid)
            if s is None:
                s = Stage(sid, label, total, weight)
                self.stages.append(s)
            s.label, s.total, s.done, s.state, s.info = label, total, done, state, info
        lines = pr.get('log') or []
        new = pr.get('nlog', 0) - seen[0]
        if new > 0:
            sources.LOG.extend(lines[-new:])
            del sources.LOG[:-400]
            seen[0] = pr.get('nlog', 0)

    def _compute(self):
        """steps 1-5: cloud, linking, fetching, translation, checks, backup. No enigma2 objects in here:
        normally it runs in its own process (worker.py), so the receiver never waits for it."""
        backup = None
        fetcher = None
        try:
            cfg = self.cfg
            # 1. preparation
            s = self.stage('prep', u'تجهيز: السحابة وقائمة القنوات', 1, 2)
            sats = user_satellites(self.services)
            log(u'الأقمار في باقاتك: ' + u'، '.join(u'%s (%d)' % (SAT_TITLES.get(k, k), n)
                                                       for k, n in sorted(sats.items(), key=lambda x: -x[1])))
            # an update you start yourself always looks for a newer cloud build
            refresh_cloud(cfg['cloud_url'], max_age=600 if cfg.get('manual') else 2 * 3600,
                          use_cloud=cfg.get('cloud'), stop=self.stop)
            s.done, s.state = 1, 'ok'

            # 2. linking
            s = self.stage('link', u'الربط الذكي للقنوات', len(self.services), 3)
            enabled = list(cfg['sources']) + ['xml']
            auto = smart_link(self.services, enabled, min_score=cfg.get('min_score', 0.86))
            mapping = load_mapping()
            state = load_state()
            now = time.time()
            jobs = []
            arabic_mode = cfg.get('lang_mode', 'ar_only') != 'any'
            shifts = load_shifts()
            gold = gold_members() if cfg.get('gold', True) else {}
            stale = []
            fpl = FPLinker(cfg, enabled)
            for ref, name in self.services:
                cands, manual = resolve(ref, mapping, auto)
                s.done += 1
                if not cands and not manual:      # no name links: the same programmes at the same minutes?
                    hit = fpl.find(ref, name, self.native.get(ref))
                    if hit:
                        cands = [(hit, 0.93)]
                fk = u(state['filled'].get(ref, {}).get('key', ''))
                if fk and not fk.startswith('copy:') and not fk.startswith('xml:est/') and \
                        fk not in [c[0] for c in cands]:
                    stale.append(ref)
                if cands and (cands[0][0] == '-' or cands[0][0].startswith('chan:')):
                    continue
                if not cands:
                    continue
                if not manual and self.rich.get(ref):
                    continue
                if cfg.get('smart') and self.present.get(ref) and not self.native.get(ref) and \
                        state['filled'].get(ref, {}).get('until', 0) > now + max(0.5, cfg['days'] - 1.5) * 86400:
                    self.stats['skipped'] += 1
                    continue
                # never give a Polish/Greek/... channel an Arabic guide, or an Arabic channel a European one
                nl = text_lang([t for _b, _d, t in self.native.get(ref, ())]) or \
                    ('eu' if ref in (state.get('tr_native') or {}) else None)   # translated by us: was European
                if nl and not manual:
                    kept = [c for c in cands if not ({nl, key_region(c[0])} == {'ar', 'eu'})]
                    if len(kept) < len(cands):
                        self.stats['region'] = self.stats.get('region', 0) + len(cands) - len(kept)
                    cands = kept
                    if not cands:
                        continue
                keys = [c[0] for c in cands]
                scores = dict(cands)
                arabic = arabic_mode and _is_arabic_channel(ref, name, keys)
                if arabic:   # official Arabic sites first, mixed-language files last
                    keys = [k for k in keys if not k.startswith('xml:')] + [k for k in keys if k.startswith('xml:')]
                if gold and not manual:   # the golden guide of this channel (all its sources merged) comes first
                    gk = [(gold[k], scores.get(k, 0.0)) for k in keys if k in gold]
                    if gk:
                        g = u'xml:gold/' + gk[0][0]
                        keys = [g] + [k for k in keys if k != g]
                        scores[g] = max(sc for x, sc in gk if x == gk[0][0])
                jobs.append({'ref': ref, 'name': name, 'cands': keys, 'key': None, 'arabic': arabic, 'alt': None,
                             'scores': scores, 'manual': manual})
                if shifts.get(ref):           # your own time correction for this channel
                    jobs[-1]['shift'], jobs[-1]['manual_shift'] = int(shifts[ref]) * 3600, True
            self.stale = stale
            fpl.save()
            if stale:
                log(u'قنوات تغيّر ربطها (سيُمسح دليلها القديم): %d' % len(stale))
            s.state = 'ok'
            s.info = u'%d قناة' % len(jobs)
            log(u'قنوات الباقات: %d | سيتم تحديث: %d | تم تخطي (دليلها حديث): %d' % (
                len(self.services), len(jobs), self.stats['skipped']))
            if self.stats.get('region'):
                log(u'منع الربط بمصدر بلغة/منطقة مختلفة عن دليل القمر: %d اقتراح' % self.stats['region'])

            # 3. fetch: up to 3 rounds (first choice, then alternatives)
            results = {}
            fetcher = Fetcher(cfg, self.stop)
            rounds = max([len(j['cands']) for j in jobs] or [1])
            self.offline = False
            for rnd in range(min(rounds, 4)):
                if self.cancel or self.offline:
                    break
                need = {}
                for j in jobs:
                    if j['key'] is None and len(j['cands']) > rnd:
                        k = j['cands'][rnd]
                        if k not in results:
                            src, _, cid = k.partition(':')
                            need.setdefault(src, set()).add(cid)
                # bulk sources return many channels per request: take the later alternatives along now
                for j in jobs:
                    if j['key'] is None:
                        for k in j['cands'][rnd + 1:]:
                            src, _, cid = k.partition(':')
                            if src in BULK_SOURCES and src in need and k not in results:
                                need[src].add(cid)
                if rnd and need:
                    log(u'مصادر بديلة: محاولة %d لـ %d قناة' % (rnd + 1, sum(len(v) for v in need.values())))
                order = sorted(need, key=lambda x: SOURCE_ORDER.index(x) if x in SOURCE_ORDER else 99)
                for src in order:  # show every source of this round in the list right away
                    self.stage('src_%s_%d' % (src, rnd), SOURCE_NAMES.get(src, src) + (u' (بديل)' if rnd else u''),
                               len(need[src]), 1 + len(need[src]) / 10.0, wait=True)
                lock = threading.Lock()

                def run_source(src, rnd=rnd):
                    ids = sorted(need[src])
                    label = SOURCE_NAMES.get(src, src) + (u' (بديل)' if rnd else u'')
                    st = self.stage('src_%s_%d' % (src, rnd), label, len(ids), 1 + len(ids) / 10.0)

                    def step(n=1, st=st):
                        st.done += n
                    fetcher.step = step
                    try:
                        got = fetcher.fetch(src, ids)
                    except Exception as e:
                        log(u'%s: %s' % (label, e))
                        got = {}
                    ok = 0
                    t_now = time.time()
                    with lock:
                        for cid in ids:
                            # an old schedule (everything already over) counts as no data: try the alternatives
                            evs = [e for e in (got.get(cid) or []) if e[0] + e[1] > t_now]
                            if len(evs) < 2:
                                evs = []
                            got[cid] = evs
                            results[src + ':' + cid] = evs
                            ok += 1 if evs else 0
                    st.done = st.total
                    st.state = 'ok' if ok else 'fail'
                    st.info = u'%d/%d' % (ok, len(ids))
                    log(u'%s: %d/%d قناة بها بيانات' % (label, ok, len(ids)))
                    empty = [display_key(src + ':' + c).split(': ', 1)[-1] for c in ids if not got.get(c)]
                    self.health['sources'][label] = [ok, len(ids), empty[:15]]
                    if not rnd:     # per channel, so partial updates (stages, one channel) add up
                        items = self.health.setdefault('items', {}).setdefault(SOURCE_NAMES.get(src, src), {})
                        for c in ids:
                            items[c] = [1 if got.get(c) else 0, int(t_now),
                                        display_key(src + ':' + c).split(': ', 1)[-1]]
                    if empty and src != 'xml':
                        log(u'%s بدون بيانات: %s' % (label, u'، '.join(empty[:12])))

                # independent sources run side by side (at most 4 at a time)
                pending = list(order)
                while pending and not self.cancel:
                    if sources.NET['dns_fail'] >= 3:
                        log(u'انقطاع في الإنترنت/DNS - انتظار عودة الاتصال...')
                        if not sources.wait_network(self.stop):
                            log(u'لا يوجد اتصال بالإنترنت - تم إيقاف الجلب، وستُستخدم النسخة الاحتياطية')
                            self.offline = True
                            break
                        sources.NET['dns_fail'] = 0
                    batch, pending = pending[:4], pending[4:]
                    threads = [threading.Thread(target=run_source, args=(x,)) for x in batch]
                    for t in threads:
                        t.daemon = True
                        t.start()
                    for t in threads:
                        t.join()
                for j in jobs:
                    if j['key'] is None:
                        for k in j['cands'][:rnd + 1]:
                            evs = results.get(k)
                            if not evs:
                                continue
                            # official channel sites are trusted as they are; other files must be Arabic
                            if j['arabic'] and k.split(':')[0] not in ARABIC_SOURCES and \
                                    not k.startswith('xml:gold/') and sources.arabic_ratio(evs) < 0.6:
                                j['alt'] = j['alt'] or k     # data exists, but not in Arabic
                                continue
                            j['key'] = k
                            break
            if arabic_mode:
                not_ar = [j for j in jobs if j['key'] is None and j['alt']]
                if cfg.get('lang_mode') in ('ar_first', 'translate'):
                    for j in not_ar:
                        j['key'] = j['alt']
                        j['translate'] = cfg.get('lang_mode') == 'translate'
                if not_ar:
                    log(u'قنوات لم يُعثر لها على دليل عربي: %d%s' % (
                        len(not_ar), {'ar_first': u' (استُخدم الدليل الإنجليزي)',
                                      'translate': u' (سيُترجم الدليل الإنجليزي إلى العربية)'}.get(
                            cfg.get('lang_mode'), u' (لم تُحدَّث - الوضع: عربي فقط)')))
                    self.stats['non_arabic'] = len(not_ar)
                    for j in not_ar:
                        j['non_arabic'] = True
                if cfg.get('lang_mode') == 'translate' and not self.cancel:
                    self._translate([j for j in jobs if j.get('translate')], results)

            # 3b. ready-made files often give many small channels a COPY of another channel's schedule:
            # the same programmes at the same times under different channel names -> not used
            fps = {}
            t_now = time.time()
            for k, evs in results.items():
                if k.startswith('xml:') and evs:
                    fp = tuple(sorted((int(e[0]) // 60, u(e[2]).lower()) for e in evs
                                      if t_now <= e[0] < t_now + 86400))
                    if len(fp) >= 4:
                        fps.setdefault(fp, []).append(k)
            fake = set()
            for keys in fps.values():
                if len(keys) < 2:
                    continue
                names = [(k, (catalog_entry(k) or {}).get('names', [k])[0]) for k in keys]
                if any(not name_like(a[1], b[1]) for a in names for b in names):
                    fake.update(keys)
            if fake:
                n_fake = 0
                for j in jobs:
                    if j['key'] in fake:
                        alt = [k for k in j['cands'] if k not in fake and results.get(k)]
                        j['key'] = alt[0] if alt else None
                        j['fake'] = not alt
                        n_fake += 1
                log(u'جدول منسوخ في ملف المصدر (نفس البرامج لقنوات مختلفة) - لم يُستخدم: %d قناة' % n_fake)

            # 4. verify every link against the satellite's own guide (titles AND programme start times)
            verified_pairs, fixed_pairs, rejected = [], [], []
            checked = fixed = shifted = 0
            for j in jobs:
                nat = self.native.get(j['ref'])
                if not nat or not j['key'] or j.get('manual_shift'):
                    continue
                verdict, sh, info = check_link(nat, results.get(j['key']))
                if verdict == 'unknown':
                    continue
                checked += 1
                if verdict == 'ok':
                    j['verified'] = True
                    verified_pairs.append((j['ref'], j['key']))
                    continue
                if verdict == 'offset':
                    # same schedule shifted by whole hours. Which side is wrong? Ask a second online source:
                    # it agrees with the satellite -> the first source is off, its times are corrected;
                    # it agrees with the first source -> the satellite is off, the source stays as it is.
                    shifted += 1
                    j['verified'] = True
                    second = None
                    for k in j['cands']:
                        if k != j['key'] and results.get(k):
                            v2, sh2, _i2 = check_link(nat, results.get(k))
                            if v2 == 'ok' or (v2 == 'offset' and sh2 == sh):
                                second = (k, v2)
                                break
                    if second and second[1] == 'ok':
                        j['shift'] = j.get('shift', 0) + sh * 3600
                        log(u'تصحيح توقيت %s (%s): %s يطابق دليل القمر، فتم تعديل مواعيد %s %+d ساعة' % (
                            j['name'], info, display_key(second[0]), display_key(j['key']), sh))
                    elif second:
                        log(u'فرق توقيت (%s) في دليل القمر نفسه لقناة %s: مصدران متفقان (%s و %s) - تم الاعتماد عليهما' % (
                            info, j['name'], display_key(j['key']), display_key(second[0])))
                    else:
                        log(u'نفس الجدول مع فرق توقيت (%s) بين دليل القمر و %s: %s - تم الاعتماد على المصدر '
                            u'(يمكنك تصحيح التوقيت يدويا من شاشة القنوات)' % (info, display_key(j['key']), j['name']))
                    continue
                if j.get('manual'):     # your own choice is kept, but you are told
                    log(u'تنبيه: الربط اليدوي لا يطابق دليل القمر (%s): %s ← %s' % (info, j['name'], display_key(j['key'])))
                    continue
                # wrong: look for an alternative that fits, otherwise import nothing for this channel
                alt = None
                for k in j['cands']:
                    if k != j['key'] and results.get(k) and name_close(j['name'], k):
                        v2, _sh2, _i = check_link(nat, results.get(k))
                        if v2 == 'ok':
                            alt = k
                            break
                if alt:
                    log(u'تصحيح الربط بمقارنة دليل القمر: %s: %s ← %s' % (j['name'], display_key(j['key']), display_key(alt)))
                    j['key'], j['verified'] = alt, True
                    fixed_pairs.append((j['ref'], alt))
                    fixed += 1
                else:
                    log(u'رُفض الربط (لا يطابق دليل القمر - %s): %s ← %s' % (info, j['name'], display_key(j['key'])))
                    rejected.append((j['ref'], j['name'], j['key']))
                    j['key'], j['rejected'] = None, True
            if checked:
                log(u'التحقق بدليل القمر (العناوين ومواعيد البرامج): %d قناة، متطابقة %d، تصحيح %d، رفض %d%s' % (
                    checked, len([1 for j in jobs if j.get('verified')]), fixed, len(rejected),
                    (u'، فرق توقيت فقط %d' % shifted) if shifted else u''))
            for ref, name, key in rejected:
                reject_auto(name, key)
                unlearn_ref(ref, key)
            self.stats['rejected'] = len(rejected)

            # 4a. time corrections learned from the broadcast while you watched (the channel's own "now")
            ls = live_shifts()
            if ls:
                n_ls, gone = 0, []
                for j in jobs:
                    x = ls.get(j['ref'])
                    if not x or not x.get('on') or not j['key'] or j.get('manual_shift') or j.get('shift'):
                        continue
                    v = live_shift_check(x, results.get(j['key']))
                    if v == 'keep':
                        j['shift'] = int(x['sh']) * 3600
                        j['live_shift'] = int(x['sh'])
                        n_ls += 1
                    else:
                        gone.append(j['ref'])
                        log(u'تصحيح التوقيت من البث لم يعد لازماً (%s): %s' % (
                            u'المصدر صحّح مواعيده' if v == 'fixed' else u'قديم', j['name']))
                if gone:
                    for r in gone:
                        ls.pop(r, None)
                    _save(LIVE_SHIFT_FILE, ls)
                if n_ls:
                    log(u'تصحيح توقيت تعلّمه البلوجين من البث نفسه: %d قناة' % n_ls)
                self.stats['live_shift'] = n_ls

            # 4b. confidence: a link that could not be checked must be strong enough on its own
            mode = cfg.get('link_mode', 'normal')
            held = []
            if mode != 'all':
                need = 1.0 if mode == 'safe' else 0.92
                for j in jobs:
                    if j['key'] and not j.get('verified') and not j.get('manual') and \
                            j['scores'].get(j['key'], 0.0) < need:
                        held.append(j)
                for j in held:
                    j['key'], j['held'] = None, True
                if held:
                    log(u'ربط غير مؤكد لم يُستورد (راجعه في: القنوات ← تحتاج مراجعة): %d قناة' % len(held))
            state['review'] = dict((j['ref'], j['cands'][0]) for j in held if j['cands'])

            # 5. backup for the rest
            backup = Backup()
            for k, evs in results.items():
                if evs:
                    backup.put(k, evs)
            missing = [j for j in jobs if j['key'] is None and not j.get('rejected') and not j.get('held')
                       and not j.get('fake')]
            if missing:
                s = self.stage('backup', u'النسخة الاحتياطية للقنوات المتعثرة', len(missing), 1)
                for j in missing:
                    for k in j['cands']:
                        evs = backup.get(k)
                        if evs and j['arabic'] and k.split(':')[0] not in ARABIC_SOURCES and \
                                not k.startswith('xml:gold/') and sources.arabic_ratio(evs) < 0.6:
                            continue
                        if evs:
                            j['key'], j['backup'] = k, True
                            results[k] = evs
                            self.stats['backup'] += 1
                            break
                    s.done += 1
                s.state = 'ok'
                if self.stats['backup']:
                    log(u'تم استخدام النسخة الاحتياطية لـ %d قناة' % self.stats['backup'])

            # 5b. estimated guide: the source stopped (or ends early) and the channel repeats its weekly/daily grid
            ef = (load_index().get('files') or {}).get('est') if cfg.get('estimate', True) else None
            if ef and ef.get('channels') and not self.cancel:
                have = set(c for c, _n in ef['channels'])
                want, limit = {}, now + (cfg['days'] - 0.5) * 86400
                for j in jobs:
                    if j.get('rejected') or j.get('held') or j.get('fake'):
                        continue
                    if j['key']:
                        evs = results.get(j['key']) or []
                        end = max(e[0] + e[1] for e in evs) if evs else 0
                        if end < limit and j['key'] in have:
                            want[j['ref']] = (j['key'], end)
                    else:
                        for k in j['cands']:
                            if k in have:
                                want[j['ref']] = (k, 0)
                                break
                if want:
                    got = fetcher.fetch('xml', sorted(set('est/' + k for k, _e in want.values())))
                    n_est, merged = 0, set()
                    for j in jobs:
                        if j['ref'] not in want:
                            continue
                        k, end = want[j['ref']]
                        evs = [e for e in got.get('est/' + k) or [] if e[0] >= end - 60]
                        if not evs:
                            continue
                        if j['key']:
                            if j['key'] not in merged:            # once per source: every channel of it gets it
                                results[j['key']] = list(results.get(j['key']) or []) + evs
                                merged.add(j['key'])
                        else:
                            j['key'] = u'xml:est/' + k
                            results[j['key']] = evs
                        j['est'] = len(evs)
                        n_est += 1
                    if n_est:
                        log(u'جدول تقديري (من تكرار البث الأسبوعي/اليومي) لـ %d قناة توقف مصدرها أو ينتهي مبكرا' % n_est)
                    self.stats['estimated'] = n_est
            return {'jobs': jobs, 'results': dict((j['key'], results.get(j['key'])) for j in jobs if j['key']),
                    'rejected': rejected, 'held': [j['ref'] for j in held], 'verified_pairs': verified_pairs,
                    'fixed_pairs': fixed_pairs, 'review': state.get('review') or {}, 'stale': self.stale}
        finally:
            try:
                if backup:
                    backup.save()
            except Exception:
                pass
            if fetcher:
                fetcher.cleanup()

    def _apply(self, ctx):
        """step 6 and after (update thread): import into the receiver, leak check, lock, state."""
        cfg = self.cfg
        jobs, results = ctx['jobs'], ctx['results']
        rejected, verified_pairs, fixed_pairs = ctx['rejected'], ctx['verified_pairs'], ctx['fixed_pairs']
        held_refs = set(ctx['held'])
        held = [j for j in jobs if j['ref'] in held_refs]
        self.stale = ctx.get('stale') or []
        state = load_state()
        state['review'] = ctx.get('review') or {}
        filled_before = set(state['filled'])
        # 6. import (clean-up first, then a leak check afterwards)
        now = int(time.time())
        mine = [j for j in jobs if j['key'] and j['ref'] in self.conflicts and j.get('manual')]
        for j in mine:      # your own link: imported anyway, you were told what else shows it
            log(u'ربط يدوي لقناة رقم خدمتها مشترك (%s) - سيظهر دليلها أيضا على: %s' % (
                j['name'], u'، '.join(CONFLICT_WITH.get(j['ref']) or [u'قناة أخرى'])))
        blocked = [j for j in jobs if j['key'] and j['ref'] in self.conflicts and not j.get('manual')]
        for j in blocked:
            j['key'], j['conflict'] = None, True
        if blocked:
            log(u'لم يُستورد دليل %d قناة لأن رقم خدمتها مشترك مع قناة أخرى مختلفة (كان الدليل سيظهر على '
                u'القناتين معًا - يمكنك ربطها يدويا إن أردت): %s' % (len(blocked), u'، '.join(
                    u'%s (مع %s)' % (j['name'], u'، '.join((CONFLICT_WITH.get(j['ref']) or [u'?'])[:2]))
                    for j in blocked[:12])))
        manual_refs = set(j['ref'] for j in mine)
        to_clean = set(r for r in state['filled'] if r in self.conflicts and r not in manual_refs)
        to_clean |= set(r for r, _n, _k in rejected if r in state['filled'])
        to_clean |= set(getattr(self, 'stale', ()))
        to_clean |= set(j['ref'] for j in jobs if j.get('fake') and j['ref'] in state['filled'])
        for r in to_clean:
            state['filled'].pop(r, None)
        groups = {}
        for j in jobs:
            if j['key']:
                groups.setdefault((j['key'], j.get('shift', 0)), []).append(j['ref'])
            elif not j.get('rejected') and not j.get('held') and not j.get('conflict'):
                self.stats['failed'] += 1
        why = state.setdefault('why', {})      # why a channel got nothing (for "channels without a guide")
        for j in jobs:
            why.pop(j['ref'], None)
            if not j['key']:
                code = ('conflict' if j.get('conflict') else 'rejected' if j.get('rejected') else
                        'held' if j.get('held') else 'fake' if j.get('fake') else 'empty')
                why[j['ref']] = [code, (j.get('cands') or [u''])[0], now]
        got_guide = set(r for refs in groups.values() for r in refs)
        copies = [(t, c) for t, c in self.copies.items() if t not in got_guide and t not in self.conflicts]
        if to_clean and self._flush(to_clean, u'دليل خطأ أو قديم'):
            to_clean = set()

        jref = dict((j['ref'], j) for j in jobs)

        def do_import(exclude, quiet=False):
            done = {}
            st = self.stage('import', u'إضافة البرامج إلى EPG الريسيفر', len(groups), 3)
            for (key, shift), refs in groups.items():
                if self.cancel:
                    break
                refs = [r for r in refs if r not in exclude]
                evs = results.get(key) or []
                if shift:
                    evs = [(e[0] + shift,) + tuple(e[1:]) for e in evs]
                if refs and evs:
                    try:
                        n = import_events(self.epgcache, refs, evs)
                        until = max(e[0] + e[1] for e in evs)
                        for r in refs:
                            done[r] = evs
                            state['filled'][r] = {'key': key, 't': now, 'n': n, 'until': until}
                            if jref.get(r, {}).get('verified'):
                                state['filled'][r]['v'] = 1     # the satellite's own guide agrees
                            if jref.get(r, {}).get('est'):
                                state['filled'][r]['est'] = jref[r]['est']   # partly / only estimated
                            if shift:
                                state['filled'][r]['shift'] = shift
                    except Exception as e:
                        log(u'فشل الاستيراد %s: %s' % (key, e))
                st.done += 1
            st.state = 'ok'
            if copies and not self.cancel:
                st = self.stage('copy', u'نسخ الدليل من نسخة أخرى لنفس القناة', len(copies), 1)
                for t, c in copies:
                    if t in exclude:
                        continue
                    try:
                        n = import_events(self.epgcache, [t], c['events'])
                        done[t] = c['events']
                        state['filled'][t] = {'key': u'copy:' + c['from'], 'from_name': c['from_name'],
                                              'name': c['name'], 't': now,
                                              'n': n, 'until': max(e[0] + e[1] for e in c['events'])}
                        if not quiet:
                            log(u'نسخ الدليل: %s ← %s (%s، %d برنامج)' % (c['name'], c['from_name'],
                                                                     c['bouquet'], n))
                    except Exception as e:
                        log(u'فشل نسخ دليل %s: %s' % (c['name'], e))
                    st.done += 1
                st.state = 'ok'
            return done

        imported = do_import(set())
        # 6b. leak check: a guide must not show up on another channel (receivers that keep the guide
        # per service number show it on every channel with the same number)
        if not self.cancel:
            leaks = self._find_leaks(dict((r, v) for r, v in imported.items() if r not in manual_refs))
            if leaks:
                for src, victims in leaks.items():
                    state['filled'].pop(src, None)
                    state.setdefault('leak', {})[src] = {'t': now, 'victims': victims[:5]}
                if self._flush(set(leaks), u'دليل تسرب لقنوات أخرى') == 'all':
                    imported = do_import(set(leaks), quiet=True)
                for src in leaks:
                    imported.pop(src, None)
        self.stats['channels'] = len(imported)
        self.stats['events'] = sum(len(v) for v in imported.values())
        n_copied = len([1 for t, _c in copies if t in imported])
        if n_copied:
            log(u'نسخ الدليل من نسخة أخرى لنفس القناة: %d قناة' % n_copied)
        self.stats['copied'] = n_copied
        copied_refs = set(t for t, _c in copies if t in imported)
        for j in jobs:
            if not j['key'] and j['ref'] in copied_refs:
                self.stats['failed'] -= 1
                j['copied'] = True
        if cfg.get('foreign_lang', 'en') != 'en' and state.get('tr_native') and not self.cancel:
            # translation switched off: the satellite guides translated earlier go, the satellite sends its own again
            refs = list(state['tr_native'])
            try:
                n, how = clear_plugin_epg(self.epgcache, refs)
                if how:
                    log(u'إيقاف الترجمة: مُسح دليل القمر المترجم من %d قناة (يعود دليل القمر الأصلي)' % n)
            except Exception as e:
                log(u'إيقاف الترجمة: %s' % e)
            state['tr_native'] = {}
        if self.native_full and not self.cancel:
            try:
                self._translate_native(imported, state, now)
            except Exception as e:
                log(u'ترجمة دليل القمر: %s' % e)
        hidden = [r for r, v in load_mapping().items() if v == '!' and r in dict(self.all_services)]
        if hidden:
            n, how = clear_plugin_epg(self.epgcache, hidden)
            if how:
                log(u'تم إخفاء دليل القمر الخطأ من %d قناة' % n)
            else:
                log(u'إخفاء دليل القمر: هذه الصورة لا تسمح بمسح دليل قناة واحدة')
        self.stats['conflicts'] = len(blocked)
        self.health['held'] = len(held)
        self.health['rejected'] = len(rejected)
        self.health['conflicts'] = [j['name'] for j in blocked][:60]
        self.health['leaks'] = dict((k, v) for k, v in (state.get('leak') or {}).items())
        self.health['copied'] = n_copied
        self.health['translated'] = self.stats.get('translated', 0)
        # keep the results of the last 2 days per channel: the health screen shows the whole picture,
        # not only the last (maybe partial) update
        old = (state.get('health') or {}).get('items') or {}
        for lbl, chans in (self.health.get('items') or {}).items():
            old.setdefault(lbl, {}).update(chans)
        limit = time.time() - 2 * 86400
        items = dict((lbl, dict((c, v) for c, v in chans.items() if v[1] >= limit)) for lbl, chans in old.items())
        self.health['items'] = dict((k, v) for k, v in items.items() if v)
        for lbl, chans in self.health['items'].items():
            okn = sum(1 for v in chans.values() if v[0])
            self.health['sources'][lbl] = [okn, len(chans), [v[2] for v in chans.values() if not v[0]][:15]]
        state['health'] = self.health
        try:
            self.epgcache.save()
        except Exception:
            pass
        state['last'] = now
        state['last_stats'] = self.stats
        try:
            n_lock = eit_lock(lock_wanted(state), cfg.get('lock', True))
            self.health['locked'] = n_lock
            if cfg.get('lock', True):
                log(u'تثبيت دليل البلوجين (لا يكتب القمر فوقه): %d قناة' % n_lock)
        except Exception as e:
            log(u'تثبيت الدليل: %s' % e)
        state['non_arabic'] = [j['name'] for j in jobs if j.get('non_arabic')][:300]
        n_learn = learn_refs(verified_pairs + [(r, k) for r, k in fixed_pairs])
        if n_learn:
            log(u'تم حفظ %d ربط مؤكد برقم الخدمة' % n_learn)
        if not getattr(self, 'only_refs', None):
            # channels that had no guide at the last update and have one now (a new source in the cloud...)
            known = set(state.get('known_refs') or [])
            if known:
                names = dict(self.services)
                gained = [r for r in state['filled'] if r in known and r not in filled_before and
                          r not in set(self.stale or ())]
                if gained:
                    state['gained'] = {'t': now, 'names': [names.get(r, r) for r in gained][:60], 'n': len(gained)}
                    log(u'قنوات صار لها دليل لأول مرة: %d - %s' % (
                        len(gained), u'، '.join(state['gained']['names'][:20])))
            state['known_refs'] = [r for r, _n in self.services]
        save_state(state)
        failed_names = [j['name'] for j in jobs if not j['key'] and not j.get('copied') and not j.get('rejected')
                        and not j.get('held') and not j.get('conflict')]
        self.summary = [
            u'تم تحديث %d قناة (%d برنامج) خلال %d ثانية' % (
                self.stats['channels'], self.stats['events'], time.time() - self.started),
            u'من النسخة الاحتياطية: %d | لم تُحدَّث: %d | تخطي (حديثة): %d' % (
                self.stats['backup'], self.stats['failed'], self.stats['skipped'])]
        if rejected or held:
            self.summary.append(u'للأمان: رُفض %d ربط لا يطابق دليل القمر، و %d ربط غير مؤكد ينتظر مراجعتك' % (
                len(rejected), len(held)))
        if n_copied:
            self.summary.append(u'نُسخ الدليل من نسخة أخرى لنفس القناة (نفس الباقة): %d قناة' % n_copied)
        bq = sorted([b for b in bouquet_stats(self.bouquets, state) if b['events']],
                    key=lambda b: -b['events'])
        if bq:
            self.summary.append(u'البرامج لكل باقة: ' + u'، '.join(
                u'%s %d' % (b['name'], b['events']) for b in bq[:8]) + (u' ...' if len(bq) > 8 else u''))
        if failed_names:
            self.summary.append(u'بدون بيانات: ' + u'، '.join(failed_names[:15]) +
                                (u' ...' if len(failed_names) > 15 else u''))
        for line in self.summary:
            log(line)

# --------------------------------------------------------------------- helpers for screens
def fetch_preview(key, cfg):
    src, _, cid = key.partition(':')
    refresh_cloud(cfg['cloud_url'], use_cloud=cfg.get('cloud'))
    f = Fetcher(cfg)
    try:
        return f.fetch(src, [cid]).get(cid, [])
    finally:
        f.cleanup()


def test_sources(cfg):
    out = []
    t = time.time()
    idx = refresh_cloud(cfg['cloud_url'], force=True, use_cloud=cfg.get('cloud'))
    files = idx.get('files') or {}
    cloud = [k for k, f in files.items() if not f.get('direct')]
    if cfg.get('cloud'):
        if cloud:
            out.append(u'السحابة: تعمل - %d ملف، آخر بناء %s' % (
                len(cloud), time.strftime('%d/%m %H:%M', time.localtime(idx.get('generated', 0)))))
        else:
            out.append(u'السحابة: غير جاهزة بعد - ارفع المستودع على GitHub وشغّل Build EPG')
    for k, f in sorted(files.items()):
        if f.get('direct'):
            out.append(u'%s: %d قناة' % (f.get('title', k), len(f.get('channels') or [])))
    out.append(u'(%.1f ث)' % (time.time() - t))
    checks = [
        ('Shahid/MBC (MBC Masr)', lambda: sources.grab_shahid(['387290'], 1, cfg['lang'], cfg['country']).get('387290')),
        ('OSN (OSN Kids)', lambda: sources.grab_osn(['4506'], 1, cfg['lang']).get('4506')),
        ('elCinema (CBC)', lambda: sources.grab_elcinema(['1198'], 2, cfg['elcinema_offset']).get('1198')),
        ('beIN SPORTS (beIN SPORTS 1)', lambda: sources.grab_beinsports(
            ['7836FEA9-6B39-4A1A-8352-DC5FCB97A16C'], 1).get('7836FEA9-6B39-4A1A-8352-DC5FCB97A16C')),
        ('Roya (Roya TV)', lambda: sources.grab_roya(['1'], 1).get('1')),
        ('ART (Aflam 1)', lambda: sources.grab_art(['#'], 1).get('#')),
        ('Oman TV', lambda: sources.grab_ayn([u'158/قناة-عمان-العامة'], 2).get(u'158/قناة-عمان-العامة')),
        ('SNRT (Al Aoula)', lambda: sources.grab_snrt(['1208'], 2).get('1208')),
        ('LBCI', lambda: sources.grab_lbci(['1'], 2).get('1')),
        ('Tunisia TV (Watania 1)', lambda: sources.grab_tunisia(['69c58aac8d6cac7a55cd2e09'], 1).get('69c58aac8d6cac7a55cd2e09')),
        ('TRT Arabi', lambda: sources.grab_trt(['1'], 1).get('1')),
        ('beIN (Alkass One)', lambda: sources.grab_bein(['sports#33'], 1, cfg['lang']).get('sports#33')),
        ('Rotana (Rotana Drama)', lambda: sources.grab_rotana(['436'], 2, cfg['lang']).get('436')),
        ('Al Jazeera', lambda: sources.grab_aljazeera(['aja#'], 2).get('aja#')),
    ]
    for name, fn in checks:
        t = time.time()
        try:
            evs = fn() or []
            out.append(u'%s: %s (%d برنامج، %.1f ث)' % (name, u'يعمل' if evs else u'لا توجد بيانات', len(evs),
                                                         time.time() - t))
            if evs:
                e = [x for x in evs if x[0] + x[1] > time.time()][:1] or evs[:1]
                out.append(u'    مثال: %s %s' % (time.strftime('%d/%m %H:%M', time.localtime(e[0][0])), e[0][2]))
        except Exception as e:
            out.append(u'%s: فشل - %s' % (name, e))
    for line in out:
        log(line)
    return out


def check_plugin_update(current):
    """-> version info dict when a newer package is published, else None"""
    try:
        info = json.loads(sources.http_get(VERSION_URL, timeout=20).decode('utf-8'))
    except Exception as e:
        log(u'تعذر التحقق من تحديث البلوجين: %s' % e)
        return None

    def v(x):
        return tuple(int(p) for p in str(x).split('.') if p.isdigit())
    return info if v(info.get('version', '0')) > v(current) else None


def install_plugin_update(info):
    """download the ipk and install it with opkg. -> (ok, message)"""
    import subprocess
    path = '/tmp/saadepg_update.ipk'
    try:
        sources.http_download(info['ipk'], path, timeout=60)
        p = subprocess.Popen(['opkg', 'install', '--force-reinstall', '--force-overwrite', path],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = p.communicate()
        return p.returncode == 0, u(out)[-600:]
    except Exception as e:
        return False, u(e)


__all__ = ['norm']


# --------------------------------------------------------------------- linking the channel you watch by hand
_FPIDX = {'sig': None, 'by_min': None}
_ALL_SOURCES = {'sig': None, 'm': None}


def _fp_index(cfg):
    """{minute: [(key, title hash)]} of every cloud channel for the next day (kept while the cloud is the same)"""
    sig = load_index().get('generated')
    if _FPIDX['sig'] != sig or _FPIDX['by_min'] is None:
        fpl = FPLinker(cfg, [])
        _FPIDX['by_min'], _FPIDX['sig'] = fpl._index(), sig
    return _FPIDX['by_min'] or {}


def _matcher_all_sources():
    xml = cloud_xml_entries(None, every=True)
    sig = (len(catalog()), len(xml), load_index().get('generated'))
    if _ALL_SOURCES['sig'] != sig:
        _ALL_SOURCES['m'] = Matcher(list(catalog()) + xml, SOURCE_ORDER)
        _ALL_SOURCES['sig'] = sig
    return _ALL_SOURCES['m']


def _chunk_of(key, idx):
    """plugin key -> (cloud file, channel id) of its per-channel file, or None"""
    files = idx.get('files') or {}
    if key.startswith('xml:'):
        fn, _, cid = key[4:].partition('/')
    else:
        src, _, i = key.partition(':')
        fn, cid = SITE_FILE.get(src), u'%s.%s' % (src, i)
    f = files.get(fn) or {}
    return (fn, cid) if f.get('ch') else None


def link_candidates(name, live, cfg, query=None, limit=24):
    """guides that look like the channel you are watching. live: [(begin, duration, title)] of what the channel
    itself says is on now / next (and its satellite guide, when it has one). Any thread.
    -> [{'key', 'label', 'why', 'now', 'score'}] best first"""
    refresh_cloud(cfg['cloud_url'], use_cloud=cfg.get('cloud'))
    idx = load_index()
    allowed = set(list(cfg.get('sources') or []) + ['xml'])

    def usable(k):
        return not k.startswith('xml:est/') and (k.startswith('xml:') or k.split(':')[0] in allowed)
    found = {}
    m = _matcher_all_sources()
    for k, sc in m.match_all(query or name, usable, min_score=0.5 if not query else 0.45, limit=16):
        found[k] = {'name': sc, 'hit': 0, 'tit': 0, 'odd': 0}
    if query:     # a word typed by you: every channel that contains it
        q = norm(query)
        for e in list(catalog()) + cloud_xml_entries(None, every=True):
            key = u'%s:%s' % (e['src'], e['id'])
            if q and usable(key) and any(q in norm(n) for n in e['names']):
                found.setdefault(key, {'name': 0.8, 'hit': 0, 'tit': 0, 'odd': 0, 'q': 1})
            if len(found) > 120:
                break
    now = time.time()
    marks = [(int(b // 60), title_hash(t), int(b // 60) % 30) for b, _d, t in live or () if b + _d > now - 3600]
    if marks:
        by_min = _fp_index(cfg)
        for mn, h, rest in marks:
            seen = set()
            for dm in (-2, -1, 0, 1, 2):
                for key, kh in by_min.get(mn + dm, ()):
                    if key in seen or not usable(key):
                        continue
                    seen.add(key)
                    x = found.setdefault(key, {'name': 0, 'hit': 0, 'tit': 0, 'odd': 0})
                    x['hit'] += 1
                    if h and h == kh:
                        x['tit'] += 1
                    if 3 <= rest <= 27:        # 18:47 says more than 19:00
                        x['odd'] += 1
    out = []
    for key, x in found.items():
        sched = x['tit'] * 3 + x['odd'] * 2 + (x['hit'] if x['hit'] >= 2 else 0)
        if not x['name'] and not x['tit'] and x['odd'] < 2:
            continue          # only round start times in common: almost every channel
        score = x['name'] * 10 + sched
        why = []
        if x['tit']:
            why.append(u'نفس البرنامج الآن')
        elif x['odd'] >= 2 or x['hit'] >= 2:
            why.append(u'نفس مواعيد البرامج')
        if x.get('q'):
            why.append(u'الاسم فيه "%s"' % query)
        elif x['name'] >= 0.5:
            why.append(u'اسم مشابه %d%%' % int(x['name'] * 100))
        out.append({'key': key, 'score': score, 'why': u' + '.join(why)})
    out.sort(key=lambda r: -r['score'])
    out = out[:limit]
    # what each candidate shows now: you recognise your channel by its programme
    pairs = [p for p in (_chunk_of(r['key'], idx) for r in out) if p]
    got = {}
    if pairs:
        f = Fetcher(cfg)
        try:
            got, _bad = f.chunks(pairs, progress=False)
        except Exception as e:
            log(u'ربط يدوي: %s' % e)
    for r in out:
        r['label'] = display_key(r['key'])
        p = _chunk_of(r['key'], idx)
        evs = got.get(p) or [] if p else []
        cur = [e for e in evs if e[0] <= now < e[0] + e[1]]
        nxt = [e for e in evs if e[0] > now]
        r['now'] = (cur[0][2], cur[0][0]) if cur else None
        r['next'] = (nxt[0][2], nxt[0][0]) if nxt else None
        r['n'] = len(evs)
    return out


def link_by_hand(ref, name, key):
    """your choice for this channel: kept for good, the old automatic link is not suggested again"""
    m = load_mapping()
    old = None
    auto = load_auto().get(ref) or []
    if auto:
        old = auto[0][0]
    m[ref] = key
    save_mapping(m)
    if key not in ('-', '!') and not key.startswith('chan:'):
        learn(name, key, ref)
        if old and old != key:
            reject(name, old)
    log(u'ربط يدوي: %s ← %s' % (name, display_key(key)))
