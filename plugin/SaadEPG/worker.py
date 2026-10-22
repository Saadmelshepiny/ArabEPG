# -*- coding: utf-8 -*-
# Saad EPG - the heavy part of an update (cloud, linking, fetching, translation, checks) in its own process.
# enigma2 runs Python in its main loop: work done inside it, even in a thread, can make the picture,
# the remote and other plugins wait. Started by engine.Updater with: python3 worker.py <job folder>
from __future__ import absolute_import, print_function

import importlib
import json
import os
import pickle
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
PKG = os.path.basename(HERE)


def main(d):
    try:
        os.nice(10)              # the receiver's own work always comes first
    except Exception:
        pass
    engine = importlib.import_module(PKG + '.engine')
    sources = importlib.import_module(PKG + '.sources')
    with open(os.path.join(d, 'job.pkl'), 'rb') as f:
        job = pickle.load(f)
    cfg = job['cfg']
    sources.XML_PREFER_EN = cfg.get('foreign_lang', 'en') == 'en'
    up = engine.Updater(cfg, quiet=job.get('quiet', False))
    up.services = job['services']
    up.native = job.get('native') or {}
    up.rich = job.get('rich') or {}
    up.present = job.get('present') or {}
    nlog = [0]
    orig_log = sources.log

    def counted(msg):
        orig_log(msg)
        nlog[0] += 1
    sources.log = counted
    engine.log = counted
    for m in list(sys.modules.values()):
        if m is not None and getattr(m, '__name__', '').startswith(PKG + '.') and getattr(m, 'log', None) is orig_log:
            m.log = counted
    finished = threading.Event()

    def report():
        while True:
            last = finished.wait(0.7)
            if os.path.exists(os.path.join(d, 'cancel')):
                up.cancel = True
            pr = {'stages': [[s.id, s.label, s.total, s.done, s.state, s.info, s.weight] for s in list(up.stages)],
                  'log': list(sources.LOG[-60:]), 'nlog': nlog[0]}
            try:
                tmp = os.path.join(d, 'progress.tmp')
                with open(tmp, 'wb') as f:
                    f.write(json.dumps(pr, ensure_ascii=False).encode('utf-8'))
                os.rename(tmp, os.path.join(d, 'progress.json'))
            except Exception:
                pass
            if last:
                return
    t = threading.Thread(target=report)
    t.daemon = True
    t.start()
    try:
        ctx = up._compute()
    finally:
        finished.set()
        t.join(5)
    results = ctx.pop('results')
    ctx['stats'], ctx['health'], ctx['offline'] = up.stats, up.health, getattr(up, 'offline', False)
    tmp = os.path.join(d, 'result.tmp')
    with open(tmp, 'wb') as f:
        pickle.dump(ctx, f, 2)
        for k, evs in results.items():
            pickle.dump((k, evs), f, 2)
        pickle.dump(None, f, 2)
    os.rename(tmp, os.path.join(d, 'result.pkl'))


if __name__ == '__main__':
    t0 = time.time()
    main(sys.argv[1])
    print('worker done in %ds' % (time.time() - t0))
