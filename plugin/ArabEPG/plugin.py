# -*- coding: utf-8 -*-
# ArabEPG - online EPG for Arabic and European satellite channels
# Enigma2 plugin, Python 2 and Python 3 images (OpenATV, OpenPLi, OpenBH, Egami, VTi ...)
from __future__ import absolute_import, print_function

import os
import re
import threading
import time

from enigma import eTimer, getDesktop, eEPGCache, eServiceReference
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList
from Components.Pixmap import Pixmap
from Components.ProgressBar import ProgressBar
from Components.ScrollLabel import ScrollLabel
from Components.ConfigList import ConfigListScreen
from Components.config import (config, ConfigSubsection, ConfigYesNo, ConfigSelection, ConfigClock,
                               ConfigText, getConfigListEntry, configfile)
from Plugins.Plugin import PluginDescriptor
from Screens.Screen import Screen
from Screens.ChoiceBox import ChoiceBox
from Screens.MessageBox import MessageBox

from . import engine, sources
from .sources import log, u, PY3

VERSION = '4.1'

# --------------------------------------------------------------------- config
config.plugins.arabepg = ConfigSubsection()
C = config.plugins.arabepg
C.cloud = ConfigYesNo(default=True)
C.cloud_url = ConfigText(default=engine.CLOUD_URL, fixed_size=False)
C.src_shahid = ConfigYesNo(default=True)
C.src_osn = ConfigYesNo(default=True)
C.src_bein = ConfigYesNo(default=True)
C.src_rotana = ConfigYesNo(default=True)
C.src_aljazeera = ConfigYesNo(default=True)
C.src_elcinema = ConfigYesNo(default=True)
C.src_beinsports = ConfigYesNo(default=True)
C.src_roya = ConfigYesNo(default=True)
C.src_art = ConfigYesNo(default=True)
C.src_ayn = ConfigYesNo(default=True)
C.src_snrt = ConfigYesNo(default=True)
C.src_xmltv = ConfigYesNo(default=False)
C.xmltv_urls = ConfigText(default='', fixed_size=False)
C.days = ConfigSelection(default='3', choices=[(str(i), str(i)) for i in range(1, 8)])
C.target = ConfigSelection(default='all', choices=[
    ('all', 'كل القنوات المربوطة'), ('missing', 'القنوات بدون EPG كامل فقط')])
C.lang = ConfigSelection(default='ar', choices=[('ar', 'العربية'), ('en', 'English')])
C.country = ConfigSelection(default='EG', choices=[('EG', 'مصر'), ('SA', 'السعودية'), ('AE', 'الإمارات')])
C.elcinema_tz = ConfigSelection(default='local', choices=[
    ('local', 'نفس توقيت الريسيفر'), ('120', 'GMT+2'), ('180', 'GMT+3'), ('240', 'GMT+4')])
C.autoupdate = ConfigYesNo(default=True)
C.update_time = ConfigClock(default=int(time.mktime((2000, 1, 1, 5, 20, 0, 0, 0, -1))))
C.boot_update = ConfigYesNo(default=True)
C.check_updates = ConfigYesNo(default=True)
C.match_level2 = ConfigSelection(default='90', choices=[('95', 'دقيق جدا'), ('90', 'متوازن'), ('85', 'مرن')])
C.verify = ConfigYesNo(default=True)
C.lang_mode = ConfigSelection(default='ar_only', choices=[
    ('ar_only', 'عربي فقط (من مصادر عربية موثوقة)'), ('translate', 'عربي + ترجمة الإنجليزي إلى العربية'),
    ('ar_first', 'عربي أولا ثم إنجليزي'), ('any', 'أي لغة')])
C.tr_provider = ConfigSelection(default='mymemory', choices=[('mymemory', 'MyMemory (مجاني)'),
                                                             ('libre', 'LibreTranslate (سيرفر خاص)')])
C.tr_email = ConfigText(default='', fixed_size=False)
C.tr_url = ConfigText(default='', fixed_size=False)
C.tr_key = ConfigText(default='', fixed_size=False)
C.tr_limit = ConfigSelection(default='1500', choices=[('500', '500'), ('1500', '1500'), ('3000', '3000')])
C.zap_epg = ConfigYesNo(default=True)
C.zap_notify = ConfigYesNo(default=True)
C.copy_epg = ConfigYesNo(default=True)

SRC_CFG = (('shahid', C.src_shahid), ('osn', C.src_osn), ('bein', C.src_bein), ('rotana', C.src_rotana),
           ('aljazeera', C.src_aljazeera), ('elcinema', C.src_elcinema), ('beinsports', C.src_beinsports),
           ('roya', C.src_roya), ('art', C.src_art), ('ayn', C.src_ayn), ('snrt', C.src_snrt),
           ('xmltv', C.src_xmltv))


def T(s):
    s = u(s)
    return s if PY3 else s.encode('utf-8')


def make_timer(cb):
    t = eTimer()
    try:
        t.callback.append(cb)
    except AttributeError:  # DreamOS
        t.conn = t.timeout.connect(cb)
    return t


def current_cfg(**over):
    url = C.cloud_url.value.strip() or engine.CLOUD_URL
    if not url.endswith('/'):
        url += '/'
    cfg = {
        'days': int(C.days.value), 'sources': [s for s, c in SRC_CFG if c.value], 'target': C.target.value,
        'lang': C.lang.value, 'country': C.country.value, 'cloud': C.cloud.value, 'cloud_url': url,
        'elcinema_offset': None if C.elcinema_tz.value == 'local' else int(C.elcinema_tz.value),
        'xmltv_urls': [x.strip() for x in C.xmltv_urls.value.split(',') if x.strip()], 'smart': False,
        'min_score': int(C.match_level2.value) / 100.0, 'verify': C.verify.value, 'lang_mode': C.lang_mode.value,
        'tr_provider': C.tr_provider.value, 'tr_email': C.tr_email.value.strip(), 'tr_url': C.tr_url.value.strip(),
        'tr_key': C.tr_key.value.strip(), 'tr_limit': int(C.tr_limit.value), 'copy_epg': C.copy_epg.value,
    }
    cfg.update(over)
    return cfg


def start_update(refs=None, **over):
    return engine.Updater(current_cfg(**over), refs).start()


class BackgroundJob(object):
    """run fn() in a thread and hand its result to done(result) on the main thread."""

    def __init__(self, fn, done):
        self.fn, self.done, self.result, self.finished = fn, done, None, False
        self.timer = make_timer(self._check)
        t = threading.Thread(target=self._work)
        t.daemon = True
        t.start()
        self.timer.start(300, False)

    def _work(self):
        try:
            self.result = self.fn()
        except Exception as e:
            self.result = e
        self.finished = True

    def _check(self):
        if self.finished:
            self.timer.stop()
            self.done(self.result)

    def cancel(self):
        self.timer.stop()


# --------------------------------------------------------------------- skin helpers
_F = getDesktop(0).size().width() / 1280.0
BG, PANEL, ACCENT, TXT, DIM = '#10141c', '#1b2230', '#2f8f5b', '#f0f0f0', '#9aa4b2'


def _s(v):
    return int(v * _F)


