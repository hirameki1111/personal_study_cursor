"""Phase 4 OrderExecutor 테스트 (제안서 4.4 테스트 플랜).

- dry-run 주문: 외부 전송 0건·의사결정 로깅
- 동일 client_oid 재전송: OrderError(중복 차단)
- 4xx 확정 거부: 즉시 실패(재시도 없음) / 5xx·네트워크: 재시도 후 OrderError
- 허용 외 상태 전이: 전이 거부
- 수수료 사전 계산: 매수(수수료)·매도(수수료+거래세)
"""

from unittest.mock import MagicMock

import httpx
import pytest

from src.core.exceptions import OrderError
from src.order.order_executor import (LocalOrder, OrderExecutor, OrderStatus)


@pytest.fixture
def ex():
    auth = MagicMock()
    auth.auth_header.return_value = {"Authorization": "Bearer tok"}
    account = MagicMock()
    account.account_seq = 7
    http = MagicMock(spec=httpx.Client)
    return OrderExecutor(auth, account, http=http, dry_run=True)


def _resp(result, status=200):
    r = MagicMock()
    r.json.return_value = {"result": result}
    if status >= 400:
        r.raise_for_status.side_effect = httpx.HTTPStatusError(
            "err", request=MagicMock(),
            response=MagicMock(status_code=status, text='{"error":{}}'))
    else:
        r.raise_for_status.return_value = None
    return r


# ── dry-run (Step 4-2) ────────────────────────────────────

def test_dry_run_no_external_post(ex):
    out = ex.place_order("005930", "BUY", 1, price=70000)
    assert out["status"] == "DRY_RUN"
    ex._http.post.assert_not_called()          # 외부 전송 0건


def test_dry_run_payload_matches_spec(ex):
    out = ex.place_order("005930", "BUY", 1, price=70000)
    p = out["payload"]
    assert p["symbol"] == "005930" and p["side"] == "BUY"
    assert p["orderType"] == "LIMIT" and p["timeInForce"] == "DAY"
    assert p["quantity"] == "1" and p["price"] == "70000"
    assert len(p["clientOrderId"]) == 36       # 명세 상한 36자


# ── 입력 검증 ─────────────────────────────────────────────

def test_limit_requires_price(ex):
    with pytest.raises(OrderError, match="price 필수"):
        ex.place_order("005930", "BUY", 1, order_type="LIMIT")


def test_market_forbids_price(ex):
    with pytest.raises(OrderError, match="지정 불가"):
        ex.place_order("005930", "BUY", 1, price=70000, order_type="MARKET")


def test_invalid_side_and_qty(ex):
    with pytest.raises(OrderError):
        ex.place_order("005930", "HOLD", 1, price=70000)
    with pytest.raises(OrderError):
        ex.place_order("005930", "BUY", 0, price=70000)


# ── 멱등성·재시도 (Step 4-3) ──────────────────────────────

def test_duplicate_client_oid_blocked(ex):
    ex.dry_run = False
    ex._http.post.return_value = _resp({"orderId": "OID1",
                                        "clientOrderId": "my-1"})
    ex.place_order("005930", "BUY", 1, price=70000, client_oid="my-1")
    with pytest.raises(OrderError, match="중복"):
        ex.place_order("005930", "BUY", 1, price=70000, client_oid="my-1")


def test_4xx_rejection_no_retry(ex):
    ex.dry_run = False
    ex._http.post.return_value = _resp(None, status=422)
    with pytest.raises(OrderError, match="HTTP 422"):
        ex.place_order("005930", "BUY", 1, price=70000)
    assert ex._http.post.call_count == 1       # 확정 거부 ― 재시도 금지


def test_5xx_retries_then_order_error(ex):
    ex.dry_run = False
    ex._http.post.return_value = _resp(None, status=500)
    with pytest.raises(OrderError, match="재시도 한도"):
        ex.place_order("005930", "BUY", 1, price=70000)
    assert ex._http.post.call_count == 2       # max_retry=2


