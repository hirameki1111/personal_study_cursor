"""도메인 예외 계층 (제안서 Step 1-1).

전 모듈이 TradingError를 기저로 상속한다.
"""


class TradingError(Exception):
    """도메인 기저 예외"""


class AuthError(TradingError):
    """인증·토큰 발급 실패 ― 거래 중단 트리거"""


class MarketError(TradingError):
    """시세 조회·검증 실패"""


class AccountError(TradingError):
    """계좌 조회·정합성 검증 실패"""


class OrderError(TradingError):
    """주문 전송·상태 추적 실패"""