def skin(w, h, body, title='ArabEPG'):
    head = ('<eLabel position="0,0" size="%d,%d" backgroundColor="%s" zPosition="-2" />'
            '<eLabel position="0,0" size="%d,%d" backgroundColor="%s" zPosition="-1" />') % (
        _s(w), _s(h), BG, _s(w), _s(4), ACCENT)
    return ('<screen position="center,center" size="%d,%d" title="%s" backgroundColor="%s" flags="wfNoBorder">'
            '<widget name="title" zPosition="1" position="%d,%d" size="%d,%d" font="Regular;%d" foregroundColor="%s" '
            'backgroundColor="%s" transparent="1" halign="right" />%s%s</screen>') % (
        _s(w), _s(h), title, BG, _s(20), _s(12), _s(w - 40), _s(40), _s(28), TXT, BG, head, body)


def buttons(y, w):
    out = ''
    cols = (('red', '#b22222'), ('green', '#2e8b57'), ('yellow', '#c9a227'), ('blue', '#2b5fb3'))
    bw = (w - 50) // 4
    for i, (n, col) in enumerate(cols):
        x = 10 + i * (bw + 10)
        out += ('<eLabel position="%d,%d" size="%d,%d" backgroundColor="%s" />'
                '<widget name="key_%s" zPosition="1" position="%d,%d" size="%d,%d" font="Regular;%d" halign="center" '
                'valign="center" foregroundColor="%s" backgroundColor="%s" transparent="1" />') % (
            _s(x), _s(y + 32), _s(bw), _s(4), col, n, _s(x), _s(y), _s(bw), _s(32), _s(19), TXT, BG)
    return out


def set_buttons(scr, red='', green='', yellow='', blue=''):
    for k, v in (('red', red), ('green', green), ('yellow', yellow), ('blue', blue)):
        scr['key_' + k] = Label(T(v))


class BaseScreen(Screen):
    def __init__(self, session, title):
        Screen.__init__(self, session)
        self['title'] = Label(T(title))
        self.setTitle(T(title))

    def set_title(self, t):
        self['title'].setText(T(t))
        self.setTitle(T(t))


# --------------------------------------------------------------------- picons
PICON_DIRS = ('/usr/share/enigma2/picon/', '/media/hdd/picon/', '/media/usb/picon/', '/media/mmc/picon/',
              '/picon/', '/usr/share/enigma2/XPicons/picon/', '/media/hdd/XPicons/picon/',
              '/media/usb/XPicons/picon/', '/data/picon/')
_PICON_CACHE = {}


def find_picon(ref, name):
    key = (ref, name)
    if key in _PICON_CACHE:
        return _PICON_CACHE[key]
    p = ref.rstrip(':').split(':')
    names = ['_'.join(p).upper()]
    if len(p) > 2 and p[2] != '1':
        names.append('_'.join(p[:2] + ['1'] + p[3:]).upper())
    snp = re.sub(r'[^a-z0-9]', '', u(name).lower().replace('&', 'and').replace('+', 'plus').replace('*', 'star'))
    if snp:
        names.append(snp)
    found = None
    for d in PICON_DIRS:
        if not os.path.isdir(d):
            continue
        for n in names:
            f = d + n + '.png'
            if os.path.exists(f):
                found = f
                break
        if found:
            break
    _PICON_CACHE[key] = found
    return found


def show_picon(widget, path):
    try:
        from Tools.LoadPixmap import LoadPixmap
        if path and widget.instance:
            widget.instance.setScale(1)
            widget.instance.setPixmap(LoadPixmap(path))
            widget.show()
        else:
            widget.hide()
    except Exception:
        widget.hide()


# --------------------------------------------------------------------- main menu
class ArabEPGMain(BaseScreen):
    def __init__(self, session):
        self.skin = skin(820, 600, (
            '<widget name="list" position="%d,%d" size="%d,%d" itemHeight="%d" font="Regular;%d" '
            'scrollbarMode="showOnDemand" backgroundColor="%s" foregroundColor="%s" transparent="1" />'
            '<eLabel position="%d,%d" size="%d,%d" backgroundColor="%s" />'
            '<widget name="status" zPosition="1" position="%d,%d" size="%d,%d" font="Regular;%d" halign="right" '
            'foregroundColor="%s" backgroundColor="%s" transparent="1" />') % (
            _s(20), _s(64), _s(780), _s(380), _s(42), _s(24), BG, TXT,
            _s(0), _s(452), _s(820), _s(148), PANEL,
            _s(20), _s(462), _s(780), _s(128), _s(19), DIM, PANEL))
        BaseScreen.__init__(self, session, u'ArabEPG %s - دليل البرامج' % VERSION)
        self.menu_items = [
            (u'تحديث دليل البرامج الآن', 'update'),
            (u'الآن وبعده على كل القنوات', 'nownext'),
            (u'القنوات والربط الذكي', 'channels'),
            (u'إعادة الربط الذكي لكل القنوات', 'relink'),
            (u'إحصائيات الباقات وعدد البرامج', 'stats'),
            (u'طريقة الربط اليدوي للقنوات', 'help'),
            (u'قنوات دليلها غير عربي', 'nonar'),
            (u'تصدير الربط المؤكد (لمشاركته عبر السحابة)', 'export'),
            (u'مسح دليل البلوجين وإعادة تحميله', 'clear'),
            (u'اختبار الاتصال بالمصادر والسحابة', 'test'),
            (u'استيراد ملفات EPG (XMLTV)', 'import'),
            (u'الإعدادات', 'settings'),
            (u'سجل العمليات', 'log'),
            (u'التحقق من تحديث البلوجين', 'selfupdate'),
            (u'حذف الربط اليدوي لكل القنوات', 'reset'),
        ]
        self['list'] = MenuList([(T(a), b) for a, b in self.menu_items])
        self['status'] = Label('')
        self['actions'] = ActionMap(['OkCancelActions'], {'ok': self.ok, 'cancel': self.close}, -1)
        self.onShown.append(self.refresh)

    def refresh(self, *args):
        st = engine.load_state()
        lines = []
        r = engine.Updater.running
        if r and not r.done:
            lines.append(u'جاري التحديث في الخلفية: %d%% - %s' % (r.percent(), r.progress))
        elif st.get('last'):
            s = st.get('last_stats', {})
            lines.append(u'آخر تحديث: %s - %s قناة، %s برنامج' % (
                time.strftime('%Y-%m-%d %H:%M', time.localtime(st['last'])), s.get('channels', 0), s.get('events', 0)))
        else:
            lines.append(u'لم يتم التحديث بعد - اختر "تحديث دليل البرامج الآن"، الربط يتم تلقائيا.')
        idx = engine.load_index()
        if C.cloud.value:
            lines.append(u'السحابة: %s' % (time.strftime('آخر بناء %d/%m %H:%M', time.localtime(idx['generated']))
                                            if idx.get('generated') else u'لم يتم الاتصال بعد'))
        if AutoUpdate.instance and AutoUpdate.instance.new_version:
            lines.append(u'يوجد إصدار جديد من البلوجين: %s' % AutoUpdate.instance.new_version.get('version'))
        self['status'].setText(T(u'\n'.join(lines)))

    def ok(self):
        sel = self['list'].getCurrent()
        if not sel:
            return
        a = sel[1]
        if a == 'update':
            start_update()
            self.session.openWithCallback(self.refresh, UpdateScreen)
        elif a == 'nownext':
            self.session.open(NowNextScreen)
        elif a in ('channels', 'relink'):
            self.session.openWithCallback(self.refresh, ChannelsScreen, a == 'relink')
        elif a == 'test':
            self.session.open(TextScreen, u'اختبار الاتصال', lambda: u'\n'.join(engine.test_sources(current_cfg())))
        elif a == 'settings':
            self.session.openWithCallback(self.refresh, SettingsScreen)
        elif a == 'log':
            self.session.open(LogScreen)
        elif a == 'selfupdate':
            self.session.open(TextScreen, u'تحديث البلوجين', self_update_text, self._offer_update)
        elif a == 'nonar':
            self.session.open(TextScreen, u'قنوات دليلها غير عربي', non_arabic_text)
        elif a == 'export':
            self.session.open(TextScreen, u'تصدير الربط المؤكد', export_text)
        elif a == 'help':
            self.session.open(TextScreen, u'طريقة الربط اليدوي', lambda: MANUAL_HELP)
        elif a == 'clear':
            self.session.openWithCallback(self._clear, MessageBox, T(
                u'سيتم مسح الدليل الذي أضافه البلوجين، ونسيان الربط التلقائي والنسخة الاحتياطية '
                u'(الربط اليدوي يبقى)، ثم إعادة التحميل من المصادر العربية.\nمتابعة؟'), MessageBox.TYPE_YESNO)
        elif a == 'stats':
            self.session.open(StatsScreen)
        elif a == 'import':
            self.session.open(TextScreen, u'استيراد ملفات EPG', import_text)
        elif a == 'reset':
            self.session.openWithCallback(self._reset, MessageBox,
                                          T(u'حذف كل الربط اليدوي والعودة للربط التلقائي الذكي؟'), MessageBox.TYPE_YESNO)

    def _offer_update(self, info):
        if isinstance(info, dict):
            self.session.openWithCallback(lambda yes: yes and self.session.open(InstallScreen, info), MessageBox,
                                          T(u'تثبيت الإصدار %s الآن؟' % info.get('version')), MessageBox.TYPE_YESNO)

    def _clear(self, yes):
        if not yes:
            return
        r = engine.Updater.running
        if r and not r.done:          # an update is running: stop it and come back when it ended
            r.cancel = True
            log(u'إيقاف التحديث الجاري قبل المسح...')
            self._wait = make_timer(lambda: self._clear_when_idle())
            self._wait.start(1000, False)
            return
        self._do_clear()

    def _clear_when_idle(self):
        r = engine.Updater.running
        if r and not r.done:
            return
        self._wait.stop()
        self._do_clear()

    def _do_clear(self):
        refs = list(engine.load_state().get('filled', {}))
        try:
            n, how = engine.clear_plugin_epg(eEPGCache.getInstance(), refs)
        except Exception as e:
            n, how = 0, None
            log(u'تعذر مسح الدليل: %s' % e)
        if how is None and refs:
            self.session.openWithCallback(lambda yes: self._clear_all(yes, refs), MessageBox, T(
                u'هذه الصورة لا تسمح بمسح دليل قناة واحدة.\n'
                u'هل تريد مسح كل الـ EPG (سيعود دليل القمر تلقائيا عند المشاهدة)؟'), MessageBox.TYPE_YESNO)
            return
        log(u'تم مسح دليل %d قناة' % n)
        self._reload()

    def _clear_all(self, yes, refs):
        if yes:
            engine.clear_plugin_epg(eEPGCache.getInstance(), refs, everything=True)
            log(u'تم مسح كل الـ EPG')
        self._reload()

    def _reload(self):
        engine.reset_plugin_data()
        if start_update(target='all'):
            self.session.openWithCallback(self.refresh, UpdateScreen)

    def _reset(self, yes):
        if yes:
            engine.save_mapping({})


