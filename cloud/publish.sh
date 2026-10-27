#!/bin/bash
# build the receiver files from the grabbed guides and publish them to the "epg" branch.
# needs: work/ (grab outputs), iptv-epg/sites (channel lists), ziko/configs (service references)
set -e
python3 tools/make_catalog.py --iptv iptv-epg --catalog plugin/SaadEPG/channels.json --cloud cloud/channels
mkdir -p public/tr
(timeout 15m python3 cloud/official.py --work work || echo "official sources skipped") 2>&1 | tee public/tr/official.txt
# English dictionaries of the foreign guides (made by the "translate" jobs; the previous ones when a job failed)
mkdir -p public/tr
for l in $(python3 -c "import json;print(' '.join(x for g in json.load(open('cloud/sources.json')).get('translate',{}).get('groups',[]) for x in g.split('+')))"); do
  if [ -s "work/out_tren_${l}.xml" ]; then
    mv "work/out_tren_${l}.xml" "public/tr/en_${l}.json.gz"
  else
    curl -fsSL "https://raw.githubusercontent.com/${GITHUB_REPOSITORY}/epg/tr/en_${l}.json.gz" -o "public/tr/en_${l}.json.gz" \
      || rm -f "public/tr/en_${l}.json.gz"
  fi
done
cat work/out_trlog_*.xml > public/tr/log.txt 2>/dev/null || true
mkdir -p public/tr/grab && for f in work/out_grablog_*.xml; do [ -f "$f" ] && tail -c 60000 "$f" > "public/tr/grab/$(basename "$f" .xml).txt"; done; true
(python3 cloud/sattv.py merge work work/sattv_ar.xml work/sattv_en.xml || echo "sat.tv merge skipped") 2>&1 | tee -a public/tr/official.txt
# the channel catalog follows iptv-org every run: channels the sites add appear by themselves
CAT=plugin/SaadEPG/channels.json
if python3 tools/make_catalog.py --iptv iptv-epg --catalog work/catalog_auto.json --cloud work/channels_auto >/dev/null 2>&1; then
  python3 - <<'PY' && CAT=work/catalog_auto.json
import json, sys
old = json.load(open('plugin/SaadEPG/channels.json', encoding='utf8'))
new = json.load(open('work/catalog_auto.json', encoding='utf8'))
if len(new) < 0.9 * len(old):
    sys.exit(1)
have = set((c['src'], c['id']) for c in new)
new += [c for c in old if (c['src'], c['id']) not in have]       # hand-made entries stay
added = len(new) - len(old)
json.dump(new, open('work/catalog_auto.json', 'w', encoding='utf8'), ensure_ascii=False, separators=(',', ':'))
print('catalog: %d channels (%+d new from iptv-org)' % (len(new), added))
PY
fi
python3 cloud/build.py --work work --out public --catalog "$CAT" --tr public/tr 2>&1 | tee public/tr/build.txt
[ "${PIPESTATUS[0]}" = "0" ] || exit 1
cp version.json public/ 2>/dev/null || true
timeout 10m python3 cloud/probe.py --out public || echo "probe skipped"   # raw official pages for new readers
cp cloud/linked_refs.json public/linked_refs.json 2>/dev/null || echo '{}' > public/linked_refs.json
python3 tools/make_refs.py --src ziko/configs --src cloud/extra_refs --out public/refs.json && gzip -9 -f public/refs.json || true

# Arabic for the English guides of Arabic channels: real titles learned from bilingual sites, then machine
# translation of the rest and of the descriptions. Never blocks publishing.
if [ "${TRANSLATE:-1}" = "1" ]; then
  pip install -q argostranslate || echo "argostranslate not installed"
  timeout 40m python3 cloud/translate_cloud.py --guides public/guides --tr public/tr --out-dir public \
      --budget 1800 2>&1 | tee -a public/tr/build.txt || echo "translation skipped"
  for f in tr_ar.json.gz tr_ar_desc.json.gz tr_ar_real.json.gz; do
    [ -s "public/$f" ] || curl -fsSL "https://raw.githubusercontent.com/${GITHUB_REPOSITORY}/epg/$f" -o "public/$f" || true
  done
fi

cd public
git init -q
git checkout -q -b epg
git config user.name "ArabEPG bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A
git commit -q -m "EPG $(date -u '+%Y-%m-%d %H:%M')"
git push -q -f "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" epg
cat status.json
