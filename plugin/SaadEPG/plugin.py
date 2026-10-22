# -*- coding: utf-8 -*-
# Saad EPG - online EPG for Arabic and European satellite channels
# Enigma2 plugin, Python 2 and Python 3 images (OpenATV, OpenPLi, OpenBH, Egami, VTi ...)
from __future__ import absolute_import, print_function

import json
import os
import re
import threading
import time

from enigma import eTimer, eEPGCache, eServiceReference
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

from . import engine, sources, ui
from .sources import log, u, PY3

VERSION = '1.7'

# --------------------------------------------------------------------- config
config.plugins.saadepg = ConfigSubsection()
C = config.plugins.saadepg
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
C.src_lbci = ConfigYesNo(default=True)
C.src_tunisia = ConfigYesNo(default=True)
C.src_trt = ConfigYesNo(default=True)
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
C.autoupdate2 = ConfigYesNo(default=True)
C.update_time2 = ConfigClock(default=int(time.mktime((2000, 1, 1, 17, 20, 0, 0, 0, -1))))
C.stages = ConfigSelection(default='3', choices=[('1', 'مرة واحدة'), ('2', 'مرحلتين'), ('3', '3 مراحل'),
                                                 ('4', '4 مراحل')])
C.stage_gap = ConfigSelection(default='20', choices=[('10', '10 دقائق'), ('20', '20 دقيقة'), ('30', '30 دقيقة'),
                                                     ('60', 'ساعة')])
C.check_updates = ConfigYesNo(default=True)
C.match_level2 = ConfigSelection(default='90', choices=[('95', 'دقيق جدا'), ('90', 'متوازن'), ('85', 'مرن')])
C.verify = ConfigYesNo(default=True)
C.link_mode = ConfigSelection(default='normal', choices=[
    ('safe', 'أمان عالٍ: المؤكد فقط'), ('normal', 'متوازن (موصى به)'), ('all', 'كل الربط (بدون تحفظ)')])
C.lang_mode = ConfigSelection(default='ar_only', choices=[
    ('ar_only', 'عربي فقط (من مصادر عربية موثوقة)'), ('translate', 'عربي، وإن لم يوجد فالإنجليزي مع العربي المتاح'),
    ('ar_first', 'عربي أولا ثم إنجليزي'), ('any', 'أي لغة')])
C.tr_provider = ConfigSelection(default='mymemory', choices=[('cloud', 'السحابة فقط (بلا حدود)'),
                                                            ('mymemory', 'السحابة ثم MyMemory'),
                                                            ('libre', 'السحابة ثم LibreTranslate (سيرفر خاص)')])
C.tr_email = ConfigText(default='', fixed_size=False)
C.tr_url = ConfigText(default='', fixed_size=False)
C.tr_key = ConfigText(default='', fixed_size=False)
C.tr_limit = ConfigSelection(default='1500', choices=[('500', '500'), ('1500', '1500'), ('3000', '3000')])
C.zap_epg = ConfigYesNo(default=True)
C.zap_notify = ConfigYesNo(default=True)
C.migrated = ConfigYesNo(default=False)
C.bouquets = ConfigText(default='', fixed_size=False)   # names joined by | (empty = all bouquets)
C.copy_epg = ConfigYesNo(default=True)
C.auto_backup = ConfigYesNo(default=True)
C.ar_tr_mode = ConfigSelection(default='desc', choices=[
    ('desc', 'الوصف فقط - العنوان كما هو (أو العربي الرسمي)'),
    ('all', 'العنوان والوصف (العنوان الأصلي يظهر في التفاصيل)'), ('none', 'بدون ترجمة')])
C.lock_epg = ConfigYesNo(default=True)
C.gold = ConfigYesNo(default=True)
C.foreign_lang = ConfigSelection(default='en', choices=[
    ('en', 'الإنجليزية (ترجمة من السحابة)'), ('orig', 'لغة القناة الأصلية')])

SRC_CFG = (('shahid', C.src_shahid), ('osn', C.src_osn), ('bein', C.src_bein), ('rotana', C.src_rotana),
           ('aljazeera', C.src_aljazeera), ('elcinema', C.src_elcinema), ('beinsports', C.src_beinsports),
           ('roya', C.src_roya), ('art', C.src_art), ('ayn', C.src_ayn), ('snrt', C.src_snrt), ('lbci', C.src_lbci), ('tunisia', C.src_tunisia), ('trt', C.src_trt),
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


def _migrate_old_settings():
    """settings saved under the old name (config.plugins.arabepg.*) are taken over once."""
    try:
        if C.migrated.value:
            return
        with open('/etc/enigma2/settings') as f:
            lines = f.read().splitlines()
        n = 0
        for line in lines:
            if line.startswith('config.plugins.arabepg.') and '=' in line:
                k, v = line[len('config.plugins.arabepg.'):].split('=', 1)
                item = getattr(C, k, None)
                if item is not None and k != 'migrated':
                    try:
                        item.saved_value = v
                        item.load()
                        item.save()
                        n += 1
                    except Exception:
                        pass
        C.migrated.value = True
        C.migrated.save()
        configfile.save()
        if n:
            log(u'تم نقل %d إعداد من الاسم القديم ArabEPG' % n)
    except Exception:
        pass


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
        'tr_key': C.tr_key.value.strip(), 'tr_limit': int(C.tr_limit.value), 'copy_epg': C.copy_epg.value, 'link_mode': C.link_mode.value,
        'bouquets': [x for x in C.bouquets.value.split('|') if x], 'foreign_lang': C.foreign_lang.value,
        'ar_tr_mode': C.ar_tr_mode.value, 'lock': C.lock_epg.value, 'gold': C.gold.value,
    }
    cfg.update(over)
    sources.XML_PREFER_EN = cfg['foreign_lang'] == 'en'
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
_s = ui.S
BG, PANEL, ACCENT, TXT, DIM, CELL = ui.BG, ui.PANEL, ui.ACCENT, ui.TXT, ui.DIM, ui.CELL
Col, Table = ui.Col, ui.Table


def skin(w, h, body, title='Saad EPG'):
    return ui.frame(w, h, body, title)


def buttons(y, w):
    return ui.buttons(y, w)


def text_panel(x, y, w, h, size=19, name='text'):
    """a scrolling text inside a soft box"""
    return ui.box(x, y, w, h, PANEL) + (
        '<widget name="%s" position="%d,%d" size="%d,%d" font="Regular;%d" foregroundColor="%s" '
        'backgroundColor="%s" transparent="1" zPosition="1" />') % (
        name, _s(x + 14), _s(y + 10), _s(w - 28), _s(h - 20), _s(size), TXT, PANEL)


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
MAIN_ITEMS = [
    (u'تحديث دليل البرامج الآن', 'update', u'جلب الدليل من كل المصادر وحفظه'),
    (u'القنوات والربط', 'channels', u'مصدر دليل كل قناة حسب الباقات'),
    (u'الآن وبعده', 'nownext', u'البرنامج الحالي والتالي لكل القنوات'),
    (u'صحة الدليل', 'health', u'المصادر والقنوات المرفوضة والتنبيهات'),
    (u'الإعدادات', 'settings', u'المصادر واللغات ومواعيد التحديث'),
    (u'أدوات متقدمة', 'advanced', u'الباقات، الإحصائيات، النسخ الاحتياطي...'),
]
ADVANCED_ITEMS = [
    (u'اختيار الباقات التي يحدّثها البلوجين', 'bouquets', u'الباقات المفضلة أولا'),
    (u'إحصائيات الباقات وعدد البرامج', 'stats', u'لكل باقة: قنوات ومربوطة وبرامج'),
    (u'حفظ الدليل على الهارد (epg.dat)', 'epgfile', u'يبقى الدليل بعد إعادة التشغيل'),
    (u'إعادة الربط الذكي لكل القنوات', 'relink', u'ربط من جديد بدون الربط اليدوي'),
    (u'قنوات دليلها غير عربي', 'nonar', u'قنوات عربية لم يوجد لها دليل عربي'),
    (u'طريقة الربط اليدوي والحماية', 'help', u'شرح خطوة بخطوة'),
    (u'مسح دليل البلوجين وإعادة تحميله', 'clear', u'بداية نظيفة (الربط اليدوي يبقى)'),
    (u'اختبار الاتصال بالمصادر والسحابة', 'test', u'هل كل مصدر يعمل الآن؟'),
    (u'استيراد ملفات EPG (XMLTV)', 'import', u'ملفات تضعها على الهارد'),
    (u'تصدير الربط المؤكد', 'export', u'لمشاركته عبر السحابة'),
    (u'نسخ احتياطي الآن', 'backup', u'الإعدادات والربط اليدوي على الهارد'),
    (u'استرجاع النسخة الاحتياطية', 'restore', u'من الهارد أو الفلاشة'),
    (u'مسح ذاكرة الترجمة القديمة', 'trclear', u'ترجمة MyMemory المحفوظة'),
    (u'سجل العمليات', 'log', u'/tmp/SaadEPG.log'),
    (u'التحقق من تحديث البلوجين', 'selfupdate', u'تثبيت الإصدار الجديد'),
    (u'حذف الربط اليدوي لكل القنوات', 'reset', u'العودة للربط التلقائي'),
]