MANUAL_HELP = u'''الربط التلقائي يكفي لمعظم القنوات. استخدم الربط اليدوي فقط للقناة التي دليلها خطأ أو غير موجود.

١. من قائمة البلوجين اختر: القنوات والربط الذكي.
٢. اضغط الأصفر لتغيير العرض:
     المربوطة / تحتاج مراجعة / غير المربوطة / كل القنوات.
٣. انزل إلى القناة. اللوحة الجانبية تعرض: المصدر الحالي ونسبة الثقة، والمصادر البديلة،
   وهل رقم الخدمة موجود في قاعدة المراجع، والبرنامج الحالي والتالي.
٤. اضغط الأزرق (معاينة البرامج) لترى جدول القناة من المصدر قبل اعتماده.
   لو البرامج لا تطابق ما يعرض على القناة فعلا، فالربط خطأ.
٥. اضغط OK لتغيير المصدر. ستجد:
     - تأكيد الربط الحالي: لو المعاينة صحيحة.
     - مقترح ...: أفضل المصادر المرشحة مع نسبة الثقة.
     - بحث بالاسم: اكتب جزءا من اسم القناة (عربي أو إنجليزي) واختر من النتائج.
     - ربط تلقائي ذكي: يلغي اختيارك ويعود للتلقائي.
     - تعطيل البلوجين لهذه القناة: لو دليل القمر نفسه صحيح.
٦. اضغط الأخضر ثم "تحديث هذه القناة فقط" لتطبيق الربط فورا.

ملاحظات:
- البلوجين يتعلم: لو ربطت قناة يدويا، يُطبَّق نفس الربط على كل قناة بنفس الاسم على الأقمار الأخرى،
  ولو استبدلت ربطا خاطئا لن يقترحه مرة أخرى.
- الأحمر يعطّل/يفعّل البلوجين للقناة.
- لإعادة كل شيء من البداية: "مسح دليل البلوجين وإعادة تحميله".
- لاختيار لغة الدليل: الإعدادات ← لغة الدليل للقنوات العربية.'''


def non_arabic_text():
    names = engine.load_state().get('non_arabic') or []
    if not names:
        return u'لا توجد قنوات عربية بدليل غير عربي في آخر تحديث.'
    return (u'هذه القنوات العربية لم يُعثر لها إلا على دليل غير عربي في آخر تحديث (%d قناة).\n'
            u'الحلول: اختر لها مصدرا آخر من "القنوات والربط الذكي"، أو من الإعدادات اختر لغة الدليل:\n'
            u'"عربي + ترجمة الإنجليزي إلى العربية" أو "عربي أولا ثم إنجليزي".\n\n' % len(names)) + u'\n'.join(names)


def export_text():
    path, n = engine.export_linked_refs()
    return (u'تم حفظ %d ربط مؤكد في الملف:\n%s\n\n'
            u'لمشاركته مع كل الريسيفرات (ومع أي ريسيفر جديد لك):\n'
            u'ارفع الملف إلى مستودعك على GitHub في المسار cloud/linked_refs.json\n'
            u'وسيصل تلقائيا بعد تشغيل Build EPG.') % (n, path)


def self_update_text():
    info = engine.check_plugin_update(VERSION)
    if info:
        return info, u'يوجد إصدار جديد: %s (الحالي %s)\n%s' % (info.get('version'), VERSION, info.get('notes', ''))
    return None, u'أنت تستخدم آخر إصدار (%s).' % VERSION


class InstallScreen(BaseScreen):
    def __init__(self, session, info):
        self.skin = skin(900, 420, '<widget name="text" position="%d,%d" size="%d,%d" font="Regular;%d" '
                                   'foregroundColor="%s" backgroundColor="%s" transparent="1" />' % (
                                       _s(20), _s(64), _s(860), _s(340), _s(20), TXT, BG))
        BaseScreen.__init__(self, session, u'تحديث البلوجين')
        self['text'] = ScrollLabel(T(u'جاري تحميل وتثبيت الإصدار %s...' % info.get('version')))
        self['actions'] = ActionMap(['OkCancelActions'], {'ok': self.ok, 'cancel': self.close}, -1)
        self.ok_done = None
        self.job = BackgroundJob(lambda: engine.install_plugin_update(info), self.finished)

    def finished(self, res):
        if isinstance(res, Exception):
            res = (False, u(res))
        self.ok_done = res[0]
        self['text'].setText(T((u'تم التثبيت بنجاح. اضغط OK لإعادة تشغيل الواجهة.\n\n' if res[0]
                                else u'فشل التثبيت:\n\n') + res[1]))

    def ok(self):
        if self.ok_done:
            from Screens.Standby import TryQuitMainloop
            self.session.open(TryQuitMainloop, 3)
        else:
            self.close()


