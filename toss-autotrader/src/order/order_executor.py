"""OrderExecutor ― 주문 실행·취소·상태추적, dry-run 우선 (Phase 4).

공식 OpenAPI 명세(v1.1.5) 확정 사항:
- POST /api/v1/orders (X-Tossinvest-Account: accountSeq)
  body: {clientOrderId?, symbol, side(BUY|SELL), orderType(LIMIT|MARKET),
         timeInForce(DAY|CLS, 기본 DAY), quantity(decimal 문자열), price?}
  * clientOrderId = 멱등성 키. 최대 36자, [a-zA-Z0-9-_]. 10분간 유효.
    동일 값 재요청 시 이전 주문 결과 재반환 → uuid4(36자)로 생성
- GET /api/v1/orders/{orderId} → Order(status·execution.filledQuantity 등)
- POST /api/v1/orders/{orderId}/cancel → 새 orderId 발급(원주문과 다름)
- 상태: PENDING → PARTIAL_FILLED/FILLED/CANCELED/REJECTED,
  취소·정정은 PENDING_CANCEL/PENDING_REPLACE 경유, 거부 시 원상태 복귀

안전 원칙 (Step 4-2): dry_run=True면 외부 전송 0건 ― 검증·비용계산만 수행.
실거래 경로는 G4 게이트 전까지 settings mode로 비활성 유지.
"""

import uuid
from dataclasses import dataclass, field
from enum import Enum

import httpx
from loguru import logger

from src.auth.auth_manager import AuthManager, BASE_URL
from src.core.exceptions import OrderError


class OrderStatus(str, Enum):
    """공식 OrderStatus enum (10종). unknown 값 허용 구현 권장."""
    PENDING = "PENDING"
    PENDING_CANCEL = "PENDING_CANCEL"
    PENDING_REPLACE = "PENDING_REPLACE"
    PARTIAL_FILLED = "PARTIAL_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    CANCEL_REJECTED = "CANCEL_REJECTED"
    REPLACE_REJECTED = "REPLACE_REJECTED"
    REPLACED = "REPLACED"


# 허용 상태 전이 (Step 4-1: 역전이 금지 ― 종결 상태에서의 전이 불가)
ALLOWED = {
    OrderStatus.PENDING: {OrderStatus.PARTIAL_FILLED, OrderStatus.FILLED,
                          OrderStatus.PENDING_CANCEL,
                          OrderStatus.PENDING_REPLACE,
                          OrderStatus.CANCELED, OrderStatus.REJECTED},
    OrderStatus.PARTIAL_FILLED: {OrderStatus.FILLED,
                                 OrderStatus.PENDING_CANCEL,
                                 OrderStatus.PENDING_REPLACE,
                                 OrderStatus.CANCELED, OrderStatus.REJECTED},
    # 취소·정정 거부 시 원주문은 이전 상태로 복귀 (명세)
    OrderStatus.PENDING_CANCEL: {OrderStatus.CANCELED, OrderStatus.PENDING,
                                 OrderStatus.PARTIAL_FILLED},
    OrderStatus.PENDING_REPLACE: {OrderStatus.REPLACED, OrderStatus.PENDING,
                                  OrderStatus.PARTIAL_FILLED},
    # FILLED / CANCELED / REJECTED / REPLACED / *_REJECTED: 종결 ― 전이 없음
}

# 매도 시 거래세율 (한국). API 미제공 ― 세법 개정 여부 확인 필요 [확인 필요]
DEFAULT_TAX_RATE_SELL_KR = 0.0015
# 수수료율은 GET /api/v1/commissions로 실값 조회 가능 (AccountManager)
DEFAULT_COMMISSION_RATE = 0.00015


@dataclass
class LocalOrder:
    """로컬 주문 추적 레코드 ― 전이 규칙 강제."""
    client_oid: str
    symbol: str
    side: str
    quantity: float
    order_type: str
    price: float | None = None
    order_id: str | None = None
    status: OrderStatus = OrderStatus.PENDING
    history: list[OrderStatus] = field(default_factory=list)

    def transition(self, new: OrderStatus) -> None:
        if new == self.status:
            return
        if new not in ALLOWED.get(self.status, set()):
            raise OrderError(
                f"허용되지 않은 상태 전이: {self.status.value} → {new.value}")
        self.history.append(self.status)
        self.status = new


