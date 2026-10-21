#!/bin/bash
# grab one part of one site with iptv-org/epg, then retry (slowly) the channels that came back empty.
# usage: cloud/grab.sh <site> <part> <parts>      (run from the repository root, iptv-org/epg in ./iptv-epg)
site="$1"; part="${2:-1}"; parts="${3:-1}"
root="$(pwd)"
work="$root/work"; mkdir -p "$work"
if [ "$site" = "translate" ]; then
  # foreign guides -> English (offline models). Never fails the build: the previous dictionaries stay in use.
  python3 -m venv /tmp/trenv && /tmp/trenv/bin/pip install -q ctranslate2 sentencepiece || echo "pip failed"
  timeout 70m /tmp/trenv/bin/python cloud/translate_en.py --langs "$part" --work "$work" || echo "translation stopped"
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
