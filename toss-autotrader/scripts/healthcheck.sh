#!/usr/bin/env bash
# 운영 헬스체크 (Phase 10): systemd 상태 + 오늘 로그의 오류 건수 요약
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== 프로세스 상태 ==="
systemctl is-active toss-autotrader 2>/dev/null || echo "(systemd 서비스 미등록 ― 수동 실행 환경)"

echo "=== 오늘 로그 오류 ==="
TODAY_LOG="${ROOT}/logs/trader_$(date +%Y-%m-%d).log"
if [[ -f "${TODAY_LOG}" ]]; then
  ERRORS=$(grep -c "| ERROR" "${TODAY_LOG}" || true)
  echo "ERROR ${ERRORS}건 (${TODAY_LOG})"
  grep "| ERROR" "${TODAY_LOG}" | tail -5 || true
else
  echo "(오늘 로그 파일 없음)"
fi

echo "=== DB 폴백 여부 ==="
if [[ -s "${ROOT}/logs/db_fallback.jsonl" ]]; then
  echo "경고: DB 폴백 기록 존재 ― DB 상태 점검 필요"
else
  echo "정상 (폴백 없음)"
fi
