# toss-autotrader

토스증권 Open API 기반 규칙형(Rule-based) 자동매매 시스템.
「토스증권 자동매매 시스템 단계별 구축제안서 v3.0」에 따라 Phase 0~10을 점진 구축한다.

> **안전 원칙 (필독)**
> - dry-run → 백테스트 → 페이퍼트레이딩을 실거래보다 먼저 완성한다. 순서 역전 금지.
> - 매 Phase는 완료기준(DoD) 전 항목 통과 후 다음 Phase에 착수한다.
> - 실거래 진입은 G4(소액 1주)·G5(본격 운용) 게이트 통과 시에만 허용한다.
> - 모든 코드 템플릿의 엔드포인트·파라미터·응답키는 공식 OpenAPI JSON
>   (developers.tossinvest.com) 대조 검증 후 확정한다.
> - 본 시스템은 수익을 보장하지 않으며, 실거래 손실 책임은 운용자 본인에게 귀속된다.

## 디렉토리 구조

```
toss-autotrader/
├── .env / .env.example        # 시크릿 (.env는 git 제외)
├── .gitignore
├── pyproject.toml / requirements.txt
├── config/
│   ├── settings.yaml           # 전략·한도·종목·주기 (부록 C)
│   └── strategies/             # 전략별 파라미터
├── src/
│   ├── core/                   # 설정로더·예외·모델·HTTP 공통클라이언트
│   ├── auth/  market/  account/  order/
│   ├── strategy/  risk/  backtest/  monitor/
│   └── orchestrator.py         # 메인 실행
├── tests/                      # 단위·통합 테스트 (pytest)
├── scripts/                    # 운영 스크립트 (백업·헬스체크)
├── logs/  data/                # 로그·캔들 캐시 (git 제외)
└── deploy/                     # systemd·배포 산출물
```

## 시작하기 (Phase 0)

```bash
cd toss-autotrader

# 1. 가상환경 구성 (Python 3.11+)
python --version
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 2. 의존 라이브러리 설치
pip install -r requirements.txt

# 3. 시크릿 설정 (.env는 git에 절대 커밋하지 않는다)
cp .env.example .env
# .env에 TOSS_CLIENT_ID / TOSS_CLIENT_SECRET / ACCOUNT_NO 기재

# 4. 골격 검증
pytest
git status   # .env가 표시되지 않는지 확인
```

## 실행 모드

실행 모드는 `config/settings.yaml`의 `runtime.mode` **1곳에서만** 전환한다.

| 모드 | 설명 | 진입 조건 |
|---|---|---|
| `dry_run` | 외부 주문 전송 없이 의사결정만 로깅 | 기본값 |
| `paper` | 실시간 시세 기반 가상 체결 | G2 통과 |
| `live` | 실거래 | G4·G5 게이트 통과 |

## 구축 로드맵

| Phase | 명칭 | 산출물 | 게이트 | 상태 |
|---|---|---|---|---|
| 0 | 환경 준비 | API 키·가상환경·프로젝트 골격 | ― | ✅ |
| 1 | 인증·토큰 | `src/auth` AuthManager | G1 | ✅ 실발급 검증 |
| 2 | 시세 수집 | `src/market` MarketDataClient | G1 | ✅ 실호출 검증 |
| 3 | 계좌·잔고 | `src/account` AccountManager | G1 | ✅ 실호출 검증 |
| 4 | 주문(dry-run) | `src/order` OrderExecutor | G1 | ✅ dry-run 검증 |
| 5 | 전략 엔진 | `src/strategy` StrategyEngine | G2 | ✅ |
| 6 | 리스크 관리 | `src/risk` RiskManager | G2 | ✅ |
| 7 | 백테스트·페이퍼 | `src/backtest` Backtester/PaperBroker | G2·G3 | ✅ 실데이터 검증 |
| 8 | 모니터링·알림 | `src/monitor` Logger/Notifier | ― | ✅ |
| 9 | 오케스트레이션 | `src/orchestrator.py` | G4 | ✅ dry-run 통합 검증 |
| 10 | 운영·배포 | `deploy/`·`docs/operations.md` | G5 | 운용 단계 진입 |

게이트 통과 기록: `docs/gates.md` / 운영 절차: `docs/operations.md`

## 실행

```bash
python -m src.orchestrator --once   # 1회 사이클 검증 (거래시간 무시)
python -m src.orchestrator          # 상시 루프 (정규장만 거래)
```

모든 엔드포인트·응답키는 `spec/openapi.json`(공식 명세 v1.1.5) 대조로 확정됨.

## 주의사항

- Secret은 발급 시 1회만 노출된다. 평문 메모·메신저 전송 금지.
- `logs/`, `data/`는 git에서 제외된다 (`.gitkeep`만 추적).
- 정합성(reconcile) 불일치 시 자동 보정 금지 ― 거래 중단 후 수동 원인 규명.
- 운용 중 동일 계좌 수동 매매 금지 (정합성 불일치 원인).
