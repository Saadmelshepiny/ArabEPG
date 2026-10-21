#!/usr/bin/env python3
"""Build the enigma2 .ipk from plugin/ (pure Python, no ar/opkg tools needed).

Usage: python3 tools/build_ipk.py [--version 3.0] [--out release]
The version is read from plugin/SaadEPG/plugin.py (VERSION = '...') when not given.
Also writes version.json for the plugin's online self-update.
"""
import argparse
import io
import json
import os
import re
import tarfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = 'enigma2-plugin-extensions-saadepg'
DEST = './usr/lib/enigma2/python/Plugins/Extensions/SaadEPG'
REPO_RAW = 'https://raw.githubusercontent.com/Saadmelshepiny/ArabEPG/main'


def tar_gz(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz', format=tarfile.GNU_FORMAT) as t:
        for name, data, mode in members:
            if data is None:
                ti = tarfile.TarInfo(name)
                ti.type, ti.mode = tarfile.DIRTYPE, 0o755
                ti.mtime = int(time.time())
                t.addfile(ti)
                continue
            ti = tarfile.TarInfo(name)
            ti.size, ti.mode, ti.mtime = len(data), mode, int(time.time())
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def ar(files):
    out = io.BytesIO()
    out.write(b'!<arch>\n')
    for name, data in files:
        hdr = '%-16s%-12d%-6d%-6d%-8s%-10d`\n' % (name, int(time.time()), 0, 0, '100644', len(data))
        out.write(hdr.encode('ascii'))
        out.write(data)
        if len(data) % 2:
            out.write(b'\n')
    return out.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--version')
    ap.add_argument('--out', default=os.path.join(ROOT, 'release'))
    ap.add_argument('--notes', default='')
    a = ap.parse_args()
    src = os.path.join(ROOT, 'plugin', 'SaadEPG')
    ver = a.version or re.search(r"VERSION = '([^']+)'", open(os.path.join(src, 'plugin.py'), encoding='utf8').read()).group(1)

    ctl = os.path.join(ROOT, 'plugin', 'CONTROL')
    control = open(os.path.join(ctl, 'control'), encoding='utf8').read()
    control = re.sub(r'(?m)^Version: .*$', 'Version: ' + ver, control)
    cmembers = [('./control', control.encode('utf8'), 0o644)]
    for s in ('postinst', 'postrm', 'preinst', 'prerm'):
        p = os.path.join(ctl, s)
        if os.path.exists(p):
            cmembers.append(('./' + s, open(p, 'rb').read(), 0o755))

    dmembers = []
    parts = DEST.split('/')
    for i in range(2, len(parts) + 1):
        dmembers.append(('/'.join(parts[:i]) + '/', None, 0o755))
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d != '__pycache__')
        rel = os.path.relpath(root, src)
        base = DEST if rel == '.' else DEST + '/' + rel.replace(os.sep, '/')
        if rel != '.':
            dmembers.append((base + '/', None, 0o755))
        for fn in sorted(files):
            if not fn.endswith(('.pyc', '.pyo')):
                dmembers.append((base + '/' + fn, open(os.path.join(root, fn), 'rb').read(), 0o644))

    ipk = ar([('debian-binary', b'2.0\n'), ('control.tar.gz', tar_gz(cmembers)), ('data.tar.gz', tar_gz(dmembers))])
    os.makedirs(a.out, exist_ok=True)
    for old in os.listdir(a.out):
        if old.startswith(PKG) and old.endswith('.ipk'):
            os.remove(os.path.join(a.out, old))
    name = '%s_%s_all.ipk' % (PKG, ver)
    with open(os.path.join(a.out, name), 'wb') as f:
        f.write(ipk)
    info = {'version': ver, 'ipk': '%s/release/%s' % (REPO_RAW, name), 'date': time.strftime('%Y-%m-%d'),
            'notes': a.notes}
    with open(os.path.join(ROOT, 'version.json'), 'w', encoding='utf8') as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print('built %s (%d bytes)' % (name, len(ipk)))


if __name__ == '__main__':
    main()
