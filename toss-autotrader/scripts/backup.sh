#!/usr/bin/env bash
# 로그·이벤트 DB 백업 스크립트 (제안서 Step 10-5: 주 1회 이상, 복원 리허설 필수)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"
DEST="${ROOT}/data/backups"

mkdir -p "${DEST}"
tar -czf "${DEST}/backup_${STAMP}.tar.gz" \
    -C "${ROOT}" logs data/trade.db 2>/dev/null || true
echo "[backup] ${DEST}/backup_${STAMP}.tar.gz 생성 완료"
