#!/bin/bash
# build the receiver files from the grabbed guides and publish them to the "epg" branch.
# needs: work/ (grab outputs), iptv-epg/sites (channel lists), ziko/configs (service references)
set -e
python3 tools/make_catalog.py --iptv iptv-epg --catalog plugin/SaadEPG/channels.json --cloud cloud/channels
timeout 15m python3 cloud/official.py --work work || echo "official sources skipped"
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
python3 cloud/sattv.py merge work work/sattv_ar.xml work/sattv_en.xml || echo "sat.tv merge skipped"
python3 cloud/build.py --work work --out public --catalog plugin/SaadEPG/channels.json --tr public/tr 2>&1 | tee public/tr/build.txt
[ "${PIPESTATUS[0]}" = "0" ] || exit 1
cp version.json public/ 2>/dev/null || true
timeout 10m python3 cloud/probe.py --out public || echo "probe skipped"   # raw official pages for new readers
cp cloud/linked_refs.json public/linked_refs.json 2>/dev/null || echo '{}' > public/linked_refs.json
python3 tools/make_refs.py --src ziko/configs --out public/refs.json && gzip -9 -f public/refs.json || true

# English titles of Arabic-region guides -> Arabic (offline translator, no daily limit). Never blocks publishing.
if [ "${TRANSLATE:-1}" = "1" ]; then
  pip install -q argostranslate || echo "argostranslate not installed"
  curl -fsSL "https://raw.githubusercontent.com/${GITHUB_REPOSITORY}/epg/tr_ar.json.gz" -o prev_tr.json.gz || true
  timeout 40m python3 cloud/translate_cloud.py --guides public/guides --prev prev_tr.json.gz \
      --out public/tr_ar.json.gz --budget 1800 || echo "translation skipped"
  [ -f public/tr_ar.json.gz ] || cp prev_tr.json.gz public/tr_ar.json.gz 2>/dev/null || true
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