def menu_table(width):
    return Table(width, [Col(u'', width - 380 - 10, 'right'), Col(u'', 370, 'right', 2, 'dim')], row_h=50, font=21,
                 small=16)


class MenuScreen(BaseScreen):
    """a list of actions: title on the right, a short explanation on the left. closes with the chosen key."""

    def __init__(self, session, title, items):
        n = min(len(items), 11)
        h = 76 + n * 50 + 20
        self.skin = skin(900, h, ui.list_widget('list', 14, 70, 872, n * 50 + 4))
        BaseScreen.__init__(self, session, title)
        self.table = menu_table(860)
        self['list'] = self.table.widget()
        self['list'].setList([self.table.row(k, [t, d], i) for i, (t, k, d) in enumerate(items)])
        self['actions'] = ActionMap(['OkCancelActions'], {'ok': self.ok, 'cancel': lambda: self.close(None)}, -1)

    def ok(self):
        self.close(self['list'].payload())


class SaadEPGMain(BaseScreen):
    CARDS = (u'آخر تحديث', u'قنوات حدّثها البلوجين', u'برامج', u'آخر تجميع للسحابة')

    def __init__(self, session):
        self.skin = skin(900, 610, (
            ui.list_widget('list', 14, 70, 872, 6 * 50 + 4) +
            ui.stat_cards('card', 4, 20, 386, 860) +
            ui.box(20, 466, 860, 130) +
            ui.label('status', 34, 474, 832, 116, 18, DIM, PANEL)))
        BaseScreen.__init__(self, session, u'Saad EPG  %s' % VERSION)
        self.menu_items = MAIN_ITEMS
        self.advanced_items = ADVANCED_ITEMS
        self.table = menu_table(860)
        self['list'] = self.table.widget()
        self['list'].setList([self.table.row(k, [t, d], i) for i, (t, k, d) in enumerate(self.menu_items)])
        for i, t in enumerate(self.CARDS):
            self['card%d' % i] = Label('-')
            self['card%dt' % i] = Label(T(t))
        self['status'] = Label('')
        self['actions'] = ActionMap(['OkCancelActions'], {'ok': self.ok, 'cancel': self.close}, -1)
        self.onShown.append(self.refresh)

    def refresh(self, *args):
        st = engine.load_state()
        s = st.get('last_stats', {})
        idx = engine.load_index()
        self['card0'].setText(time.strftime('%H:%M', time.localtime(st['last'])) if st.get('last') else '-')
        self['card0t'].setText(T(time.strftime(u'آخر تحديث %d/%m', time.localtime(st['last'])) if st.get('last')
                                 else u'آخر تحديث'))
        self['card1'].setText(str(s.get('channels', 0)))
        self['card2'].setText(str(s.get('events', 0)))
        self['card3'].setText(time.strftime('%H:%M', time.localtime(idx['generated'])) if idx.get('generated') else '-')
        lines = []
        r = engine.Updater.running
        if r and not r.done:
            lines.append(u'جاري التحديث في الخلفية: %d%% - %s' % (r.percent(), r.progress))
        elif not st.get('last'):
            lines.append(u'لم يتم التحديث بعد - اختر "تحديث دليل البرامج الآن"، الربط يتم تلقائيا.')
        times = [u'%02d:%02d' % tuple(c.value) for on, c in ((C.autoupdate, C.update_time), (C.autoupdate2, C.update_time2))
                 if on.value]
        if times:
            lines.append(u'التحديث التلقائي: %s  (%s)' % (u' و '.join(times), u(C.stages.getText())))
        b = engine.backup_info()
        if b:
            lines.append(u'آخر نسخة احتياطية: %s  (%s)' % (time.strftime('%d/%m/%Y', time.localtime(b[1])), b[0]))
        if AutoUpdate.instance and AutoUpdate.instance.new_version:
            lines.append(u'يوجد إصدار جديد من البلوجين: %s' % AutoUpdate.instance.new_version.get('version'))
        self['status'].setText(T(u'\n'.join(lines)))

    def ok(self):
        sel = self['list'].payload()
        if sel:
            self.run(sel)

    def run(self, a):
        if a == 'advanced':
            self.session.openWithCallback(lambda r: r and self.run(r), MenuScreen, u'أدوات متقدمة', self.advanced_items)
        elif a == 'epgfile':
            self.session.open(TextScreen, u'حفظ الدليل', epg_file_text)
        elif a == 'update':
            start_update(manual=True)
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
        elif a == 'health':
            self.session.open(TextScreen, u'صحة الدليل', health_text)
        elif a == 'bouquets':
            self.session.openWithCallback(self.refresh, BouquetsScreen)
        elif a == 'backup':
            self.session.open(TextScreen, u'نسخ احتياطي', backup_text)
        elif a == 'restore':
            self.session.openWithCallback(lambda yes: yes and self.session.open(TextScreen, u'استرجاع', restore_text),
                                          MessageBox, T(u'استرجاع الإعدادات والربط اليدوي من النسخة الاحتياطية؟'),
                                          MessageBox.TYPE_YESNO)
        elif a == 'trclear':
            self.session.open(TextScreen, u'ذاكرة الترجمة', clear_tr_text)
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
- لاختيار لغة الدليل: الإعدادات ← لغة الدليل للقنوات العربية.

الحماية من الدليل الخطأ (تلقائية):
- كل ربط يُقارن بدليل القمر نفسه: عنوان البرنامج الحالي، ومواعيد بداية البرامج (تعمل مهما كانت اللغة).
  لو المواعيد لا تطابق، يُرفض الربط ولا يُستورد، ويُمسح أي دليل خطأ سابق.
- لو المواعيد متطابقة لكن بفرق ساعة أو ساعتين، يُصحَّح فرق التوقيت تلقائيا.
- القناة التي دليلها على القمر بلغة أوروبية لا تُربط بمصدر عربي، والعكس.
- الربط الذي لا يمكن التحقق منه ونسبة تطابق اسمه أقل من 92% لا يُستورد، ويظهر بعلامة [?]
  في "تحتاج مراجعة". اضغط عليه OK ثم "تأكيد الربط الحالي" لو المعاينة صحيحة.
- الإعدادات ← مستوى الأمان: "أمان عالٍ" يستورد المؤكد فقط، "كل الربط" يلغي هذا التحفظ.

