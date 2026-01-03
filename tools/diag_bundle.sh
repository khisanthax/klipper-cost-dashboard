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
} | tee "$OUT"

echo "Wrote $OUT"