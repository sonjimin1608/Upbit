import pyupbit
import pandas as pd
import time
from pyupbit.errors import UpbitError
from dotenv import load_dotenv
import os
import logging
import sys
import requests

# 로그 파일 및 콘솔 동시 출력 설정
import sys

class DualLogger:
    def __init__(self):
        self.terminal = sys.stdout
        self.log_all = open("trade_all.log", "a", encoding="utf-8")
        self.log_filtered = open("trade.log", "a", encoding="utf-8")
        self._skip_next_newline = False  # 상태 저장용

    def write(self, message):
        # 항상 콘솔과 전체 로그에는 출력
        self.terminal.write(message)
        self.log_all.write(message)

        # 이전 메시지가 "거래 없음"이고 지금 메시지가 줄바꿈이면 필터링
        if self._skip_next_newline and message == "\n":
            self._skip_next_newline = False
            return

        # 이번 메시지가 "거래 없음"이면 필터하고, 다음 줄바꿈도 스킵 예약
        if "거래 없음" in message.strip():
            self._skip_next_newline = True
            return

        # 그 외의 메시지는 정상적으로 기록
        self.log_filtered.write(message)

        # flush는 항상 수행
        self.terminal.flush()
        self.log_all.flush()
        self.log_filtered.flush()

    def flush(self):
        self.terminal.flush()
        self.log_all.flush()
        self.log_filtered.flush()

sys.stdout = DualLogger()


# === 1. API 키 입력 ===
load_dotenv()
access_key = os.getenv("ACCESS_KEY")
secret_key = os.getenv("SECRET_KEY")
upbit = pyupbit.Upbit(access_key, secret_key)

# === 2. 관리할 티커 리스트 및 prev_rsi 딕셔너리 ===

# === 3. 로그인 및 계정 정보 확인 ===
def check_login():
    try:
        balances = upbit.get_balances()
        krw_balance = upbit.get_balance("KRW")
        if balances and krw_balance is not None:
            print(f"[로그인 성공] 보유 원화: {krw_balance:,.0f}원")
            return True
        else:
            print("[로그인 실패] 계정 정보 없음")
            return False
    except Exception as e:
        print(f"[로그인 오류] {str(e)}")
        return False

# === 4. RSI 계산 함수 (Wilder 공식) ===
def get_rsi(ticker, interval="minute5", period=14):
    try:
        df = pyupbit.get_ohlcv(ticker, interval=interval, count=200)
        if df is None or df.empty:
            print(f"[{ticker}] [RSI 오류] 데이터 수신 실패")
            return None
            
        delta = df['close'].diff().dropna()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)

        # 초기 평균 계산 (첫 14개 데이터)
        avg_gain = [gain.iloc[:period].mean()]
        avg_loss = [loss.iloc[:period].mean()]

        # Wilder 스무딩 적용
        for i in range(period, len(gain)):
            new_avg_gain = (avg_gain[-1] * (period-1) + gain.iloc[i]) / period
            new_avg_loss = (avg_loss[-1] * (period-1) + loss.iloc[i]) / period
            avg_gain.append(new_avg_gain)
            avg_loss.append(new_avg_loss)

        # RSI 계산
        rs = pd.Series(avg_gain) / pd.Series(avg_loss)
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1]

    except Exception as e:
        print(f"[{ticker}] [RSI 계산 오류] {str(e)}")
        return None

