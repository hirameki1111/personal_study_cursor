"""AccountManager ― 잔고·매수가능 조회, 포지션 정합성 reconcile (Phase 3).

공식 OpenAPI 명세(v1.1.5) 확정 사항:
- GET /api/v1/accounts           → result: [{accountNo, accountSeq, accountType}]
  * X-Tossinvest-Account 헤더에는 계좌번호가 아닌 accountSeq(정수)를 사용
  * 현재 BROKERAGE(종합매매) 계좌만 반환
- GET /api/v1/holdings           → result: HoldingsOverview{..., items[]}
- GET /api/v1/buying-power?currency= → result: {currency, cashBuyingPower}
- GET /api/v1/sellable-quantity?symbol= → result: {sellableQuantity}
- GET /api/v1/commissions        → result: [{marketCountry, commissionRate(%), ...}]
- 수량·금액은 decimal 문자열 (US 주식은 소수점 수량 가능)

정합성 원칙 (Step 3-3): 불일치 시 halted=True, 자동 보정 금지(수동 원인 규명).
"""

import httpx
from loguru import logger

from src.auth.auth_manager import AuthManager, BASE_URL
from src.core.exceptions import AccountError


class AccountManager:
    def __init__(self, auth: AuthManager,
                 http: httpx.Client | None = None,
                 account_no: str | None = None) -> None:
        """account_no(.env ACCOUNT_NO)는 복수 계좌 중 선택용 ― 없으면 첫 BROKERAGE 계좌."""
        self._auth = auth
        self._http = http or httpx.Client(base_url=BASE_URL, timeout=10.0)
        self._account_no = account_no
        self._account_seq: int | None = None
        self.halted: bool = False        # 정합성 불일치 시 True

    # ── 공통 ──────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None,
             with_account: bool = True) -> dict | list:
        headers = self._auth.auth_header()
        if with_account:
            headers["X-Tossinvest-Account"] = str(self.account_seq)
        try:
            resp = self._http.get(path, params=params or {}, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300]   # error.requestId 포함
            logger.error(f"계좌 조회 거부 HTTP {e.response.status_code}: {body}")
            raise AccountError(
                f"account request rejected: HTTP {e.response.status_code}"
            ) from e
        except httpx.HTTPError as e:
            logger.error(f"계좌 조회 실패({type(e).__name__})")
            raise AccountError(
                f"account request failed: {type(e).__name__}") from e
        return resp.json()["result"]

    # ── 계좌 식별 ─────────────────────────────────────────

    @property
    def account_seq(self) -> int:
        if self._account_seq is None:
            self._account_seq = self._resolve_account_seq()
        return self._account_seq

    def _resolve_account_seq(self) -> int:
        accounts = self._get("/api/v1/accounts", with_account=False)
        brokerage = [a for a in accounts if a.get("accountType") == "BROKERAGE"]
        if self._account_no:
            matched = [a for a in brokerage
                       if a.get("accountNo") == self._account_no]
            if not matched:
                raise AccountError(
                    "ACCOUNT_NO와 일치하는 BROKERAGE 계좌 없음 "
                    "(.env ACCOUNT_NO와 계좌 목록 대조 필요)")
            brokerage = matched
        if not brokerage:
            raise AccountError("사용 가능한 BROKERAGE 계좌 없음")
        seq = brokerage[0]["accountSeq"]
        logger.info(f"계좌 확정: accountSeq={seq}")   # 계좌번호는 로깅 금지
        return seq

    # ── 조회 (Step 3-2) ───────────────────────────────────

    def get_holdings(self, symbol: str | None = None) -> dict:
        """보유 주식 조회. HoldingsOverview(요약 + items[]) 반환."""
        params = {"symbol": symbol} if symbol else None
        return self._get("/api/v1/holdings", params)

    def get_buying_power(self, currency: str = "KRW") -> float:
        """현금 기반 매수 가능 금액."""
        result = self._get("/api/v1/buying-power", {"currency": currency})
        return float(result["cashBuyingPower"])

    def get_sellable(self, symbol: str) -> float:
        """판매 가능 수량 (US는 소수점 가능)."""
        result = self._get("/api/v1/sellable-quantity", {"symbol": symbol})
        return float(result["sellableQuantity"])

    def get_commissions(self) -> list[dict]:
        """시장별 수수료율. commissionRate는 % 단위 문자열 (예: '0.015' = 0.015%)."""
        return self._get("/api/v1/commissions")

    # ── 정합성 (Step 3-3) ─────────────────────────────────

    @staticmethod
    def parse_positions(holdings: dict) -> dict[str, float]:
        """HoldingsOverview.items[] → {symbol: quantity} 포지션 dict."""
        return {it["symbol"]: float(it["quantity"])
                for it in holdings.get("items", [])}

    def reconcile(self, system_positions: dict[str, float]) -> bool:
        """실잔고와 시스템 포지션 대조. 불일치 시 halt (자동 보정 금지)."""
        real = self.parse_positions(self.get_holdings())
        if real != system_positions:
            self.halted = True
            logger.error(f"정합성 불일치 ― 거래 중단 "
                         f"(실잔고={real}, 시스템={system_positions})")
            return False
        return True
