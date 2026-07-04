# 운영 절차서 (Phase 10)

제안서 10장·부록 E/F/G 기반 운영 가이드.

## 1. 배포 (Step 10-1~10-2)

상시 가동 서버(클라우드 VM·미니PC, KST·NTP 동기화) 기준:

```bash
# 배포 위치
sudo mkdir -p /opt/toss-autotrader
sudo rsync -a --exclude .venv --exclude logs --exclude data \
    ./ /opt/toss-autotrader/
cd /opt/toss-autotrader
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp /안전한/경로/.env .env && chmod 600 .env      # 시크릿 권한 600

# systemd 등록 (비정상 종료 시 자동 재기동)
sudo cp deploy/toss-autotrader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now toss-autotrader
journalctl -u toss-autotrader -f                  # 로그 확인
```

- **서버 IP를 개발자 포털 허용 IP에 등록**할 것 (미등록 시 403)
- 재기동 시 reconcile이 선행됨 (live 모드는 불일치 시 기동 거부)
- 클라이언트당 유효 토큰 1개 ― 서버 가동 중 로컬 PC에서 check_* 스크립트
  실행 금지 (서버의 토큰이 무효화됨)

## 2. 일상 점검 (부록 G)

| 주기 | 항목 |
|---|---|
| 매일 장 시작 전 | 토큰 발급 정상 · 휴장일 여부 · reconcile 일치 · kill switch 리셋(`reset_daily`) |
| 매일 장 중 | 시세 지연 여부 · 미체결 적체 · 알림 채널 정상 |
| 매일 장 종료 후 | 일일 리포트 검토(체결·손익·거부·오류) · 손실 한도 근접 여부 |
| 매주 | `scripts/backup.sh` 백업 · 에러 추세 · 디스크/메모리 |
| 매월 | 전략 성과(아웃오브샘플 대비) · 한도 적정성 · 복구 리허설 |
| 수시 | API 스펙 변경 공지(`spec/openapi.json` 재다운로드 대조) · 보안 업데이트 |

## 3. 긴급 정지·복구 (부록 E)

**긴급 정지**
1. `sudo systemctl stop toss-autotrader` (또는 kill switch 발동 확인)
2. 신규 주문 차단 확인 (로그·DB orders)
3. 미체결 주문 파악 → 취소 여부 수동 판단 (토스증권 앱 병행)

**상태 복구 (재기동)**
1. 재기동 시 DB 미체결·포지션 로드 → 실잔고 reconcile 자동 수행
2. 일치 → 정상 기동 / 불일치 → halted 유지, **자동 보정 금지**·수동 규명

**버전 롤백**
1. `git tag`(배포 직전) 기준 코드 복귀 → 설정 이력 대조
2. dry-run 1사이클(`python -m src.orchestrator --once`) 검증 후 재배포

**재개 조건 (3개 모두 충족 후에만, 자동 재개 금지)**
① 정합성 일치 ② 원인 규명·조치 완료 ③ dry-run 회귀 통과

## 4. 장애 대응 (부록 F 요약)

| 증상 | 즉시 조치 |
|---|---|
| 401 Unauthorized | 토큰 재발급(자동) → 반복 시 거래 중단·키 점검 |
| 403 IP not allowed | 서버 공인 IP 변경 여부 확인 → 포털 재등록 |
| 429 Too Many Requests | 자동 백오프 ― 반복 시 폴링 주기 상향 |
| 5xx 연속 | 헬스체크가 연속 3회 실패 시 안전 종료 → 미체결 앱 확인 |
| 주문 후 상태 불명 | **재전송 금지** ― clientOrderId 멱등성으로 상태 조회 우선 |
| 정합성 불일치 | 거래 중단(자동) → 수동 규명 (운용 중 수동 매매 금지) |
| 프로세스 다운 | systemd 자동 재기동 → 복구 로그·reconcile 확인 |

## 5. 게이트 진행 절차 (G3→G5)

1. **G3 (페이퍼 10거래일)**: `settings.yaml` mode를 `paper`로 → 상시 가동
   → 매일 일일 리포트 검토·기록 → 10거래일 무오류 시 `docs/gates.md` 기입
2. **G4 (소액 1주)**: mode `live` + 한도 최소화(1주) → 매수→체결→매도
   1사이클 → 주문·체결·잔고 3자 대사, 수수료·세금 실제값 대조
   (거래세 상수 0.0015 검증·확정) → 시장 급변동일(지수 ±3%) 회피
3. **G5 (20거래일)**: 소액 운용 무사고 + 손실한도 위반 0건 + 복구 리허설
   1회 → 통과 후 한도 단계 상향(1회 50% 이내, 상향 후 1주 집중 관찰)

## 6. 스크립트 목록

| 스크립트 | 용도 |
|---|---|
| `scripts/check_auth.py` | 토큰 발급 검증 (Phase 1) |
| `scripts/check_market.py` | 시세 조회 검증 (Phase 2) |
| `scripts/check_account.py` | 계좌·잔고 검증 (Phase 3) |
| `scripts/check_order_dryrun.py` | dry-run 주문 검증 (Phase 4) |
| `scripts/run_backtest.py` | 실데이터 백테스트 (Phase 7) |
| `scripts/backup.sh` | 로그·DB 백업 (주 1회+) |
| `scripts/healthcheck.sh` | 프로세스·상태 점검 |
