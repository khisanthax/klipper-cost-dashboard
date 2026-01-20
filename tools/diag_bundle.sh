#!/usr/bin/env bash
set -u
OUT="/tmp/kcd_diag_bundle.txt"
{
  echo "== KCD diagnostic bundle =="
  date
  echo ""
  echo "== git status =="
  git status -sb 2>&1 || true
  echo ""
  echo "== env =="
  env | sort || true
  echo ""
  echo "== kcd help =="
  python -m kcd -h 2>&1 || true
  echo ""
  echo "== kcd reports help =="
  python -m kcd reports -h 2>&1 || true
  echo ""
  echo "== kcd cache help =="
  python -m kcd cache -h 2>&1 || true
  echo ""
  echo "== kcd export help =="
  python -m kcd export -h 2>&1 || true
  echo ""
  echo "== kcd cache info =="
  python -m kcd cache info 2>&1 || true
  echo ""
  echo "== kcd reports parity =="
  python -m kcd reports parity --range 90d 2>&1 || true
  echo ""
  echo "== moonraker probe =="
  BASE_URL="${KCD_MOONRAKER_URL:-${MOONRAKER_URL:-}}"
  if [ -n "${BASE_URL}" ]; then
    HEADERS="/tmp/kcd_moonraker_headers.txt"
    BODY="/tmp/kcd_moonraker_body.json"
    rm -f "$HEADERS" "$BODY"
    if curl -sS -D "$HEADERS" -o "$BODY" "${BASE_URL%/}/server/info"; then
      BYTES="$(wc -c < "$BODY" | tr -d ' ')"
      HTTP_CODE="$(awk 'NR==1{print $2}' "$HEADERS")"
      CONTENT_TYPE="$(grep -i '^Content-Type:' "$HEADERS" | head -n1 | cut -d: -f2- | tr -d '\r' | xargs)"
      if [ "${BYTES}" -gt 0 ] && echo "$CONTENT_TYPE" | grep -qi "application/json"; then
        export KCD_BODY_PATH="$BODY"
        if python - <<'PY'; then
import json
import os
path = os.environ.get("KCD_BODY_PATH", "")
with open(path, "r", encoding="utf-8", errors="replace") as f:
    json.load(f)
print("Moonraker probe OK")
PY
          true
        else
          PREVIEW="$(head -c 200 "$BODY" | tr -d '\r')"
          echo "Moonraker history fetch failed: http_code=${HTTP_CODE} content_type=${CONTENT_TYPE} bytes=${BYTES} preview=${PREVIEW}"
        fi
      else
        PREVIEW="$(head -c 200 "$BODY" | tr -d '\r')"
        echo "Moonraker history fetch failed: http_code=${HTTP_CODE} content_type=${CONTENT_TYPE} bytes=${BYTES} preview=${PREVIEW}"
      fi
    else
      echo "Moonraker history fetch failed: curl error for ${BASE_URL%/}/server/info"
    fi

    if [ -n "${MOONRAKER_FILENAME:-}" ]; then
      echo ""
      echo "== moonraker metadata for ${MOONRAKER_FILENAME} =="
      HEADERS="/tmp/kcd_moonraker_meta_headers.txt"
      BODY="/tmp/kcd_moonraker_meta.json"
      rm -f "$HEADERS" "$BODY"
      if curl -sS -D "$HEADERS" -o "$BODY" "${BASE_URL%/}/server/files/metadata?filename=${MOONRAKER_FILENAME}"; then
        BYTES="$(wc -c < "$BODY" | tr -d ' ')"
        HTTP_CODE="$(awk 'NR==1{print $2}' "$HEADERS")"
        CONTENT_TYPE="$(grep -i '^Content-Type:' "$HEADERS" | head -n1 | cut -d: -f2- | tr -d '\r' | xargs)"
        if [ "${BYTES}" -gt 0 ] && echo "$CONTENT_TYPE" | grep -qi "application/json"; then
          export KCD_BODY_PATH="$BODY"
          if python - <<'PY'; then
import json
import os
path = os.environ.get("KCD_BODY_PATH", "")
with open(path, "r", encoding="utf-8", errors="replace") as f:
    json.load(f)
print("Moonraker metadata OK")
PY
            true
          else
            PREVIEW="$(head -c 200 "$BODY" | tr -d '\r')"
            echo "Moonraker metadata fetch failed: http_code=${HTTP_CODE} content_type=${CONTENT_TYPE} bytes=${BYTES} preview=${PREVIEW}"
          fi
        else
          PREVIEW="$(head -c 200 "$BODY" | tr -d '\r')"
          echo "Moonraker metadata fetch failed: http_code=${HTTP_CODE} content_type=${CONTENT_TYPE} bytes=${BYTES} preview=${PREVIEW}"
        fi
      else
        echo "Moonraker metadata fetch failed: curl error for ${BASE_URL%/}/server/files/metadata"
      fi
    fi
  else
    echo "Moonraker URL not set. Set KCD_MOONRAKER_URL or MOONRAKER_URL to probe."
  fi
} | tee "$OUT"

echo "Wrote $OUT"