class OrderExecutor:
    def __init__(self, auth: AuthManager, account,
                 http: httpx.Client | None = None,
                 dry_run: bool = True, max_retry: int = 2,
                 commission_rate: float = DEFAULT_COMMISSION_RATE,
                 tax_rate_sell: float = DEFAULT_TAX_RATE_SELL_KR) -> None:
        """account: AccountManager (accountSeq 해석·헤더용)."""
        self._auth, self._account = auth, account
        self._http = http or httpx.Client(base_url=BASE_URL, timeout=10.0)
        self.dry_run, self._max_retry = dry_run, max_retry
        self.commission_rate, self.tax_rate_sell = commission_rate, tax_rate_sell
        self.orders: dict[str, LocalOrder] = {}    # client_oid → LocalOrder
        self._sent: set[str] = set()               # idempotency (전송 완료)
        self._cancel_requested: set[str] = set()   # 중복 취소 방지

    def _headers(self) -> dict[str, str]:
        h = self._auth.auth_header()
        h["X-Tossinvest-Account"] = str(self._account.account_seq)
        return h

    # ── 비용 사전계산 (Step 4-4) ──────────────────────────

    def estimate_cost(self, side: str, qty: float, price: float) -> float:
        """예상 총비용. 매수=금액+수수료, 매도=금액-수수료-거래세(수취액)."""
        gross = qty * price
        fee = gross * self.commission_rate
        if side == "BUY":
            return gross + fee
        return gross - fee - gross * self.tax_rate_sell

    # ── 주문 (Step 4-2·4-3) ───────────────────────────────

    def place_order(self, symbol: str, side: str, qty: float,
                    price: float | None = None,
                    order_type: str = "LIMIT",
                    time_in_force: str = "DAY",
                    client_oid: str | None = None) -> dict:
        if side not in ("BUY", "SELL"):
            raise OrderError(f"잘못된 side: {side}")
        if order_type == "LIMIT" and price is None:
            raise OrderError("LIMIT 주문에는 price 필수")
        if order_type == "MARKET" and price is not None:
            raise OrderError("MARKET 주문에는 price 지정 불가")
        if qty <= 0:
            raise OrderError(f"잘못된 수량: {qty}")

        client_oid = client_oid or str(uuid.uuid4())   # 36자 ― 명세 상한과 일치
        if client_oid in self._sent:
            raise OrderError(f"중복 클라이언트 주문 ID: {client_oid[:8]}...")

        payload: dict = {"clientOrderId": client_oid, "symbol": symbol,
                         "side": side, "orderType": order_type,
                         "timeInForce": time_in_force,
                         "quantity": str(qty)}
        if price is not None:
            payload["price"] = str(price)

        if price is not None:
            est = self.estimate_cost(side, qty, price)
            logger.info(f"예상 {'비용' if side == 'BUY' else '수취액'}: "
                        f"{est:,.2f} (수수료율 {self.commission_rate:.5%})")

        if self.dry_run:
            logger.info(f"[DRY-RUN] 주문 시뮬레이션: {payload}")
            return {"status": "DRY_RUN", "payload": payload}

        return self._send_order(payload, client_oid, symbol, side,
                                qty, order_type, price)

    def _send_order(self, payload: dict, client_oid: str, symbol: str,
                    side: str, qty: float, order_type: str,
                    price: float | None) -> dict:
        last_err: Exception | None = None
        for attempt in range(1, self._max_retry + 1):
            try:
                r = self._http.post("/api/v1/orders", json=payload,
                                    headers=self._headers())
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                if code < 500:
                    # 4xx = 확정 거부 ― 재시도 금지 (중복 주문 위험)
                    logger.error(f"주문 거부 HTTP {code}: "
                                 f"{e.response.text[:300]}")
                    raise OrderError(f"order rejected: HTTP {code}") from e
                last_err = e
                logger.warning(f"주문 시도 {attempt} 실패: HTTP {code}")
            except httpx.HTTPError as e:
                # 네트워크 오류: 전송·미전송 불명(모호 상태) ―
                # 동일 clientOrderId 재시도는 멱등성으로 안전 (명세: 10분 유효)
                last_err = e
                logger.warning(f"주문 시도 {attempt} 실패: {type(e).__name__}")
            else:
                self._sent.add(client_oid)
                result = r.json()["result"]
                order = LocalOrder(client_oid=client_oid, symbol=symbol,
                                   side=side, quantity=qty,
                                   order_type=order_type, price=price,
                                   order_id=result["orderId"])
                self.orders[client_oid] = order
                logger.info(f"[LIVE] 주문 접수: orderId={result['orderId'][:12]}...")
                return result
        raise OrderError("주문 실패 ― 재시도 한도 초과(중단·알림)") from last_err

    # ── 상태 추적 (Step 4-5) ──────────────────────────────

    def get_order(self, order_id: str) -> dict:
        try:
            r = self._http.get(f"/api/v1/orders/{order_id}",
                               headers=self._headers())
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise OrderError(f"주문 조회 실패: {type(e).__name__}") from e
        return r.json()["result"]

    def sync_status(self, client_oid: str) -> OrderStatus:
        """서버 상태를 조회해 로컬 상태머신에 반영 (허용 전이만)."""
        order = self.orders.get(client_oid)
        if order is None or order.order_id is None:
            raise OrderError(f"미등록 주문: {client_oid[:8]}...")
        remote = self.get_order(order.order_id)
        try:
            new_status = OrderStatus(remote["status"])
        except ValueError:
            logger.warning(f"알 수 없는 주문 상태 수신: {remote['status']} "
                           "― 상태 유지(명세: unknown 허용)")
            return order.status
        order.transition(new_status)
        return order.status

    def cancel_order(self, client_oid: str) -> dict:
        """주문 취소. 취소로 새 orderId가 발급됨 (원주문과 다름)."""
        order = self.orders.get(client_oid)
        if order is None or order.order_id is None:
            raise OrderError(f"미등록 주문: {client_oid[:8]}...")
        if order.order_id in self._cancel_requested:
            raise OrderError("중복 취소 요청 차단")
        if self.dry_run:
            logger.info(f"[DRY-RUN] 취소 시뮬레이션: {order.order_id[:12]}...")
            return {"status": "DRY_RUN"}
        try:
            r = self._http.post(f"/api/v1/orders/{order.order_id}/cancel",
                                json={}, headers=self._headers())
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise OrderError(
                f"취소 거부: HTTP {e.response.status_code}") from e
        except httpx.HTTPError as e:
            raise OrderError(f"취소 실패: {type(e).__name__}") from e
        self._cancel_requested.add(order.order_id)
        order.transition(OrderStatus.PENDING_CANCEL)
        return r.json()["result"]

    def open_orders(self) -> list[LocalOrder]:
        """미종결 주문 목록 (Risk 중복주문 검사·타임아웃 취소 판단용)."""
        terminal = {OrderStatus.FILLED, OrderStatus.CANCELED,
                    OrderStatus.REJECTED, OrderStatus.REPLACED}
        return [o for o in self.orders.values() if o.status not in terminal]
