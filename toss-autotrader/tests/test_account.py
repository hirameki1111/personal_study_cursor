"""Phase 3 AccountManager 테스트 (제안서 3.4 테스트 플랜).

- 보유·매수가능·매도가능 조회(모의): 정상 매핑
- 포지션 불일치 주입: halted=True·거래 중단
- 포지션 일치: 정상 통과(True)
- accountSeq 해석: BROKERAGE 선택·ACCOUNT_NO 매칭·부재 시 오류
"""

from unittest.mock import MagicMock

import httpx
import pytest

from src.account.account_manager import AccountManager
from src.core.exceptions import AccountError

ACCOUNTS = [{"accountNo": "12345678901", "accountSeq": 7,
             "accountType": "BROKERAGE"}]
HOLDINGS = {
    "totalPurchaseAmount": {"currency": "KRW", "amount": "6500000"},
    "items": [
        {"symbol": "005930", "name": "삼성전자", "quantity": "100",
         "lastPrice": "72000", "averagePurchasePrice": "65000",
         "currency": "KRW", "marketCountry": "KR"},
    ],
}


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


@pytest.fixture
def am():
    auth = MagicMock()
    auth.auth_header.return_value = {"Authorization": "Bearer tok"}
    http = MagicMock(spec=httpx.Client)
    return AccountManager(auth, http=http)


def test_account_seq_resolved_from_accounts_api(am):
    am._http.get.return_value = _resp(ACCOUNTS)
    assert am.account_seq == 7
    # 캐싱 확인 ― 재호출 없음
    am._http.get.reset_mock()
    assert am.account_seq == 7
    am._http.get.assert_not_called()


def test_account_no_match_selects_account(am):
    am._account_no = "12345678901"
    am._http.get.return_value = _resp(ACCOUNTS)
    assert am.account_seq == 7


def test_account_no_match_ignores_hyphens(am):
    am._account_no = "123456789-01"      # 하이픈 포함 표기도 매칭
    am._http.get.return_value = _resp(ACCOUNTS)
    assert am.account_seq == 7


def test_account_no_mismatch_raises_with_masked_candidates(am):
    am._account_no = "99999999999"
    am._http.get.return_value = _resp(ACCOUNTS)
    with pytest.raises(AccountError, match=r"123\*+01"):
        _ = am.account_seq


def test_no_brokerage_account_raises(am):
    am._http.get.return_value = _resp(
        [{"accountNo": "1", "accountSeq": 1,
          "accountType": "PENSION_SAVINGS"}])
    with pytest.raises(AccountError, match="BROKERAGE"):
        _ = am.account_seq


def test_holdings_header_carries_account_seq(am):
    am._account_seq = 7
    am._http.get.return_value = _resp(HOLDINGS)
    am.get_holdings()
    headers = am._http.get.call_args.kwargs["headers"]
    assert headers["X-Tossinvest-Account"] == "7"
    assert headers["Authorization"] == "Bearer tok"


def test_buying_power_and_sellable_parse_decimal_strings(am):
    am._account_seq = 7
    am._http.get.return_value = _resp(
        {"currency": "KRW", "cashBuyingPower": "5000000"})
    assert am.get_buying_power() == 5000000.0
    am._http.get.return_value = _resp({"sellableQuantity": "100"})
    assert am.get_sellable("005930") == 100.0


def test_parse_positions_from_holdings():
    assert AccountManager.parse_positions(HOLDINGS) == {"005930": 100.0}
    assert AccountManager.parse_positions({"items": []}) == {}


def test_reconcile_match_passes(am):
    am._account_seq = 7
    am._http.get.return_value = _resp(HOLDINGS)
    assert am.reconcile({"005930": 100.0}) is True
    assert am.halted is False


def test_reconcile_mismatch_halts(am):
    am._account_seq = 7
    am._http.get.return_value = _resp(HOLDINGS)
    assert am.reconcile({"005930": 90.0}) is False
    assert am.halted is True


def test_http_error_wrapped_as_account_error(am):
    am._account_seq = 7
    am._http.get.return_value = _resp(None, status=401)
    with pytest.raises(AccountError, match="HTTP 401"):
        am.get_holdings()
