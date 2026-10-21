#!/bin/bash
# grab one part of one site with iptv-org/epg, then retry (slowly) the channels that came back empty.
# usage: cloud/grab.sh <site> <part> <parts>      (run from the repository root, iptv-org/epg in ./iptv-epg)
site="$1"; part="${2:-1}"; parts="${3:-1}"
root="$(pwd)"
work="$root/work"; mkdir -p "$work"
if [ "$site" = "translate" ]; then
  # foreign guides -> English (offline models). Never fails the build: the previous dictionaries stay in use.
  # The log is published (epg branch, tr/log.txt) so it can be read without signing in to GitHub.
  translate() {
    echo "== translate $part | $(grep PRETTY /etc/os-release | cut -d= -f2) | $(python3 --version 2>&1)"
    PY=""
    for c in /opt/hostedtoolcache/Python/3.12*/x64/bin/python3 /opt/hostedtoolcache/Python/3.11*/x64/bin/python3 \
             /opt/hostedtoolcache/Python/3.13*/x64/bin/python3 python3.12 python3.11 python3.13 python3; do
      command -v "$c" >/dev/null 2>&1 || continue
      rm -rf /tmp/trenv
      if "$c" -m venv /tmp/trenv && /tmp/trenv/bin/pip install -q ctranslate2 sentencepiece sacremoses \
         && /tmp/trenv/bin/pip install -q --no-deps argostranslate; then
        PY=/tmp/trenv/bin/python; echo "== python: $c ($($PY --version 2>&1))"; break
      fi
      echo "== could not set up with $c"
    done
    if [ -z "$PY" ]; then
      echo "== trying uv (managed Python 3.12)"
      curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
      export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
      rm -rf /tmp/trenv
      uv venv -q -p 3.12 /tmp/trenv && uv pip install -q -p /tmp/trenv/bin/python ctranslate2 sentencepiece sacremoses \
        && uv pip install -q --no-deps -p /tmp/trenv/bin/python argostranslate \
        && PY=/tmp/trenv/bin/python
    fi
    if [ -z "$PY" ]; then echo "== no Python with ctranslate2: translation skipped"; return; fi
    timeout 70m "$PY" cloud/translate_en.py --langs "$part" --work "$work" || echo "== translation stopped ($?)"
  }
  translate 2>&1 | tee "$work/out_trlog_${part}.xml"
  ls -la "$work"
  exit 0
fi
python3 tools/make_catalog.py --iptv iptv-epg --cloud cloud/channels >/dev/null
list="$work/ch_${site}.${part}.xml"
python3 cloud/chan_tools.py split "cloud/channels/${site}.channels.xml" "$part" "$parts" "$list"
read days delay conns <<< "$(python3 cloud/chan_tools.py opts "$site")"
out="$work/out_${site}.${part}.xml"
echo "== $site part $part/$parts: days=$days delay=$delay connections=$conns"
cd iptv-epg
npx tsx scripts/commands/epg/grab.ts --channels="$list" --output="$out" \
  --days="$days" --maxConnections="$conns" --delay="$delay" --timeout=30000 || true
cd "$root"
if python3 cloud/chan_tools.py missing "$list" "$out" "$work/retry_${site}.${part}.xml"; then
  echo "== retry"
  sleep 20
  cd iptv-epg
  npx tsx scripts/commands/epg/grab.ts --channels="$work/retry_${site}.${part}.xml" \
    --output="$work/out_${site}.${part}.retry.xml" \
    --days="$days" --maxConnections=1 --delay=$(( delay > 2000 ? delay : 2000 )) --timeout=45000 || true
  cd "$root"
fi
rm -f "$work"/ch_*.xml "$work"/retry_*.xml
ls -la "$work"