# --------------------------------------------------------------------- update progress screen
_STATE_MARK = {'wait': u'○', 'run': u'►', 'ok': u'✔', 'fail': u'✘', 'skip': u'–'}


class UpdateScreen(BaseScreen):
    def __init__(self, session):
        self.skin = skin(1080, 680, (
            '<widget name="bar" position="%d,%d" size="%d,%d" borderWidth="1" borderColor="%s" '
            'foregroundColor="%s" backgroundColor="%s" />'
            '<widget name="pct" position="%d,%d" size="%d,%d" font="Regular;%d" halign="right" '
            'foregroundColor="%s" backgroundColor="%s" transparent="1" />'
            '<eLabel position="%d,%d" size="%d,%d" backgroundColor="%s" />'
            '<widget name="stages" zPosition="1" position="%d,%d" size="%d,%d" font="Regular;%d" halign="right" '
            'foregroundColor="%s" backgroundColor="%s" transparent="1" />'
            '<widget name="log" position="%d,%d" size="%d,%d" font="Regular;%d" '
            'foregroundColor="%s" backgroundColor="%s" transparent="1" />') % (
            _s(20), _s(66), _s(1040), _s(26), DIM, ACCENT, PANEL,
            _s(20), _s(98), _s(1040), _s(34), _s(22), TXT, BG,
            _s(10), _s(138), _s(1060), _s(300), PANEL,
            _s(24), _s(144), _s(1032), _s(288), _s(20), TXT, PANEL,
            _s(20), _s(446), _s(1040), _s(176), _s(16), DIM, BG) + buttons(632, 1080))
        BaseScreen.__init__(self, session, u'تحديث دليل البرامج')
        self['bar'] = ProgressBar()
        self['pct'] = Label('')
        self['stages'] = Label('')
        self['log'] = ScrollLabel('')
        set_buttons(self, u'إيقاف', u'إخفاء (يستمر بالخلفية)', u'السجل الكامل', '')
        self['actions'] = ActionMap(['OkCancelActions', 'ColorActions', 'DirectionActions'], {
            'cancel': self.close, 'ok': self.close, 'green': self.close, 'red': self.stop,
            'yellow': lambda: self.session.open(LogScreen),
            'up': self['log'].pageUp, 'down': self['log'].pageDown}, -1)
        self.last_log = -1
        self.timer = make_timer(self.tick)
        self.onLayoutFinish.append(self.tick)
        self.timer.start(700, False)
        self.onClose.append(self.timer.stop)

    def tick(self):
        r = engine.Updater.running
        if not r:
            self['pct'].setText(T(u'لا يوجد تحديث قيد التشغيل'))
            return
        p = r.percent()
        self['bar'].setValue(p)
        eta = r.eta()
        el = int(time.time() - r.started)
        txt = u'%d%%   -   %s' % (p, r.progress)
        txt += u'   -   مضى %d:%02d' % (el // 60, el % 60)
        if eta is not None:
            txt += u'   -   متبقي تقريبا %d:%02d' % (eta // 60, eta % 60)
        if r.done:
            txt = u'100%   -   اكتمل التحديث خلال ' + u'%d:%02d' % (el // 60, el % 60)
        self['pct'].setText(T(txt))
        lines = []
        for s in r.stages:
            extra = s.info or (u'%d/%d' % (s.done, s.total) if s.total > 1 and s.state == 'run' else u'')
            lines.append(u'%s  %s   %s' % (_STATE_MARK.get(s.state, u'?'), s.label, extra))
        if r.done and r.summary:
            lines.append(u'')
            lines.extend(r.summary)
        self['stages'].setText(T(u'\n'.join(lines[-11:])))
        if len(sources.LOG) != self.last_log:
            self.last_log = len(sources.LOG)
            self['log'].setText(T(u'\n'.join(sources.LOG[-30:])))
            try:
                self['log'].lastPage()
            except Exception:
                pass
        if r.done:
            self['key_red'].setText('')
            self['key_green'].setText(T(u'إغلاق'))

    def stop(self):
        r = engine.Updater.running
        if r and not r.done:
            r.cancel = True
            log(u'تم طلب إيقاف التحديث...')


# --------------------------------------------------------------------- text / log screens
class TextScreen(BaseScreen):
    def __init__(self, session, title, fn, after=None):
        self.skin = skin(1000, 620, '<widget name="text" position="%d,%d" size="%d,%d" font="Regular;%d" '
                                    'foregroundColor="%s" backgroundColor="%s" transparent="1" />' % (
                                        _s(20), _s(64), _s(960), _s(540), _s(20), TXT, BG))
        BaseScreen.__init__(self, session, title)
        self['text'] = ScrollLabel(T(u'انتظر قليلا...'))
        self['actions'] = ActionMap(['OkCancelActions', 'DirectionActions'], {
            'cancel': self.close, 'ok': self.ok, 'up': self['text'].pageUp, 'down': self['text'].pageDown,
            'left': self['text'].pageUp, 'right': self['text'].pageDown}, -1)
        self.after = after
        self.payload = None
        self.job = BackgroundJob(fn, self.show_result)
        self.onClose.append(self.job.cancel)

    def show_result(self, res):
        if isinstance(res, Exception):
            res = u'خطأ: %s' % res
        if isinstance(res, tuple):
            self.payload, res = res
        self['text'].setText(T(res or u'لا توجد بيانات'))

    def ok(self):
        if self.after and self.payload:
            payload, after = self.payload, self.after
            self.close()
            after(payload)
        else:
            self.close()


class LogScreen(BaseScreen):
    def __init__(self, session):
        self.skin = skin(1080, 660, '<widget name="text" position="%d,%d" size="%d,%d" font="Regular;%d" '
                                    'foregroundColor="%s" backgroundColor="%s" transparent="1" />' % (
                                        _s(20), _s(64), _s(1040), _s(580), _s(18), TXT, BG))
        BaseScreen.__init__(self, session, u'سجل العمليات  (/tmp/ArabEPG.log)')
        self['text'] = ScrollLabel('')
        self['actions'] = ActionMap(['OkCancelActions', 'DirectionActions'], {
            'cancel': self.close, 'ok': self.close, 'up': self['text'].pageUp, 'down': self['text'].pageDown,
            'left': self['text'].pageUp, 'right': self['text'].pageDown}, -1)
        self.last = -1
        self.timer = make_timer(self.tick)
        self.onLayoutFinish.append(self.tick)
        self.timer.start(1000, False)
        self.onClose.append(self.timer.stop)

    def tick(self):
        if len(sources.LOG) != self.last:
            self.last = len(sources.LOG)
            self['text'].setText(T(u'\n'.join(sources.LOG[-200:])))
            try:
                self['text'].lastPage()
            except Exception:
                pass


# --------------------------------------------------------------------- channels list
class ChannelsScreen(BaseScreen):
    FILTERS = (('linked', u'المربوطة'), ('review', u'تحتاج مراجعة'), ('unlinked', u'غير المربوطة'),
               ('all', u'كل القنوات'))

    def __init__(self, session, relink=False):
        self.skin = skin(1240, 700, (
            '<widget name="list" position="%d,%d" size="%d,%d" itemHeight="%d" font="Regular;%d" '
            'scrollbarMode="showOnDemand" foregroundColor="%s" backgroundColor="%s" transparent="1" />'
            '<eLabel position="%d,%d" size="%d,%d" backgroundColor="%s" />'
            '<widget name="picon" zPosition="1" position="%d,%d" size="%d,%d" alphatest="blend" />'
            '<widget name="details" zPosition="1" position="%d,%d" size="%d,%d" font="Regular;%d" halign="right" '
            'foregroundColor="%s" backgroundColor="%s" transparent="1" />'
            '<widget name="info" position="%d,%d" size="%d,%d" font="Regular;%d" halign="right" '
            'foregroundColor="%s" backgroundColor="%s" transparent="1" />') % (
            _s(10), _s(62), _s(800), _s(540), _s(30), _s(19), TXT, BG,
            _s(820), _s(62), _s(410), _s(540), PANEL,
            _s(925), _s(76), _s(200), _s(120),
            _s(832), _s(206), _s(386), _s(390), _s(18), TXT, PANEL,
            _s(10), _s(606), _s(1220), _s(30), _s(16), DIM, BG) + buttons(642, 1240))
        BaseScreen.__init__(self, session, u'الربط الذكي للقنوات' if relink else u'القنوات وربطها')
        self['list'] = MenuList([])
        self['picon'] = Pixmap()
        self['details'] = Label('')
        self['info'] = Label(T(u'جاري قراءة القنوات وربطها تلقائيا...'))
        set_buttons(self, u'تعطيل/تفعيل', u'تحديث', u'', u'معاينة البرامج')
        self['actions'] = ActionMap(['OkCancelActions', 'ColorActions', 'DirectionActions'], {
            'ok': self.pick, 'cancel': self.close, 'red': self.disable, 'green': self.update_now,
            'yellow': self.next_filter, 'blue': self.preview,
            'up': self.up, 'down': self.down, 'left': self.pgup, 'right': self.pgdown}, -1)
        self.relink = relink
        self.filter = 0
        self.services, self.auto, self.epg = [], {}, {}
        self.job = None
        self.onLayoutFinish.append(self.load)
        self.onClose.append(lambda: self.job and self.job.cancel())

    # navigation keeps the side panel in sync
    def up(self):
        self['list'].up()
        self.side()

    def down(self):
        self['list'].down()
        self.side()

    def pgup(self):
        self['list'].pageUp()
        self.side()

    def pgdown(self):
        self['list'].pageDown()
        self.side()

    def load(self):
        try:
            self.services = engine.get_services()
        except Exception as e:
            self['info'].setText(T(u'خطأ في قراءة الباقات: %s' % e))
            return
        cfg = current_cfg()
        enabled = list(cfg['sources']) + ['xml']
        relink, services = self.relink, self.services

        def work():
            engine.refresh_cloud(cfg['cloud_url'], use_cloud=cfg['cloud'])
            return engine.smart_link(services, enabled, force=relink, min_score=cfg['min_score'])
        self.job = BackgroundJob(work, self.loaded)

    def loaded(self, res):
        if isinstance(res, Exception):
            self['info'].setText(T(u'خطأ في الربط: %s' % res))
            return
        self.auto = res
        filled = engine.load_state()['filled']
        epg = eEPGCache.getInstance()
        self.epg = {}
        for ref, _n in self.services:
            self.epg[ref] = 'A' if ref in filled else ('+' if engine.epg_count(epg, ref) >= 4 else ' ')
        self.build()

    def build(self, keep=None):
        mapping = engine.load_mapping()
        mode = self.FILTERS[self.filter][0]
        rows, n_linked = [], 0
        for ref, name in self.services:
            cands, manual = engine.resolve(ref, mapping, self.auto)
            key = cands[0][0] if cands else None
            linked = bool(key and key != '-')
            n_linked += linked
            if (mode == 'linked' and not linked) or (mode == 'unlinked' and linked):
                continue
            if mode == 'review' and not (linked and not manual and cands[0][1] < 0.97):
                continue
            if linked:
                how = u'يدوي' if manual else u'%d%%' % int(cands[0][1] * 100)
                src = u'%s (%s)' % (engine.display_key(key), how)
            else:
                src = u'(معطل)' if key == '-' else u'—'
            rows.append((T(u'[%s] %s  >>  %s' % (self.epg.get(ref, ' '), name, src)),
                         (ref, name, key, cands, manual)))
        self['list'].setList(rows)
        if keep is not None:
            for i, r in enumerate(rows):
                if r[1][0] == keep:
                    self['list'].moveToIndex(i)
                    break
        self['key_yellow'].setText(T(u'عرض: ' + self.FILTERS[(self.filter + 1) % len(self.FILTERS)][1]))
        self['info'].setText(T(u'كل القنوات: %d | مربوطة: %d | المعروض: %d     '
                               u'[+] EPG من القمر  [A] من البلوجين  [ ] لا يوجد' % (len(self.services), n_linked, len(rows))))
        self.side()

    def side(self):
        c = self.cur()
        if not c:
            self['details'].setText('')
            show_picon(self['picon'], None)
            return
        ref, name, key, cands, manual = c
        show_picon(self['picon'], find_picon(ref, name))
        st = engine.load_state()['filled'].get(ref)
        lines = [name, u'القمر: %s' % engine.SAT_TITLES.get(engine.sat_of_ref(ref), engine.sat_of_ref(ref))]
        if engine.load_refs().get(engine.ref_key(ref) or ''):
            lines.append(u'رقم الخدمة موجود في قاعدة المراجع (ربط مؤكد)')
        lines.append(u'')
        if key and key != '-':
            lines.append(u'المصدر%s:' % (u' (يدوي)' if manual else u''))
            for k, sc in cands[:4]:
                lines.append(u'  %s  %d%%' % (engine.display_key(k), int(sc * 100)))
        else:
            lines.append(u'غير مربوطة - اضغط OK للاختيار')
        if st and u(st.get('key', '')).startswith('copy:'):
            lines.append(u'')
            lines.append(u'الدليل منسوخ من: %s (نفس الباقة)' % st.get('from_name', ''))
        if st:
            lines.append(u'')
            lines.append(u'آخر تحديث: %s' % time.strftime('%d/%m %H:%M', time.localtime(st.get('t', 0))))
            if st.get('until'):
                lines.append(u'الدليل حتى: %s' % time.strftime('%d/%m %H:%M', time.localtime(st['until'])))
        try:
            nw, nx = engine.now_next(eEPGCache.getInstance(), ref)
            if nw:
                lines.append(u'')
                lines.append(u'الآن: %s %s' % (time.strftime('%H:%M', time.localtime(nw[1])), nw[0]))
            if nx:
                lines.append(u'التالي: %s %s' % (time.strftime('%H:%M', time.localtime(nx[1])), nx[0]))
        except Exception:
            pass
        self['details'].setText(T(u'\n'.join(lines)))

    def cur(self):
        c = self['list'].getCurrent()
        return c and c[1]

    def next_filter(self):
        self.filter = (self.filter + 1) % len(self.FILTERS)
        c = self.cur()
        self.build(c and c[0])

    def pick(self, query=None):
        c = self.cur()
        if not c:
            return
        ref, name = c[0], c[1]
        cfg = current_cfg()
        enabled = list(cfg['sources']) + ['xml']
        items = []
        if c[2] and c[2] != '-' and not c[4]:
            items.append((T(u'>> تأكيد الربط الحالي: %s' % engine.display_key(c[2])), c[2]))
        items += [(T(u'>> بحث بالاسم...'), 'search'), (T(u'>> ربط تلقائي ذكي'), 'auto'),
                 (T(u'>> تعطيل البلوجين لهذه القناة'), '-')]
        sats = list(engine.user_satellites(self.services))
        m = engine.matcher(sats)
        for k, sc in m.match_all(name, engine._allow_for(enabled, engine.sat_of_ref(ref)), min_score=0.6):
            items.append((T(u'مقترح %d%%: %s' % (sc * 100, engine.display_key(k))), k))
        if query:
            q = engine.norm(query)
            pool = list(engine.catalog()) + engine.cloud_xml_entries(sats)
            hits = 0
            for x in pool:
                if x['src'] not in enabled or not any(q in engine.norm(n) for n in x['names']):
                    continue
                key = '%s:%s' % (x['src'], x['id'])
                items.append((T(engine.display_key(key)), key))
                hits += 1
                if hits > 300:
                    break
        else:
            for x in sorted(engine.catalog(), key=lambda x: (engine.SOURCE_ORDER.index(x['src'])
                                                              if x['src'] in engine.SOURCE_ORDER else 99,
                                                              x['names'][0].lower())):
                if x['src'] in enabled:
                    key = '%s:%s' % (x['src'], x['id'])
                    items.append((T(engine.display_key(key)), key))
        if 'xmltv' in enabled:
            items.append((T(u'>> XMLTV: إدخال معرف القناة يدويا'), 'xmltvid'))
        self.session.openWithCallback(lambda r: self.picked(ref, r), ChoiceBox,
                                      title=T(u'اختر مصدر EPG لقناة: %s' % name), list=items)

    def picked(self, ref, res):
        if not res:
            return
        v = res[1]
        if v in ('search', 'xmltvid'):
            from Screens.VirtualKeyBoard import VirtualKeyBoard
            if v == 'search':
                self.session.openWithCallback(lambda t: t and self.pick(u(t)), VirtualKeyBoard,
                                              title=T(u'اكتب جزءا من اسم القناة'), text='')
            else:
                self.session.openWithCallback(lambda t: t and self.picked(ref, (None, 'xmltv:' + u(t).strip())),
                                              VirtualKeyBoard, title=T(u'معرف القناة في ملف XMLTV'), text='')
            return
        m = engine.load_mapping()
        name, old = '', None
        for r, n in self.services:
            if r == ref:
                name = n
                break
        cands = self.auto.get(ref) or []
        old = cands[0][0] if cands else None
        if v == 'auto':
            m.pop(ref, None)
        else:
            m[ref] = v
            if v != '-':
                engine.learn(name, v, ref)     # same name/service on other satellites/feeds follows
                if old and old != v:
                    engine.reject(name, old)   # never suggest the wrong one again
        engine.save_mapping(m)
        self.build(ref)

    def disable(self):
        c = self.cur()
        if not c:
            return
        m = engine.load_mapping()
        if m.get(c[0]) == '-':
            m.pop(c[0], None)
        else:
            m[c[0]] = '-'
        engine.save_mapping(m)
        self.build(c[0])

    def update_now(self):
        self.session.openWithCallback(self._update_choice, ChoiceBox, title=T(u'تحديث'), list=[
            (T(u'تحديث هذه القناة فقط'), 'one'), (T(u'تحديث كل القنوات المربوطة'), 'all')])

    def _update_choice(self, res):
        if not res:
            return
        c = self.cur()
        refs = set([c[0]]) if (res[1] == 'one' and c) else None
        if start_update(refs, target='all'):
            self.session.openWithCallback(lambda *x: self.loaded(self.auto), UpdateScreen)

    def preview(self):
        c = self.cur()
        if not c:
            return
        key = c[2]
        if not key or key == '-':
            self.session.open(MessageBox, T(u'هذه القناة غير مربوطة بأي مصدر. اضغط OK لاختيار مصدر.'),
                              MessageBox.TYPE_INFO, timeout=5)
            return
        self.session.open(TextScreen, u'%s  <<  %s' % (c[1], engine.display_key(key)), lambda: preview_text(key))


def preview_text(key):
    evs = engine.fetch_preview(key, current_cfg())
    now = time.time()
    lines, day = [], None
    for e in evs:
        if e[0] + e[1] < now:
            continue
        lt = time.localtime(e[0])
        d = time.strftime('%d-%m-%Y', lt)
        if d != day:
            day = d
            lines.append(u'\n===== %s =====' % d)
        lines.append(u'%s  (%d د)  %s%s' % (time.strftime('%H:%M', lt), e[1] // 60, e[2],
                                            (u'  - ' + e[3]) if e[3] else u''))
    return u'\n'.join(lines) if lines else u'لم يتم العثور على برامج لهذه القناة.\nجرب "اختبار الاتصال بالمصادر".'


# --------------------------------------------------------------------- now / next
class NowNextScreen(BaseScreen):
    def __init__(self, session):
        self.skin = skin(1240, 700, (
            '<widget name="list" position="%d,%d" size="%d,%d" itemHeight="%d" font="Regular;%d" '
            'scrollbarMode="showOnDemand" foregroundColor="%s" backgroundColor="%s" transparent="1" />'
            '<widget name="info" position="%d,%d" size="%d,%d" font="Regular;%d" halign="right" '
            'foregroundColor="%s" backgroundColor="%s" transparent="1" />') % (
            _s(10), _s(62), _s(1220), _s(540), _s(30), _s(19), TXT, BG,
            _s(10), _s(606), _s(1220), _s(30), _s(16), DIM, BG) + buttons(642, 1240))
        BaseScreen.__init__(self, session, u'الآن وبعده')
        self['list'] = MenuList([])
        self['info'] = Label('')
        set_buttons(self, u'', u'مشاهدة القناة', u'', u'تحديث القائمة')
        self['actions'] = ActionMap(['OkCancelActions', 'ColorActions'], {
            'ok': self.zap, 'green': self.zap, 'cancel': self.close, 'yellow': self.toggle, 'blue': self.build}, -1)
        self.only_plugin = True
        self.onLayoutFinish.append(self.build)

    def toggle(self):
        self.only_plugin = not self.only_plugin
        self.build()

    def build(self):
        epg = eEPGCache.getInstance()
        filled = engine.load_state()['filled']
        now = time.time()
        rows = []
        for ref, name in engine.get_services():
            if self.only_plugin and ref not in filled:
                continue
            nw, nx = engine.now_next(epg, ref)
            if not nw:
                continue
            pct = int(max(0, min(100, (now - nw[1]) * 100.0 / max(1, nw[2]))))
            txt = u'%-22s  %s  %s (%d%%)' % (name[:22], time.strftime('%H:%M', time.localtime(nw[1])), nw[0][:42], pct)
            if nx:
                txt += u'   >>  %s %s' % (time.strftime('%H:%M', time.localtime(nx[1])), nx[0][:30])
            rows.append((T(txt), ref))
        self['list'].setList(rows)
        self['key_yellow'].setText(T(u'عرض: كل القنوات' if self.only_plugin else u'عرض: قنوات البلوجين'))
        self['info'].setText(T(u'%d قناة - OK لمشاهدة القناة' % len(rows)))

    def zap(self):
        c = self['list'].getCurrent()
        if c:
            try:
                self.session.nav.playService(eServiceReference(T(c[1])))
            except Exception:
                pass
            self.close()


# --------------------------------------------------------------------- bouquet statistics
class StatsScreen(BaseScreen):
    def __init__(self, session):
        self.skin = skin(1180, 680, (
            '<widget name="list" position="%d,%d" size="%d,%d" itemHeight="%d" font="Regular;%d" '
            'scrollbarMode="showOnDemand" foregroundColor="%s" backgroundColor="%s" transparent="1" />'
            '<widget name="info" position="%d,%d" size="%d,%d" font="Regular;%d" halign="right" '
            'foregroundColor="%s" backgroundColor="%s" transparent="1" />') % (
            _s(10), _s(62), _s(1160), _s(500), _s(32), _s(20), TXT, BG,
            _s(10), _s(570), _s(1160), _s(100), _s(17), DIM, BG))
        BaseScreen.__init__(self, session, u'إحصائيات الباقات')
        self['list'] = MenuList([])
        self['info'] = Label(T(u'جاري الحساب...'))
        self['actions'] = ActionMap(['OkCancelActions'], {'ok': self.close, 'cancel': self.close}, -1)
        self.onLayoutFinish.append(self.build)

    def build(self):
        bouquets = engine.get_bouquets()
        state = engine.load_state()
        auto = engine._load(engine.AUTO_FILE, {})
        auto = dict((k, [tuple(x) for x in v[1]]) for k, v in auto.items() if k != '_stamp')
        mapping = engine.load_mapping()
        epg = eEPGCache.getInstance()
        rich = {}
        for _b, items in bouquets:
            for r, _n in items:
                if r not in rich:
                    rich[r] = engine.epg_count(epg, r) >= 4
        rows = engine.bouquet_stats(bouquets, state, auto, mapping, rich)
        out = []
        tot = {'channels': 0, 'linked': 0, 'updated': 0, 'events': 0, 'native': 0}
        for b in rows:
            for k in tot:
                tot[k] += b[k] or 0
            out.append((T(u'%-28s  قنوات %4d | مربوطة %4d | حدّثها البلوجين %4d | برامج %6d | دليل القمر %4d' % (
                b['name'][:28], b['channels'], b['linked'] or 0, b['updated'], b['events'], b['native'] or 0)), b['name']))
        self['list'].setList(out)
        last = state.get('last')
        srcs = {}
        for f in state.get('filled', {}).values():
            s = engine.display_key(f.get('key', '')).split(':')[0]
            srcs[s] = srcs.get(s, 0) + 1
        self['info'].setText(T(
            u'الإجمالي: %d قناة، مربوطة %d، حدّثها البلوجين %d، %d برنامج%s\n'
            u'حسب المصدر: %s' % (
                tot['channels'], tot['linked'], tot['updated'], tot['events'],
                (u' - آخر تحديث %s' % time.strftime('%d/%m %H:%M', time.localtime(last))) if last else u'',
                u'، '.join(u'%s %d' % kv for kv in sorted(srcs.items(), key=lambda kv: -kv[1])[:8]))))


def import_text():
    files = engine.refresh_local(set(), force=True)
    lines = [u'ضع ملفات XMLTV (.xml أو .xml.gz) في أحد هذه المجلدات، ثم اختر "تحديث دليل البرامج الآن".',
             u'البلوجين يقرأ أسماء القنوات فيها ويربطها تلقائيا بقنوات باقاتك:', u'']
    for d in engine.IMPORT_DIRS:
        lines.append(u'  %s %s' % (d, u'(موجود)' if os.path.isdir(d) else u''))
    lines.append(u'')
    lines.append(u'المصادر المباشرة والملفات المستوردة الحالية:')
    for k, f in sorted(files.items()):
        lines.append(u'  %s: %d قناة' % (f.get('title', k), len(f.get('channels') or [])))
    try:
        os.makedirs(engine.IMPORT_DIRS[-1])
    except OSError:
        pass
    return u'\n'.join(lines)


# --------------------------------------------------------------------- settings
class SettingsScreen(ConfigListScreen, BaseScreen):
    def __init__(self, session):
        self.skin = skin(1080, 680, '<widget name="config" position="%d,%d" size="%d,%d" itemHeight="%d" '
                                    'font="Regular;%d" scrollbarMode="showOnDemand" foregroundColor="%s" '
                                    'backgroundColor="%s" transparent="1" />' % (
                                        _s(20), _s(64), _s(1040), _s(560), _s(34), _s(20), TXT, BG)
                         + buttons(632, 1080))
        BaseScreen.__init__(self, session, u'إعدادات ArabEPG')
        lst = [
            getConfigListEntry(T(u'السيرفر السحابي (GitHub) - مصدر رئيسي سريع'), C.cloud),
            getConfigListEntry(T(u'رابط السحابة'), C.cloud_url),
            getConfigListEntry(T(u'القنوات التي يتم تحديثها'), C.target),
            getConfigListEntry(T(u'عدد الأيام'), C.days),
            getConfigListEntry(T(u'تحديث تلقائي يومي'), C.autoupdate),
            getConfigListEntry(T(u'وقت التحديث اليومي'), C.update_time),
            getConfigListEntry(T(u'تحديث ذكي عند تشغيل الريسيفر'), C.boot_update),
            getConfigListEntry(T(u'التنبيه بتحديثات البلوجين'), C.check_updates),
            getConfigListEntry(T(u'لغة الدليل للقنوات العربية'), C.lang_mode),
            getConfigListEntry(T(u'خدمة الترجمة'), C.tr_provider),
            getConfigListEntry(T(u'إيميل MyMemory (يرفع الحد اليومي إلى 50 ألف حرف)'), C.tr_email),
            getConfigListEntry(T(u'رابط سيرفر LibreTranslate'), C.tr_url),
            getConfigListEntry(T(u'مفتاح LibreTranslate'), C.tr_key),
            getConfigListEntry(T(u'أقصى عدد عناوين تُترجم في كل تحديث'), C.tr_limit),
            getConfigListEntry(T(u'نسخ الدليل من نسخة أخرى لنفس القناة في نفس الباقة'), C.copy_epg),
            getConfigListEntry(T(u'تجهيز الدليل عند فتح قناة بدون EPG'), C.zap_epg),
            getConfigListEntry(T(u'إظهار رسالة عند تجهيز دليل القناة'), C.zap_notify),
            getConfigListEntry(T(u'دقة الربط الذكي'), C.match_level2),
            getConfigListEntry(T(u'التحقق من الربط بمقارنة دليل القمر'), C.verify),
            getConfigListEntry(T(u'مصدر شاهد (MBC والعربية...)'), C.src_shahid),
            getConfigListEntry(T(u'مصدر OSN'), C.src_osn),
            getConfigListEntry(T(u'مصدر beIN والكأس'), C.src_bein),
            getConfigListEntry(T(u'مصدر روتانا'), C.src_rotana),
            getConfigListEntry(T(u'مصدر الجزيرة'), C.src_aljazeera),
            getConfigListEntry(T(u'مصدر السينما.كوم (مصر والعرب)'), C.src_elcinema),
            getConfigListEntry(T(u'مصدر beIN SPORTS (beinsports.com)'), C.src_beinsports),
            getConfigListEntry(T(u'مصدر رؤيا'), C.src_roya),
            getConfigListEntry(T(u'مصدر ART (أفلام، حكايات، سينما)'), C.src_art),
            getConfigListEntry(T(u'مصدر تلفزيون عمان'), C.src_ayn),
            getConfigListEntry(T(u'مصدر القنوات المغربية SNRT (الأولى، الرياضية، المغربية...)'), C.src_snrt),
            getConfigListEntry(T(u'ملفات XMLTV إضافية'), C.src_xmltv),
            getConfigListEntry(T(u'روابط XMLTV (مفصولة بفاصلة)'), C.xmltv_urls),
            getConfigListEntry(T(u'لغة أسماء البرامج'), C.lang),
            getConfigListEntry(T(u'دولة شاهد'), C.country),
            getConfigListEntry(T(u'توقيت مواعيد السينما.كوم'), C.elcinema_tz),
        ]
        ConfigListScreen.__init__(self, lst, session=session)
        set_buttons(self, u'إلغاء', u'حفظ')
        self['setupActions'] = ActionMap(['SetupActions', 'ColorActions'], {
            'green': self.save, 'save': self.save, 'red': self.cancel, 'cancel': self.cancel, 'ok': self.ok}, -2)

    def ok(self):
        cur = self['config'].getCurrent()
        if cur and cur[1] in (C.xmltv_urls, C.cloud_url, C.tr_email, C.tr_url, C.tr_key):
            from Screens.VirtualKeyBoard import VirtualKeyBoard
            item = cur[1]
            self.session.openWithCallback(lambda t: self._text(item, t), VirtualKeyBoard, title=cur[0],
                                          text=item.value)

    def _text(self, item, txt):
        if txt is not None:
            item.value = txt
            self['config'].invalidateCurrent()

    def save(self):
        for x in self['config'].list:
            x[1].save()
        configfile.save()
        if AutoUpdate.instance:
            AutoUpdate.instance.schedule()
        self.close(True)

    def cancel(self):
        for x in self['config'].list:
            x[1].cancel()
        self.close(False)


# --------------------------------------------------------------------- automatic updates
class AutoUpdate(object):
    instance = None

    def __init__(self, session):
        AutoUpdate.instance = self
        self.session = session
        self.new_version = None
        self.timer = make_timer(self.fire)
        self.vtimer = make_timer(self.check_version)
        self.schedule(boot=True)
        if C.check_updates.value:
            self.vtimer.startLongTimer(300)
        # guide on demand: when a channel without EPG is opened, prepare it in the background
        self.zap_timer = make_timer(self.check_zap)
        self.zap_seen = {}
        self.zap_job = None
        self.zap_watch = make_timer(self.zap_finished)
        try:
            from enigma import iPlayableService
            self.ev_start = iPlayableService.evStart
            session.nav.event.append(self.on_service_event)
        except Exception as e:
            print('[ArabEPG] zap watcher not available', e)

    def on_service_event(self, ev):
        if ev == self.ev_start and C.zap_epg.value:
            self.zap_timer.stop()
            self.zap_timer.start(6000, True)   # give the satellite guide a few seconds first

    def check_zap(self):
        try:
            sref = self.session.nav.getCurrentlyPlayingServiceReference()
            if not sref:
                return
            ref = engine.clean_ref(sref.toString())
            if not ref or not ref.startswith('1:0:'):
                return
            r = engine.Updater.running
            if r and not r.done:
                return
            if time.time() - self.zap_seen.get(ref, 0) < 6 * 3600:
                return
            epg = eEPGCache.getInstance()
            if engine.epg_count(epg, ref, 6) > 0:
                return
            from enigma import eServiceCenter
            info = eServiceCenter.getInstance().info(sref)
            name = u(info and info.getName(sref) or '').replace(u'\x86', u'').replace(u'\x87', u'')
            self.zap_seen[ref] = time.time()
            known = engine.resolve(ref, engine.load_mapping(),
                                   dict((k, [tuple(x) for x in v[1]]) for k, v in
                                        engine._load(engine.AUTO_FILE, {}).items() if k != '_stamp'))[0]
            linked = bool(known and known[0][0] != '-') or bool(engine.linked_refs().get(engine.ref_key(ref) or '')) \
                or bool(engine.load_refs().get(engine.ref_key(ref) or ''))
            if known and known[0][0] == '-':
                return
            job = engine.Updater(current_cfg(target='all', smart=False), set([ref]), [(ref, name)], quiet=True)
            if job.start():
                self.zap_job, self.zap_name = job, name
                log(u'تجهيز دليل القناة المفتوحة: %s' % name)
                if linked and C.zap_notify.value:
                    self._popup(u'ArabEPG: جاري تجهيز دليل %s' % name, 4)
                self.zap_watch.start(2000, False)
        except Exception as e:
            print('[ArabEPG] zap check error', e)

    def zap_finished(self):
        j = self.zap_job
        if not j or not j.done:
            return
        self.zap_watch.stop()
        self.zap_job = None
        if j.stats.get('events') and C.zap_notify.value:
            self._popup(u'ArabEPG: تم تجهيز دليل %s (%d برنامج)' % (self.zap_name, j.stats['events']), 4)

    def _popup(self, text, timeout):
        try:
            from Tools import Notifications
            Notifications.AddPopup(T(text), MessageBox.TYPE_INFO, timeout, 'ArabEPGZap')
        except Exception:
            pass

    def schedule(self, boot=False):
        self.timer.stop()
        now = time.time()
        self.boot = False
        if boot and C.boot_update.value:
            self.boot = True
            self.timer.startLongTimer(120)
            return
        if not C.autoupdate.value:
            return
        h, m = C.update_time.value
        lt = time.localtime(now)
        t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))
        if t <= now + 30:
            t += 86400
        self.timer.startLongTimer(int(t - now))

    def fire(self):
        if time.localtime().tm_year < 2020:  # clock not synced yet
            self.timer.startLongTimer(60)
            return
        log(u'بدء التحديث التلقائي%s' % (u' الذكي (بعد التشغيل)' if self.boot else u''))
        start_update(smart=self.boot)
        self.schedule()

    def check_version(self):
        def done(info):
            if isinstance(info, dict):
                self.new_version = info
                try:
                    from Tools import Notifications
                    Notifications.AddPopup(T(u'ArabEPG: يوجد إصدار جديد %s - افتح البلوجين ثم "التحقق من تحديث البلوجين"'
                                             % info.get('version')), MessageBox.TYPE_INFO, 15, 'ArabEPGUpdate')
                except Exception:
                    pass
        self._vjob = BackgroundJob(lambda: engine.check_plugin_update(VERSION), done)
        self.vtimer.startLongTimer(24 * 3600)


# --------------------------------------------------------------------- entry points
def main(session, **kwargs):
    session.open(ArabEPGMain)


def nownext(session, **kwargs):
    session.open(NowNextScreen)


def sessionstart(reason, session=None, **kwargs):
    if reason == 0 and session is not None and AutoUpdate.instance is None:
        try:
            AutoUpdate(session)
        except Exception as e:
            print('[ArabEPG] autostart error', e)


def Plugins(**kwargs):
    name = 'ArabEPG'
    desc = T(u'دليل برامج القنوات العربية والأوروبية من الإنترنت')
    return [
        PluginDescriptor(name=name, description=desc, where=PluginDescriptor.WHERE_PLUGINMENU,
                         icon='plugin.png', fnc=main),
        PluginDescriptor(name=name, description=desc, where=PluginDescriptor.WHERE_EXTENSIONSMENU, fnc=main),
        PluginDescriptor(name=T(u'ArabEPG - الآن وبعده'), description=desc,
                         where=PluginDescriptor.WHERE_EVENTINFO, fnc=nownext),
        PluginDescriptor(where=PluginDescriptor.WHERE_SESSIONSTART, fnc=sessionstart),
    ]