دليل قناة أخرى (يدويا):
- لو قناة ليس لها مصدر، لكن قناة أخرى في باقاتك تعرض نفس البرامج ولها دليل (من القمر أو من البلوجين):
  القنوات ← اختر القناة ← OK ← "استخدام دليل قناة أخرى من باقاتك" ← اكتب جزءا من الاسم ← اختر القناة.
  يُنسخ دليلها في كل تحديث. للإلغاء: OK ← "ربط تلقائي ذكي".'''


def non_arabic_text():
    names = engine.load_state().get('non_arabic') or []
    if not names:
        return u'لا توجد قنوات عربية بدليل غير عربي في آخر تحديث.'
    return (u'هذه القنوات العربية لم يُعثر لها إلا على دليل غير عربي في آخر تحديث (%d قناة).\n'
            u'الحلول: اختر لها مصدرا آخر من "القنوات والربط الذكي"، أو من الإعدادات اختر لغة الدليل:\n'
            u'"عربي + ترجمة الإنجليزي إلى العربية" أو "عربي أولا ثم إنجليزي".\n\n' % len(names)) + u'\n'.join(names)


def receiver_time_text():
    """the receiver's clock and time zone: every guide is shown in this time"""
    try:
        now = time.time()
        dst = time.localtime(now).tm_isdst > 0
        off = -(time.altzone if dst else time.timezone) // 60
        tz = u(os.environ.get('TZ') or u'')
        if not tz:
            try:
                lt = os.path.realpath('/etc/localtime')
                tz = u(lt.split('/zoneinfo/', 1)[1]) if '/zoneinfo/' in lt else u''
            except Exception:
                tz = u''
        txt = u'ساعة الريسيفر: %s  (UTC%+d:%02d%s%s)' % (
            time.strftime('%H:%M %d/%m', time.localtime(now)), off // 60, abs(off) % 60,
            u'، توقيت صيفي' if dst else u'', (u'، ' + tz) if tz else u'')
        if C.country.value == 'EG' and off not in (120, 180):
            txt += u'\nتنبيه: ساعة الريسيفر ليست بتوقيت مصر - اضبط المنطقة الزمنية (القاهرة) من إعدادات الريسيفر'
        return txt
    except Exception as e:
        return u'ساعة الريسيفر: %s' % e


def health_text():
    st = engine.load_state()
    h = st.get('health') or {}
    s = st.get('last_stats') or {}
    out = []
    if st.get('last'):
        out.append(u'آخر تحديث: %s - %s قناة، %s برنامج' % (
            time.strftime('%d/%m %H:%M', time.localtime(st['last'])), s.get('channels', 0), s.get('events', 0)))
    idx = engine.load_index()
    if idx.get('generated'):
        out.append(u'آخر تجميع للسحابة: %s (%d ملف)' % (
            time.strftime('%d/%m %H:%M', time.localtime(idx['generated'])), len(idx.get('files') or {})))
    out.append(receiver_time_text())
    out.append(u'')
    out.append(u'المصادر (قنوات بها بيانات / المطلوبة):')
    for label, v in sorted((h.get('sources') or {}).items()):
        ok, total, empty = v[0], v[1], v[2]
        out.append(u'  %s: %d/%d%s' % (label, ok, total, (u'  - بدون بيانات: ' + u'، '.join(empty)) if empty else u''))
    out.append(u'')
    if C.lock_epg.value:
        ok = engine.LOCK.get('ok')
        out.append(u'قنوات دليلها مثبت (القمر لا يكتب فوقه): %d%s' % (
            len(engine.locked_refs()), u'' if ok is not False else u'  - هذه الصورة لا تدعم التثبيت'))
    else:
        out.append(u'تثبيت دليل البلوجين: متوقف من الإعدادات')
    out.append(u'رُفض لعدم مطابقة دليل القمر: %d' % h.get('rejected', 0))
    out.append(u'ربط غير مؤكد ينتظر مراجعتك [?]: %d' % h.get('held', 0))
    out.append(u'نُسخ الدليل من نسخة أخرى للقناة: %d' % h.get('copied', 0))
    out.append(u'برامج إنجليزية صار لها عربي (عنوان حقيقي أو وصف): %d' % h.get('translated', 0))
    out.append(u'قنوات أجنبية تُرجم دليل قمرها للإنجليزية: %d' % len(st.get('tr_native') or {}))
    conf = h.get('conflicts') or []
    out.append(u'قنوات رقم خدمتها مشترك مع قناة أخرى (لا يضاف لها دليل): %d' % len(conf))
    if conf:
        out.append(u'  ' + u'، '.join(conf[:40]))
    try:
        ep = config.misc.epgcache_filename.value
        out.append(u'ملف الدليل على الريسيفر: %s (%.1f ميجابايت)' % (ep, os.path.getsize(ep) / 1048576.0))
    except Exception:
        pass
    leaks = h.get('leaks') or {}
    out.append(u'قنوات كان دليلها يظهر على قنوات أخرى (موقوفة 30 يوما): %d' % len(leaks))
    names = dict(engine.get_services()) if leaks else {}
    for r, v in list(leaks.items())[:20]:
        out.append(u'  %s ← ظهر على: %s' % (names.get(r, r), u'، '.join(v.get('victims') or [])))
    return u'\n'.join(out)


def epg_file_text():
    """where the receiver keeps its guide: on the hard disk it survives restarts without wearing the flash."""
    out = []
    try:
        item = config.misc.epgcache_filename
        path = item.value
    except Exception:
        item, path = None, '/etc/enigma2/epg.dat'
    out.append(u'ملف الدليل الحالي: %s' % path)
    hdd = None
    for m in ('/media/hdd', '/media/usb', '/media/mmc'):
        if os.path.ismount(m) and os.access(m, os.W_OK):
            hdd = m
            break
    if item is not None and hdd and not path.startswith(hdd):
        new = os.path.join(hdd, 'epg.dat')
        try:
            item.value = new
            item.save()
            configfile.save()
            epg = eEPGCache.getInstance()
            if hasattr(epg, 'setCacheFile'):
                epg.setCacheFile(new)
            path = new
            out.append(u'تم نقل ملف الدليل إلى: %s' % new)
        except Exception as e:
            out.append(u'تعذر تغيير مكان الملف: %s' % e)
    elif not hdd:
        out.append(u'لا يوجد هارد أو فلاشة: يبقى الملف في مكانه.')
    try:
        eEPGCache.getInstance().save()
        out.append(u'تم حفظ الدليل الآن.')
    except Exception as e:
        out.append(u'تعذر الحفظ: %s' % e)
    try:
        out.append(u'حجم الملف: %.1f ميجابايت' % (os.path.getsize(path) / 1048576.0))
    except OSError:
        out.append(u'(الملف يُكتب خلال لحظات)')
    out.append(u'')
    out.append(u'البلوجين يحفظ الدليل بعد كل تحديث، والريسيفر يحمّله تلقائيا عند التشغيل.')
    return u'\n'.join(out)


def _settings_dict():
    out = {}
    for k, v in C.dict().items():
        try:
            val = v.value
            json.dumps(val)
            out[k] = val
        except Exception:
            pass
    return out


def backup_text():
    d = engine.backup_all(_settings_dict())
    if not d:
        return u'لا يوجد هارد أو فلاشة متصلة للنسخ الاحتياطي.'
    return u'تم حفظ الإعدادات والربط اليدوي وما تعلمه البلوجين في:\n%s\n\nبعد تفليش الريسيفر: ثبّت البلوجين ثم اختر "استرجاع".' % d


RESTORE_MARK = os.path.join(engine.DATA_DIR, '.restore_checked')


def _mark_restore_checked():
    try:
        if not os.path.isdir(engine.DATA_DIR):
            os.makedirs(engine.DATA_DIR)
        open(RESTORE_MARK, 'w').close()
    except Exception:
        pass


def auto_restore():
    """after a new install / flash: take the settings and links back from the hard disk by itself.
    -> message, None (nothing to do) or 'wait' (no backup found yet: the disk may still be mounting)"""
    if os.path.exists(RESTORE_MARK):
        return None
    if not engine.fresh_install():
        _mark_restore_checked()
        return None
    if not engine.backup_info():
        return 'wait'
    msg = restore_text()
    _mark_restore_checked()
    log(u'استعادة تلقائية بعد التثبيت: ' + msg.replace(u'\n', u' '))
    return msg


def restore_text():
    d, settings = engine.restore_all()
    if not d:
        return u'لم يتم العثور على نسخة احتياطية (SaadEPG_backup) على الهارد أو الفلاشة.'
    n = 0
    for k, val in (settings or {}).items():
        try:
            item = getattr(C, k)
            item.value = val
            item.save()
            n += 1
        except Exception:
            pass
    try:
        configfile.save()
    except Exception:
        pass
    return u'تم الاسترجاع من:\n%s\n%d إعداد + الربط اليدوي.\nشغّل "تحديث دليل البرامج الآن".' % (d, n)


def clear_tr_text():
    p = os.path.join(engine.cache_dir(), 'translations.json.gz')
    try:
        os.remove(p)
        return u'تم مسح ذاكرة الترجمة القديمة. الترجمة الآن من قاموس السحابة، وما ينقص يترجم من جديد.'
    except OSError:
        return u'ذاكرة الترجمة فارغة بالفعل.'


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
        self.skin = skin(900, 420, text_panel(16, 70, 868, 336, 20))
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
        self.skin = skin(1060, 660, text_panel(16, 70, 1028, 572, 20))
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
        self.skin = skin(1120, 680, text_panel(16, 70, 1088, 594, 18))
        BaseScreen.__init__(self, session, u'سجل العمليات  (/tmp/SaadEPG.log)')
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
STATUS_CHIP = {'A': (u'البلوجين', 'accent'), '+': (u'القمر', 'blue'), '?': (u'مراجعة', 'amber')}
KIND_CHIP = {u'رسمي': 'accent', u'عربي': 'blue', u'ملف': 'violet', u'منسوخ': 'amber', u'ذهبي': 'amber'}


def channels_table():
    return Table(790, [Col(u'الدليل', 92, 'center'), Col(u'القناة', 232), Col(u'المصدر', 290),
                       Col(u'الثقة', 72, 'center', 1), Col(u'النوع', 76, 'center')], row_h=38, font=18, small=15)


def channel_cells(epg, name, key, cands, manual, mapped):
    chip = STATUS_CHIP.get(epg)
    cells = [{'t': chip[0], 'chip': chip[1]} if chip else {'t': u'—', 'fg': 'dim'}, name]
    if key and key != '-':
        sc = cands[0][1] if cands else 0
        src = engine.display_key(key)
        src = src.split(u': ', 1)[-1] + u'  ·  ' + src.split(u': ', 1)[0] if u': ' in src else src
        kind = engine.source_kind(key)
        cells += [src, {'t': u'يدوي', 'fg': 'violet', 'font': 0} if manual else
                  {'t': u'%d%%' % int(sc * 100), 'fg': 'accent' if sc >= 0.95 else ('amber' if sc >= 0.88 else 'red')},
                  {'t': kind, 'chip': KIND_CHIP.get(kind, 'violet')} if kind else u'']
    else:
        txt = (u'دليل القمر مخفي' if mapped == '!' else u'معطل') if key == '-' else u'غير مربوطة - OK للاختيار'
        cells += [{'t': txt, 'fg': 'dim'}, u'', u'']
    return cells


class ChannelsScreen(BaseScreen):
    FILTERS = (('linked', u'المربوطة'), ('review', u'تحتاج مراجعة'), ('unlinked', u'غير المربوطة'),
               ('all', u'كل القنوات'))

    def __init__(self, session, relink=False):
        self.table = channels_table()
        self.skin = skin(1240, 700, (
            self.table.header(10, 66) + ui.list_widget('list', 10, 102, 800, 494) +
            ui.box(822, 66, 408, 530) +
            ui.box(822, 66, 408, 40, ui.HEAD) +
            ui.label('bouquet', 830, 66, 392, 40, 20, ui.hexc('amber'), ui.HEAD, 'center', 'center') +
            '<widget name="picon" zPosition="1" position="%d,%d" size="%d,%d" alphatest="blend" />' % (
                _s(926), _s(114), _s(200), _s(120)) +
            ui.label('details', 836, 240, 380, 350, 18, TXT, PANEL) +
            ui.label('info', 10, 602, 1220, 36, 16, DIM, BG, 'right', 'center')) + buttons(646, 1240))
        BaseScreen.__init__(self, session, u'الربط الذكي للقنوات' if relink else u'القنوات وربطها')
        self['list'] = self.table.widget()
        self['picon'] = Pixmap()
        self['bouquet'] = Label('')
        self['details'] = Label('')
        self['info'] = Label(T(u'جاري قراءة القنوات وربطها تلقائيا...'))
        set_buttons(self, u'تعطيل/تفعيل', u'تحديث', u'', u'معاينة البرامج')
        self['actions'] = ActionMap(['OkCancelActions', 'ColorActions', 'DirectionActions'], {
            'ok': self.pick, 'cancel': self.close, 'red': self.disable, 'green': self.update_now,
            'yellow': self.next_filter, 'blue': self.preview,
            'up': self.up, 'down': self.down,
            'left': lambda: self.step_bouquet(-1), 'right': lambda: self.step_bouquet(1)}, -1)
        # up/down: channels, left/right: bouquets, CH+/CH-: page, MENU: list of bouquets
        # (InfobarChannelSelection is not used: on many images it maps the up/down keys too -> double jumps)
        self['bq_actions'] = ActionMap(['MenuActions', 'ChannelSelectBaseActions'], {
            'menu': self.choose_bouquet, 'nextBouquet': self.pgup, 'prevBouquet': self.pgdown}, -2)
        self.bouquets, self.bq = [], -1
        self.relink = relink
        self.filter = 0
        self.services, self.auto, self.epg, self.held, self.conflicts = [], {}, {}, {}, set()
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
            bqs = engine.get_bouquets()
            chosen = [x for x in C.bouquets.value.split('|') if x]
            # your bouquets in their own order (the chosen ones first), then everything else
            self.bouquets = [b for b in bqs if b[0] in chosen] + [b for b in bqs if b[0] not in chosen]
            self.services = engine.get_services(self.bouquets)
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
        st = engine.load_state()
        filled = st['filled']
        self.held = st.get('review') or {}
        self.conflicts = engine.triple_conflicts(self.services)
        epg = eEPGCache.getInstance()
        self.epg = {}
        for ref, _n in self.services:
            self.epg[ref] = 'A' if ref in filled else ('?' if ref in self.held else
                                                     ('+' if engine.epg_count(epg, ref) >= 4 else ' '))
        self.build()

    def choose_bouquet(self):
        items = [(T(u'كل الباقات'), -1)] + [(T(u'%s  (%d)' % (b[0], len(b[1]))), i) for i, b in enumerate(self.bouquets)]
        self.session.openWithCallback(self._bouquet_chosen, ChoiceBox, title=T(u'اختر الباقة'), list=items)

    def _bouquet_chosen(self, res):
        if res is not None:
            self.bq = res[1]
            self.build()

    def step_bouquet(self, d):
        if not self.bouquets:
            return
        self.bq = (self.bq + 1 + d) % (len(self.bouquets) + 1) - 1
        self.build()

    def build(self, keep=None):
        mapping = engine.load_mapping()
        mode = self.FILTERS[self.filter][0]
        rows, n_linked = [], 0
        shown = self.bouquets[self.bq][1] if 0 <= self.bq < len(self.bouquets) else self.services
        for ref, name in shown:
            cands, manual = engine.resolve(ref, mapping, self.auto)
            key = cands[0][0] if cands else None
            linked = bool(key and key != '-')
            n_linked += linked
            if (mode == 'linked' and not linked) or (mode == 'unlinked' and linked):
                continue
            if mode == 'review' and not (linked and not manual and (cands[0][1] < 0.97 or ref in self.held)):
                continue
            rows.append(self.table.row((ref, name, key, cands, manual),
                                       channel_cells(self.epg.get(ref, ' '), name, key, cands, manual,
                                                     mapping.get(ref)), len(rows)))
        self['list'].setList(rows)
        if keep is not None:
            for i, r in enumerate(rows):
                if r[0][0] == keep:
                    self['list'].moveToIndex(i)
                    break
        self['key_yellow'].setText(T(u'عرض: ' + self.FILTERS[(self.filter + 1) % len(self.FILTERS)][1]))
        where = self.bouquets[self.bq][0] if 0 <= self.bq < len(self.bouquets) else u'كل الباقات'
        self['bouquet'].setText(T(u'◀   %s   ▶' % where))
        self['info'].setText(T(u'يمين/يسار: الباقة   MENU: قائمة الباقات   |   القنوات: %d   مربوطة: %d   المعروض: %d' % (
            len(shown), n_linked, len(rows))))
        self.side()

    def side(self):
        c = self.cur()
        if not c:
            self['details'].setText('')
            show_picon(self['picon'], None)
            return
        ref, name, key, cands, manual = c
        show_picon(self['picon'], find_picon(ref, name))
        st = engine.state_ro()['filled'].get(ref)
        lines = [name, u'القمر: %s' % engine.SAT_TITLES.get(engine.sat_of_ref(ref), engine.sat_of_ref(ref))]
        if ref in self.conflicts:
            lines.append(u'تنبيه: رقم خدمة هذه القناة مشترك مع قناة أخرى مختلفة، فالريسيفر لا يفرق بين دليليهما.')
            lines.append(u'لذلك لا يضيف البلوجين دليلا لها حتى لا يظهر على القناة الأخرى.')
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
        sh = engine.load_shifts().get(ref)
        if sh:
            lines.append(u'تصحيح توقيت يدوي: %+d ساعة' % sh)
        elif st and st.get('shift'):
            lines.append(u'تصحيح توقيت تلقائي: %+d ساعة' % (int(st['shift']) // 3600))
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
        return self['list'].payload()

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
        sh = engine.load_shifts().get(ref, 0)
        items += [(T(u'>> تصحيح توقيت دليل هذه القناة%s...' % ((u' (الآن %+d ساعة)' % sh) if sh else u'')), 'shift')]
        items += [(T(u'>> بحث بالاسم...'), 'search'),
                  (T(u'>> استخدام دليل قناة أخرى من باقاتك...'), 'chanpick'),
                  (T(u'>> ربط تلقائي ذكي'), 'auto'),
                 (T(u'>> تعطيل البلوجين لهذه القناة'), '-'),
                 (T(u'>> إخفاء دليل القمر الخطأ لهذه القناة'), '!')]
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
        if v == 'shift':
            choices = [(T(u'بدون تصحيح'), 0)] + [(T(u'%+d ساعة' % h), h) for h in (1, -1, 2, -2, 3, -3)]
            self.session.openWithCallback(lambda r: r is not None and self._set_shift(ref, r[1]), ChoiceBox,
                                          title=T(u'البرامج تظهر قبل موعدها: اختر سالب. تظهر بعد موعدها: اختر موجب.'),
                                          list=choices)
            return
        if v == 'chanpick':
            from Screens.VirtualKeyBoard import VirtualKeyBoard
            self.session.openWithCallback(lambda t: t is not None and self.pick_channel(ref, u(t)), VirtualKeyBoard,
                                          title=T(u'اكتب جزءا من اسم القناة التي تريد دليلها (أو اتركه فارغا)'),
                                          text='')
            return
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
            if v not in ('-', '!') and not v.startswith('chan:'):
                engine.learn(name, v, ref)     # same name/service on other satellites/feeds follows
                if old and old != v:
                    engine.reject(name, old)   # never suggest the wrong one again
        engine.save_mapping(m)
        self.build(ref)

    def _set_shift(self, ref, hours):
        engine.set_shift(ref, hours)
        self.session.openWithCallback(lambda yes: yes and self._update_choice((None, 'one')), MessageBox,
                                      T(u'تم حفظ تصحيح التوقيت (%+d ساعة). تحديث هذه القناة الآن؟' % hours),
                                      MessageBox.TYPE_YESNO)
        self.build(ref)

    def pick_channel(self, ref, query):
        q = engine.norm(query) if query.strip() else u''
        epg = eEPGCache.getInstance()
        items = []
        for r, n in self.services:
            if r == ref or (q and q not in engine.norm(n)):
                continue
            cnt = engine.epg_count(epg, r, 24)
            if cnt <= 0:
                continue
            items.append((T(u'%s  (%s، %d برنامج)' % (n, engine.SAT_TITLES.get(engine.sat_of_ref(r), engine.sat_of_ref(r)),
                                                      cnt)), u'chan:%s|%s' % (r, n)))
            if len(items) >= 300:
                break
        if not items:
            self.session.open(MessageBox, T(u'لا توجد قناة بهذا الاسم لها دليل الآن.'), MessageBox.TYPE_INFO, timeout=5)
            return
        self.session.openWithCallback(lambda r: r and self.picked(ref, r), ChoiceBox,
                                      title=T(u'اختر القناة التي سيُنسخ دليلها'), list=items)

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
        if start_update(refs, target='all', manual=True):
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
        self.table = Table(1206, [Col(u'القناة', 226), Col(u'الآن', 82, 'center', 1, 'blue'),
                                  Col(u'البرنامج الحالي', 420), Col(u'التالي', 82, 'center', 1, 'blue'),
                                  Col(u'البرنامج التالي', 376)], row_h=40, font=19)
        self.skin = skin(1240, 700, (
            ui.box(10, 66, 1220, 38, ui.HEAD) +
            ui.label('bouquet', 20, 66, 1200, 38, 20, ui.hexc('amber'), ui.HEAD, 'center', 'center') +
            self.table.header(10, 110) + ui.list_widget('list', 10, 146, 1220, 440) +
            ui.label('info', 10, 600, 1220, 36, 16, DIM, BG, 'right', 'center')) + buttons(646, 1240))
        BaseScreen.__init__(self, session, u'الآن وبعده')
        self['list'] = self.table.widget()
        self['info'] = Label('')
        self['bouquet'] = Label('')
        set_buttons(self, u'', u'مشاهدة القناة', u'', u'تحديث القائمة')
        self['actions'] = ActionMap(['OkCancelActions', 'ColorActions', 'DirectionActions'], {
            'ok': self.zap, 'green': self.zap, 'cancel': self.close, 'yellow': self.toggle, 'blue': self.build,
            'up': lambda: self['list'].up(), 'down': lambda: self['list'].down(),
            'left': lambda: self.step(-1), 'right': lambda: self.step(1)}, -1)
        self['bq_actions'] = ActionMap(['ChannelSelectBaseActions'], {
            'nextBouquet': lambda: self['list'].pageUp(), 'prevBouquet': lambda: self['list'].pageDown()}, -2)
        self.only_plugin = False
        self.bouquets, self.bq = [], -1
        self.onLayoutFinish.append(self.load)

    def load(self):
        try:
            bqs = engine.get_bouquets()
            chosen = [x for x in C.bouquets.value.split('|') if x]
            self.bouquets = [b for b in bqs if b[0] in chosen] + [b for b in bqs if b[0] not in chosen]
            if self.bouquets:
                self.bq = 0
        except Exception as e:
            log(u'الباقات: %s' % e)
        self.build()

    def step(self, d):
        if self.bouquets:
            self.bq = (self.bq + 1 + d) % (len(self.bouquets) + 1) - 1
            self.build()

    def toggle(self):
        self.only_plugin = not self.only_plugin
        self.build()

    def build(self):
        epg = eEPGCache.getInstance()
        filled = engine.load_state()['filled']
        now = time.time()
        rows = []
        shown = self.bouquets[self.bq][1] if 0 <= self.bq < len(self.bouquets) else engine.get_services()
        seen = set()
        for ref, name in shown:
            if ref in seen or (self.only_plugin and ref not in filled):
                continue
            seen.add(ref)
            nw, nx = engine.now_next(epg, ref)
            if not nw:
                continue
            pct = int(max(0, min(100, (now - nw[1]) * 100.0 / max(1, nw[2]))))
            left = max(0, int(nw[1] + nw[2] - now) // 60)
            cells = [{'t': name, 'fg': 'accent' if ref in filled else 'txt'},
                     time.strftime('%H:%M', time.localtime(nw[1])),
                     {'t': nw[0], 'pct': pct, 'bar': 'amber' if left <= 10 else 'accent'},
                     time.strftime('%H:%M', time.localtime(nx[1])) if nx else u'',
                     {'t': nx[0] if nx else u'', 'fg': 'dim'}]
            rows.append(self.table.row(ref, cells, len(rows)))
        self['list'].setList(rows)
        where = self.bouquets[self.bq][0] if 0 <= self.bq < len(self.bouquets) else u'كل الباقات'
        self['bouquet'].setText(T(u'\u25c0   %s   \u25b6' % where))
        self['key_yellow'].setText(T(u'عرض: كل القنوات' if self.only_plugin else u'عرض: قنوات البلوجين'))
        self['info'].setText(T(u'%d قناة   |   يمين/يسار: الباقة   OK: مشاهدة القناة   |   الأخضر = دليل من البلوجين'
                               % len(rows)))

    def zap(self):
        ref = self['list'].payload()
        if ref:
            try:
                self.session.nav.playService(eServiceReference(T(ref)))
            except Exception:
                pass
            self.close()


# --------------------------------------------------------------------- bouquet statistics
class BouquetsScreen(BaseScreen):
    """choose the bouquets the plugin updates (none chosen = all)."""

    def __init__(self, session):
        self.table = Table(866, [Col(u'التحديث', 110, 'center'), Col(u'الباقة', 590), Col(u'القنوات', 150, 'center', 1)],
                           row_h=42, font=20)
        self.skin = skin(900, 680, (
            self.table.header(10, 66) + ui.list_widget('list', 10, 102, 880, 462) +
            ui.label('info', 16, 570, 868, 56, 17, DIM, BG, 'right', 'center')) + buttons(632, 900))
        BaseScreen.__init__(self, session, u'الباقات التي يحدّثها البلوجين')
        self.chosen = set(x for x in C.bouquets.value.split('|') if x)
        self.all = []
        self['list'] = self.table.widget()
        self['info'] = Label(T(u'OK: اختيار/إلغاء الباقة.  لو لم تختر أي باقة، يحدّث البلوجين كل الباقات.\n'
                               u'الباقات المختارة تظهر أولا في شاشة القنوات وتُحدَّث أولا.'))
        set_buttons(self, u'خروج بدون حفظ', u'حفظ', u'الكل / لا شيء', u'')
        self['actions'] = ActionMap(['OkCancelActions', 'ColorActions'], {
            'ok': self.toggle, 'cancel': self.close, 'red': self.close, 'green': self.save,
            'yellow': self.toggle_all}, -1)
        self.onLayoutFinish.append(self.load)

    def load(self):
        try:
            self.all = [(b, len(items)) for b, items in engine.get_bouquets()]
        except Exception as e:
            self.all = []
            log(u'الباقات: %s' % e)
        self.build()

    def build(self, keep=0):
        rows = []
        for b, n in self.all:
            on = b in self.chosen or not self.chosen
            rows.append(self.table.row(b, [{'t': u'\u2714' if on else u'\u2014', 'chip': 'accent'} if on
                                           else {'t': u'\u2014', 'fg': 'dim'}, b, str(n)], len(rows)))
        self['list'].setList(rows)
        if rows:
            self['list'].moveToIndex(min(keep, len(rows) - 1))

    def toggle(self):
        b = self['list'].payload()
        if not b:
            return
        if not self.chosen:
            self.chosen = set(b for b, _n in self.all)
        if b in self.chosen:
            self.chosen.discard(b)
        else:
            self.chosen.add(b)
        self.build(self['list'].getSelectedIndex())

    def toggle_all(self):
        everything = not self.chosen or set(b for b, _n in self.all) <= self.chosen
        self.chosen = set([u'\u0000']) if everything else set()   # none / all
        self.build(self['list'].getSelectedIndex())

    def save(self):
        chosen = [b for b, _n in self.all if b in self.chosen]
        if len(chosen) == len(self.all):
            chosen = []
        if not chosen and self.chosen and self.chosen != set(b for b, _n in self.all):
            self.session.open(MessageBox, T(u'اختر باقة واحدة على الأقل.'), MessageBox.TYPE_ERROR, timeout=4)
            return
        C.bouquets.value = u'|'.join(chosen)
        C.bouquets.save()
        configfile.save()
        self.close()


class StatsScreen(BaseScreen):
    CARDS = (u'قنوات', u'مربوطة', u'حدّثها البلوجين', u'برامج', u'آخر تحديث')

    def __init__(self, session):
        self.table = Table(1196, [Col(u'الباقة', 386), Col(u'القنوات', 150, 'center', 1),
                                  Col(u'مربوطة', 150, 'center', 1, 'blue'),
                                  Col(u'حدّثها البلوجين', 170, 'center', 1, 'accent'),
                                  Col(u'البرامج', 170, 'center', 1), Col(u'دليل القمر', 150, 'center', 1, 'dim')],
                           row_h=40, font=19)
        self.skin = skin(1240, 700, (
            self.table.header(10, 66) + ui.list_widget('list', 10, 102, 1220, 404) +
            ui.stat_cards('card', 5, 10, 516, 1220) +
            ui.label('info', 16, 594, 1208, 96, 16, DIM, BG, 'right', 'top')))
        BaseScreen.__init__(self, session, u'إحصائيات الباقات')
        self['list'] = self.table.widget()
        for i, t in enumerate(self.CARDS):
            self['card%d' % i] = Label('-')
            self['card%dt' % i] = Label(T(t))
        self['info'] = Label(T(u'جاري الحساب...'))
        self['actions'] = ActionMap(['OkCancelActions'], {'ok': self.close, 'cancel': self.close}, -1)
        self.onLayoutFinish.append(self.build)

    def build(self):
        bouquets = engine.get_bouquets()
        state = engine.load_state()
        auto = engine.load_auto()
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
            ch = b['channels'] or 0
            cover = ((b['updated'] or 0) + (b['native'] or 0)) * 100 // ch if ch else 0
            out.append(self.table.row(b['name'], [
                {'t': b['name'], 'pct': min(100, cover), 'bar': 'accent' if cover >= 60 else 'amber'},
                str(ch), str(b['linked'] or 0), str(b['updated'] or 0), str(b['events'] or 0),
                str(b['native'] or 0)], len(out)))
        self['list'].setList(out)
        last = state.get('last')
        vals = [tot['channels'], tot['linked'], tot['updated'], tot['events']]
        for i, v in enumerate(vals):
            self['card%d' % i].setText(str(v))
        self['card4'].setText(time.strftime('%H:%M', time.localtime(last)) if last else '-')
        if last:
            self['card4t'].setText(T(time.strftime(u'آخر تحديث %d/%m', time.localtime(last))))
        srcs = {}
        for f in state.get('filled', {}).values():
            s = engine.display_key(f.get('key', '')).split(':')[0]
            srcs[s] = srcs.get(s, 0) + 1
        self['info'].setText(T(u'الشريط تحت اسم الباقة = نسبة القنوات التي لها دليل (من البلوجين أو القمر)\n'
                               u'حسب المصدر:  ' + u'   \u00b7   '.join(
                                   u'%s %d' % kv for kv in sorted(srcs.items(), key=lambda kv: -kv[1])[:10])))


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
        self.skin = skin(1120, 700, (
            ui.box(10, 66, 1100, 570) +
            ('<widget name="config" position="%d,%d" size="%d,%d" itemHeight="%d" font="Regular;%d" '
             'scrollbarMode="showOnDemand" foregroundColor="%s" backgroundColor="%s" backgroundColorSelected="%s" '
             'foregroundColorSelected="%s" transparent="0" zPosition="1" />') % (
                _s(20), _s(74), _s(1080), _s(552), _s(40), _s(20), TXT, PANEL, ui.SEL, TXT)) + buttons(646, 1120))
        BaseScreen.__init__(self, session, u'إعدادات Saad EPG')
        lst = [
            getConfigListEntry(T(u'السيرفر السحابي (GitHub) - مصدر رئيسي سريع'), C.cloud),
            getConfigListEntry(T(u'رابط السحابة'), C.cloud_url),
            getConfigListEntry(T(u'القنوات التي يتم تحديثها'), C.target),
            getConfigListEntry(T(u'عدد الأيام'), C.days),
            getConfigListEntry(T(u'تثبيت دليل البلوجين (لا يظهر دليل القمر فوقه)'), C.lock_epg),
            getConfigListEntry(T(u'الدليل الذهبي (كل مصادر القناة في جدول واحد)'), C.gold),
            getConfigListEntry(T(u'تحديث تلقائي يومي'), C.autoupdate),
            getConfigListEntry(T(u'وقت التحديث اليومي'), C.update_time),
            getConfigListEntry(T(u'تحديث تلقائي ثان'), C.autoupdate2),
            getConfigListEntry(T(u'وقت التحديث الثاني'), C.update_time2),
            getConfigListEntry(T(u'تقسيم التحديث التلقائي على مراحل'), C.stages),
            getConfigListEntry(T(u'الوقت بين كل مرحلة'), C.stage_gap),
            getConfigListEntry(T(u'تحديث ذكي عند تشغيل الريسيفر'), C.boot_update),
            getConfigListEntry(T(u'التنبيه بتحديثات البلوجين'), C.check_updates),
            getConfigListEntry(T(u'نسخة احتياطية أسبوعية تلقائية على الهارد'), C.auto_backup),
            getConfigListEntry(T(u'لغة دليل القنوات الأجنبية'), C.foreign_lang),
            getConfigListEntry(T(u'لغة الدليل للقنوات العربية'), C.lang_mode),
            getConfigListEntry(T(u'ترجمة الدليل الإنجليزي للقنوات العربية'), C.ar_tr_mode),
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
            getConfigListEntry(T(u'مستوى الأمان في استيراد الربط التلقائي'), C.link_mode),
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
            getConfigListEntry(T(u'مصدر LBCI الرسمي (لبنان)'), C.src_lbci),
            getConfigListEntry(T(u'مصدر التلفزة التونسية الرسمي (الوطنية 1 و 2)'), C.src_tunisia),
            getConfigListEntry(T(u'مصدر TRT عربي الرسمي'), C.src_trt),
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
        if bool(engine.LOCK['on']) != bool(C.lock_epg.value):
            engine.eit_lock(engine.lock_wanted(engine.load_state()), C.lock_epg.value)
            engine.eit_reload()
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
        # settings: taken back from the disk after a new install, and copied to it every week
        self.restore_tries = 0
        self.rtimer = make_timer(self.try_restore)
        self.btimer = make_timer(self.weekly_backup)
        self.try_restore()
        self.btimer.startLongTimer(900)
        self.schedule(boot=True)
        if C.check_updates.value:
            self.vtimer.startLongTimer(300)
        # guide on demand: when a channel without EPG is opened, prepare it in the background
        self.zap_timer = make_timer(self.check_zap)
        self.zap_seen = {}
        self.zap_job = None
        self.zap_watch = make_timer(self.zap_finished)
        # the update thread wrote blacklist.eit: enigma2 reads it again on the main thread
        self.lock_timer = make_timer(self.apply_lock)
        self.lock_timer.start(3000, False)
        try:
            from enigma import iPlayableService
            self.ev_start = iPlayableService.evStart
            session.nav.event.append(self.on_service_event)
        except Exception as e:
            print('[SaadEPG] zap watcher not available', e)

    def apply_lock(self):
        if engine.LOCK['dirty']:
            engine.eit_reload()

    def try_restore(self):
        try:
            res = auto_restore()
        except Exception as e:
            res = None
            log(u'الاستعادة التلقائية: %s' % e)
        if res == 'wait':
            self.restore_tries += 1
            if self.restore_tries < 8:          # the disk can take a few minutes to appear
                self.rtimer.startLongTimer(60)
            else:
                _mark_restore_checked()
            return
        if res:
            self.schedule()
            self._popup(u'Saad EPG: تمت استعادة الإعدادات والربط اليدوي تلقائيا من الهارد', 10)

    def weekly_backup(self):
        self.btimer.startLongTimer(6 * 3600)
        if not C.auto_backup.value or engine.fresh_install() or not os.path.exists(RESTORE_MARK):
            return
        info = engine.backup_info()
        if info and time.time() - info[1] < 7 * 86400:
            return
        try:
            d = engine.backup_all(_settings_dict())
            if d:
                log(u'نسخة احتياطية أسبوعية تلقائية: %s' % d)
        except Exception as e:
            log(u'النسخة الاحتياطية الأسبوعية: %s' % e)

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
            if engine.load_mapping().get(ref) == '!':
                engine.clear_plugin_epg(epg, [ref])    # hide the satellite's wrong guide again
                return
            if engine.epg_count(epg, ref, 6) > 0:
                return
            from enigma import eServiceCenter
            info = eServiceCenter.getInstance().info(sref)
            name = u(info and info.getName(sref) or '').replace(u'\x86', u'').replace(u'\x87', u'')
            self.zap_seen[ref] = time.time()

            def decide():      # reading the link files can take a moment: not on the main thread
                known = engine.resolve(ref, engine.load_mapping(), engine.load_auto())[0]
                linked = bool(known and known[0][0] != '-') or \
                    bool(engine.linked_refs().get(engine.ref_key(ref) or '')) or \
                    bool(engine.load_refs().get(engine.ref_key(ref) or ''))
                return known, linked
            self._zap_decide = BackgroundJob(decide, lambda res: self._zap_go(ref, name, res))
        except Exception as e:
            print('[SaadEPG] zap check error', e)

    def _zap_go(self, ref, name, res):
        try:
            if isinstance(res, Exception):
                return
            known, linked = res
            if known and known[0][0] == '-':
                return
            r = engine.Updater.running
            if r and not r.done:
                return
            job = engine.Updater(current_cfg(target='all', smart=False), set([ref]), [(ref, name)], quiet=True)
            if job.start():
                self.zap_job, self.zap_name = job, name
                log(u'تجهيز دليل القناة المفتوحة: %s' % name)
                if linked and C.zap_notify.value:
                    self._popup(u'Saad EPG: جاري تجهيز دليل %s' % name, 4)
                self.zap_watch.start(2000, False)
        except Exception as e:
            print('[SaadEPG] zap check error', e)

    def zap_finished(self):
        j = self.zap_job
        if not j or not j.done:
            return
        self.zap_watch.stop()
        self.zap_job = None
        if j.stats.get('events') and C.zap_notify.value:
            self._popup(u'Saad EPG: تم تجهيز دليل %s (%d برنامج)' % (self.zap_name, j.stats['events']), 4)

    def _popup(self, text, timeout):
        try:
            from Tools import Notifications
            Notifications.AddPopup(T(text), MessageBox.TYPE_INFO, timeout, 'SaadEPGZap')
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
        times = []
        for on, clock in ((C.autoupdate, C.update_time), (C.autoupdate2, C.update_time2)):
            if not on.value:
                continue
            h, m = clock.value
            lt = time.localtime(now)
            t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))
            if t <= now + 30:
                t += 86400
            times.append(t)
        if times:
            self.timer.startLongTimer(int(min(times) - now))

    def fire(self):
        if time.localtime().tm_year < 2020:  # clock not synced yet
            self.timer.startLongTimer(60)
            return
        if self.boot:
            log(u'بدء التحديث التلقائي الذكي (بعد التشغيل)')
            start_update(smart=True)
        else:
            self.start_stages()
        self.schedule()

    # ---- the daily update in stages: your bouquets in order, a part at a time
    def start_stages(self):
        n = int(C.stages.value)
        try:
            bqs = engine.get_bouquets()
            chosen = [x for x in C.bouquets.value.split('|') if x]
            if chosen:
                bqs = [b for b in bqs if b[0] in chosen]
            refs = [r for r, _n in engine.get_services(bqs)]
        except Exception as e:
            log(u'التحديث على مراحل: %s' % e)
            refs = []
        if n <= 1 or len(refs) < 2 * n:
            log(u'بدء التحديث التلقائي')
            start_update()
            return
        size = (len(refs) + n - 1) // n
        self.parts = [set(refs[i:i + size]) for i in range(0, len(refs), size)]
        self.part_no = 0
        if not hasattr(self, 'stage_timer'):
            self.stage_timer = make_timer(self.next_stage)
        self.next_stage()

    def next_stage(self):
        self.stage_timer.stop()
        r = engine.Updater.running
        if r and not r.done:          # the previous stage (or another update) is still working
            self.stage_timer.startLongTimer(60)
            return
        if self.part_no >= len(self.parts):
            log(u'انتهى التحديث التلقائي على %d مراحل' % len(self.parts))
            return
        part = self.parts[self.part_no]
        self.part_no += 1
        log(u'التحديث التلقائي: المرحلة %d من %d (%d قناة)' % (self.part_no, len(self.parts), len(part)))
        start_update(part)
        if self.part_no < len(self.parts):
            self.stage_timer.startLongTimer(int(C.stage_gap.value) * 60)

    def check_version(self):
        def done(info):
            if isinstance(info, dict):
                self.new_version = info
                try:
                    from Tools import Notifications
                    Notifications.AddPopup(T(u'Saad EPG: يوجد إصدار جديد %s - افتح البلوجين ثم "التحقق من تحديث البلوجين"'
                                             % info.get('version')), MessageBox.TYPE_INFO, 15, 'SaadEPGUpdate')
                except Exception:
                    pass
        self._vjob = BackgroundJob(lambda: engine.check_plugin_update(VERSION), done)
        self.vtimer.startLongTimer(24 * 3600)


# --------------------------------------------------------------------- the infobar shows the plugin's now/next
def _cache_event(ref, nownext):
    try:
        return eEPGCache.getInstance().lookupEventTime(eServiceReference(T(ref)), -1, 1 if nownext else 0)
    except Exception:
        return None


def _playing_ref(navcore):
    try:
        sref = navcore.getCurrentlyPlayingServiceReference()
        return sref and sref.toString()
    except Exception:
        return None


def patch_now_next():
    """enigma2 shows the now/next sent with the picture (the satellite's, often in another language) before
    the guide in the cache. For channels the plugin fills, the cache (the plugin's guide) is shown first."""
    done = []
    try:
        from Components.Sources import EventInfo as EI
        cls = EI.EventInfo
        if not getattr(cls, '_saadepg', False):
            old = cls.event.fget

            def event(self, old=old):
                try:
                    svc = getattr(self, '_EventInfo__service', None)
                    ref = svc.toString() if isinstance(svc, eServiceReference) else _playing_ref(self.navcore)
                    if ref and engine.is_locked(ref):
                        ev = _cache_event(ref, self.now_or_next)
                        if ev:
                            return ev
                except Exception:
                    pass
                return old(self)
            cls.event = property(event)
            cls._saadepg = True
            done.append('EventInfo')
    except Exception as e:
        print('[SaadEPG] now/next patch (EventInfo):', e)
    try:
        from Screens import InfoBarGenerics as IB
        for name in dir(IB):
            cls = getattr(IB, name)
            if not isinstance(cls, type) or 'getNowNext' not in cls.__dict__ or cls.__dict__.get('_saadepg'):
                continue
            old = cls.__dict__['getNowNext']

            def getNowNext(self, old=old):
                old(self)
                try:
                    ref = _playing_ref(self.session.nav)
                    if ref and engine.is_locked(ref):
                        evs = [e for e in (_cache_event(ref, 0), _cache_event(ref, 1)) if e]
                        if evs:
                            self.epglist = evs
                except Exception:
                    pass
            cls.getNowNext = getNowNext
            cls._saadepg = True
            done.append(name)
    except Exception as e:
        print('[SaadEPG] now/next patch (InfoBar):', e)
    return done


# --------------------------------------------------------------------- entry points
def main(session, **kwargs):
    _migrate_old_settings()
    session.open(SaadEPGMain)


def nownext(session, **kwargs):
    session.open(NowNextScreen)


def wrong_epg(session, **kwargs):
    try:
        sref = session.nav.getCurrentlyPlayingServiceReference()
        ref = engine.clean_ref(sref.toString()) if sref else None
        from enigma import eServiceCenter
        info = eServiceCenter.getInstance().info(sref) if sref else None
        name = u(info and info.getName(sref) or '').replace(u'\x86', u'').replace(u'\x87', u'')
    except Exception:
        ref = None
    if not ref:
        return
    st = engine.load_state()['filled'].get(ref)
    if st:
        msg = (u'دليل "%s" أضافه البلوجين من: %s\nهل هو خطأ؟ سيُمسح ولن يُستخدم هذا المصدر لهذه القناة مرة أخرى، '
               u'ثم يبحث البلوجين عن مصدر آخر.' % (name, engine.display_key(st.get('key', ''))))
    else:
        msg = (u'دليل "%s" يأتي من القمر نفسه وليس من البلوجين.\nهل تريد إخفاءه؟ (يمكن إلغاء ذلك من شاشة '
               u'القنوات بالربط التلقائي الذكي)' % name)

    def done(yes):
        if not yes:
            return
        epg = eEPGCache.getInstance()
        if engine.mark_wrong(ref, name) == 'satellite':
            m = engine.load_mapping()
            m[ref] = '!'
            engine.save_mapping(m)
        n, how = engine.clear_plugin_epg(epg, [ref])
        if how is None:
            engine.clear_plugin_epg(epg, [ref], everything=True)
            start_update(target='all')     # the whole guide was cleared: bring it back now
        else:
            start_update(set([ref]), target='all')
        session.open(MessageBox, T(u'تم. سيُعاد تجهيز الدليل خلال دقائق.'), MessageBox.TYPE_INFO, timeout=5)
    session.openWithCallback(done, MessageBox, T(msg), MessageBox.TYPE_YESNO)


def sessionstart(reason, session=None, **kwargs):
    if reason == 0 and session is not None:
        _migrate_old_settings()
    if reason == 0 and session is not None and AutoUpdate.instance is None:
        engine.LOCK['on'] = C.lock_epg.value
        try:
            patched = patch_now_next()
            log(u'تثبيت الآن/التالي في شريط المعلومات: %s' % (u'، '.join(patched) or u'غير متاح'))
        except Exception as e:
            print('[SaadEPG] patch error', e)
        try:
            AutoUpdate(session)
        except Exception as e:
            print('[SaadEPG] autostart error', e)


def Plugins(**kwargs):
    name = 'Saad EPG'
    desc = T(u'دليل برامج القنوات العربية والأوروبية من الإنترنت')
    return [
        PluginDescriptor(name=name, description=desc, where=PluginDescriptor.WHERE_PLUGINMENU,
                         icon='plugin.png', fnc=main),
        PluginDescriptor(name=name, description=desc, where=PluginDescriptor.WHERE_EXTENSIONSMENU, fnc=main),
        PluginDescriptor(name=T(u'Saad EPG - الآن وبعده'), description=desc,
                         where=PluginDescriptor.WHERE_EVENTINFO, fnc=nownext),
        PluginDescriptor(name=T(u'Saad EPG - الدليل خطأ لهذه القناة'), description=desc,
                         where=PluginDescriptor.WHERE_EVENTINFO, fnc=wrong_epg),
        PluginDescriptor(where=PluginDescriptor.WHERE_SESSIONSTART, fnc=sessionstart),
    ]