# === 5. 자동매매 함수 ===
def auto_trade(ticker, investment=5000):
    global prev_rsi_dict
    global prev_buy_dict
    ticker_balance = upbit.get_balance(ticker)
    krw_balance = upbit.get_balance("KRW")

    try:
        if prev_rsi_dict[ticker] is not None:
            if prev_rsi_dict[ticker] >= 65 and ticker_balance > 0:
                time.sleep(10)
            
            if prev_rsi_dict[ticker] <= 30 and krw_balance > 5000:
                time.sleep(10)
    except:
        print("참을 수가 없어.")
        return
    current_rsi = get_rsi(ticker)
    if current_rsi is None:
        print(f"[{ticker}] [거래 중단] RSI 계산 실패")
        return

    try:
        current_price = pyupbit.get_current_price(ticker)

        # 매수 조건: RSI 30 아래→위로 반등 & 미보유
        if prev_rsi_dict[ticker] is not None:
            # 매수
            if prev_rsi_dict[ticker] <= 30 and current_rsi > 30 and krw_balance >= investment:
                if krw_balance // 2 > investment:
                    order_amount = krw_balance // 2
                else:
                    order_amount = investment
                result = upbit.buy_market_order(ticker, order_amount)
                if result and 'uuid' in result:
                    current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                    ticker_balance_after = upbit.get_balance(ticker)
                    print(f"[{ticker}] [매수 성공 ({current_time})] {order_amount}원 | 총 매수 금액: {ticker_balance_after * current_price}")
                    prev_buy_dict[ticker] = ticker_balance_after * current_price
                else:
                    print(f"[{ticker}] [매수 실패] 주문 생성 오류: {result}")
            # 매도
            elif prev_rsi_dict[ticker] >= 65 and current_rsi < 65 and ticker_balance > 0:
                result = upbit.sell_market_order(ticker, ticker_balance)
                krw_balance_after = upbit.get_balance("KRW")

                if result and 'uuid' in result:
                    current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                    earned_money = krw_balance_after - prev_buy_dict[ticker]
                    earned_percentage = round(earned_money / prev_buy_dict[ticker] * 100, 2)
                    print(f"[{ticker}] [매도 성공 ({current_time})] {krw_balance_after}원 | 수익금: {earned_money} | 수익률: {earned_percentage}%")
                else:
                    print(f"[{ticker}] [매도 실패] 주문 생성 오류: {result}")
            # 거래 없음
            else:
                current_price = pyupbit.get_current_price(ticker)
                valuation = ticker_balance * current_price if ticker_balance is not None and current_price is not None else 0
                current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                print(f"[{ticker}] [거래 없음 ({current_time}))] 이전 RSI: {prev_rsi_dict[ticker]:.2f}, 현재 RSI: {current_rsi:.2f}, 평가 가치: {valuation:,.0f}원")
        # 첫 실행시 prev_rsi 초기화
        prev_rsi_dict[ticker] = current_rsi

    except UpbitError as ue:
        print(f"[{ticker}] [API 오류] 코드: {ue.code}, 메시지: {ue.message}")
    except Exception as e:
        error_msg = str(e).lower()
        if "insufficient" in error_msg:
            print(f"[{ticker}] [거래 실패] 잔고 부족: {str(e)}")
        elif "connection" in error_msg or "timeout" in error_msg:
            print(f"[{ticker}] [거래 실패] 네트워크 오류: {str(e)}")
        elif "429" in error_msg:
            print(f"[{ticker}] [거래 실패] 요청 과다(429): {str(e)}")
        else:
            print(f"[{ticker}] [거래 실패] 기타 오류: {str(e)}")

# === 6. 코인 명단 가져오기 ===

def get_krw_market_tickers():
    url = "https://api.upbit.com/v1/market/all"
    headers = {"Accept": "application/json"}
    response = requests.get(url, headers=headers)
    data = response.json()
    
    # KRW 마켓 필터링
    krw_tickers = [item['market'] for item in data if item['market'].startswith('KRW-')]
    return krw_tickers

def get_ticker_volumes(tickers):
    url = f"https://api.upbit.com/v1/ticker?markets={','.join(tickers)}"
    response = requests.get(url)
    data = response.json()

    # 거래량 계산: acc_trade_price_24h 사용 (24시간 누적 거래대금)
    df = pd.DataFrame(data)
    df = df[['market', 'acc_trade_price_24h']]
    df = df.sort_values(by='acc_trade_price_24h', ascending=False).reset_index(drop=True)

    return df

# === 6. 메인 루프 ===
if __name__ == "__main__":
    if not check_login():
        exit("프로그램 종료: 로그인 실패")
    
    # krw_tickers = get_krw_market_tickers()
    # volume_df = get_ticker_volumes(krw_tickers)
    # CANDIDATES = volume_df['market'][:23]
    # CANDIDATES = [ticker for ticker in CANDIDATES if "XRP" not in ticker]
    # CANDIDATES = [ticker for ticker in CANDIDATES if "USDT" not in ticker]
    # CANDIDATES = [ticker for ticker in CANDIDATES if "ANIME" not in ticker]

    # candidate_rsi_dict = {candidate: None for candidate in CANDIDATES}

    # # print(CANDIDATES)
    # for candidate in CANDIDATES:
    #     candidate_rsi_dict[candidate] = get_rsi(candidate)
    #     # print(candidate_rsi_dict)
    # sorted_candidates_dict = dict(sorted(candidate_rsi_dict.items(), key=lambda item: item[1]))
    # # print(sorted_candidates_dict)
    # TICKERS = list(sorted_candidates_dict.keys())[:3]
    TICKERS = ['KRW-MASK', 'KRW-BTC', 'KRW-SOL', 'KRW-DOGE']
    prev_rsi_dict = {ticker: None for ticker in TICKERS}
    prev_buy_dict = {ticker: None for ticker in TICKERS}

    print(f"=== 자동매매 시작: {', '.join(TICKERS)} ===")
    while True:
        try:
            for ticker in TICKERS:
                auto_trade(ticker)
        except Exception as e:
            print(f"[시스템 오류] {str(e)}")
            time.sleep(1)