def test_network_error_retries(ex):
    ex.dry_run = False
    ex._http.post.side_effect = httpx.ConnectError("down")
    with pytest.raises(OrderError, match="재시도 한도"):
        ex.place_order("005930", "BUY", 1, price=70000)
    assert ex._http.post.call_count == 2


def test_live_order_tracked(ex):
    ex.dry_run = False
    ex._http.post.return_value = _resp({"orderId": "OID1",
                                        "clientOrderId": "c1"})
    ex.place_order("005930", "BUY", 1, price=70000, client_oid="c1")
    assert ex.orders["c1"].order_id == "OID1"
    assert ex.orders["c1"].status == OrderStatus.PENDING
    assert len(ex.open_orders()) == 1


# ── 상태머신 (Step 4-1) ───────────────────────────────────

def test_allowed_transitions():
    o = LocalOrder("c", "005930", "BUY", 1, "LIMIT")
    o.transition(OrderStatus.PARTIAL_FILLED)
    o.transition(OrderStatus.FILLED)
    assert o.status == OrderStatus.FILLED
    assert o.history == [OrderStatus.PENDING, OrderStatus.PARTIAL_FILLED]


def test_terminal_state_no_reverse_transition():
    o = LocalOrder("c", "005930", "BUY", 1, "LIMIT")
    o.transition(OrderStatus.FILLED)
    with pytest.raises(OrderError, match="허용되지 않은"):
        o.transition(OrderStatus.PENDING)      # 역전이 금지


def test_cancel_reject_returns_to_previous_state():
    o = LocalOrder("c", "005930", "BUY", 1, "LIMIT")
    o.transition(OrderStatus.PENDING_CANCEL)
    o.transition(OrderStatus.PENDING)          # 취소 거부 → 원상태 복귀(명세)
    assert o.status == OrderStatus.PENDING


def test_sync_status_applies_remote(ex):
    ex.dry_run = False
    ex._http.post.return_value = _resp({"orderId": "OID1",
                                        "clientOrderId": "c1"})
    ex.place_order("005930", "BUY", 1, price=70000, client_oid="c1")
    ex._http.get.return_value = _resp({"orderId": "OID1",
                                       "status": "FILLED"})
    assert ex.sync_status("c1") == OrderStatus.FILLED
    assert len(ex.open_orders()) == 0


def test_sync_status_unknown_status_kept(ex):
    ex.dry_run = False
    ex._http.post.return_value = _resp({"orderId": "OID1",
                                        "clientOrderId": "c1"})
    ex.place_order("005930", "BUY", 1, price=70000, client_oid="c1")
    ex._http.get.return_value = _resp({"orderId": "OID1",
                                       "status": "SOME_NEW_STATUS"})
    assert ex.sync_status("c1") == OrderStatus.PENDING   # unknown ― 유지


# ── 취소 ──────────────────────────────────────────────────

def test_duplicate_cancel_blocked(ex):
    ex.dry_run = False
    ex._http.post.return_value = _resp({"orderId": "OID1",
                                        "clientOrderId": "c1"})
    ex.place_order("005930", "BUY", 1, price=70000, client_oid="c1")
    ex._http.post.return_value = _resp({"orderId": "OID2"})
    ex.cancel_order("c1")
    assert ex.orders["c1"].status == OrderStatus.PENDING_CANCEL
    with pytest.raises(OrderError, match="중복 취소"):
        ex.cancel_order("c1")


# ── 비용 사전계산 (Step 4-4) ──────────────────────────────

def test_estimate_cost_buy_includes_fee(ex):
    ex.commission_rate = 0.00015
    cost = ex.estimate_cost("BUY", 10, 70000)
    assert cost == pytest.approx(700000 * 1.00015)


def test_estimate_cost_sell_deducts_fee_and_tax(ex):
    ex.commission_rate = 0.00015
    ex.tax_rate_sell = 0.0015
    proceeds = ex.estimate_cost("SELL", 10, 70000)
    assert proceeds == pytest.approx(700000 * (1 - 0.00015 - 0.0015))
