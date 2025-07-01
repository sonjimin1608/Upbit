import pyupbit
import pandas as pd
import time
from pyupbit.errors import UpbitError
from dotenv import load_dotenv
import os
import logging
import sys
import requests

# === 로그 클래스 설정 ===
class DualLogger:
    def __init__(self):
        self.terminal = sys.stdout
        self.log_all = open("trade_all.log", "a", encoding="utf-8")
        self.log_filtered = open("trade.log", "a", encoding="utf-8")
        self._skip_next_newline = False

    def write(self, message):
        self.terminal.write(message)
        self.log_all.write(message)
        if self._skip_next_newline and message == "\n":
            self._skip_next_newline = False
            return
        if "거래 없음" in message.strip():
            self._skip_next_newline = True
            return
        self.log_filtered.write(message)
        self.terminal.flush()
        self.log_all.flush()
        self.log_filtered.flush()

    def flush(self):
        self.terminal.flush()
        self.log_all.flush()
        self.log_filtered.flush()

sys.stdout = DualLogger()

# === 환경 변수 및 Upbit 객체 ===
load_dotenv()
access_key = os.getenv("ACCESS_KEY")
secret_key = os.getenv("SECRET_KEY")
upbit = pyupbit.Upbit(access_key, secret_key)

# === EMA 계산 ===
def get_ema(df, period=200):
    return df['close'].rolling(window=period).mean()

# === MACD 계산 ===
def calculate_macd(df, short_period=12, long_period=26, signal_period=9):
    df['EMA12'] = df['close'].ewm(span=short_period, adjust=False).mean()
    df['EMA26'] = df['close'].ewm(span=long_period, adjust=False).mean()
    df['MACD'] = df['EMA12'] - df['EMA26']
    df['Signal'] = df['MACD'].ewm(span=signal_period, adjust=False).mean()
    df['Histogram'] = df['MACD'] - df['Signal']
    return df

# === 최근 20개 캔들 중 최저가 (돈치안 채널 하단) ===
def get_recent_low(ticker, interval="minute5", count=20):
    df = pyupbit.get_ohlcv(ticker, interval=interval, count=count)
    if df is None or df.empty:
        print(f"[{ticker}] 캔들 데이터 수신 실패")
        return None
    return df['low'].min()

# === 자동매매 함수 ===
def auto_trade(ticker, investment=5000):
    global prev_buy_dict
    global CANDIDATES
    current_balance = upbit.get_balance(ticker)
    krw_balance = upbit.get_balance("KRW")
    current_price = pyupbit.get_current_price(ticker)

    df = pyupbit.get_ohlcv(ticker, interval="minute5", count=200)
    if df is None or df.empty:
        print(f"[{ticker}] [데이터 수신 실패]")
        return

    try:
        ema_200 = get_ema(df, period=200).iloc[-1]
        price_ema_gap = (current_price - ema_200) / ema_200

        df_macd = calculate_macd(df)
        macd_now = df_macd['MACD'].iloc[-1]
        signal_now = df_macd['Signal'].iloc[-1]
        macd_prev = df_macd['MACD'].iloc[-2]
        signal_prev = df_macd['Signal'].iloc[-2]

        # 매도 조건
        if current_balance is None:
            print(f"[{ticker}] [오류] 잔고 조회 실패 (current_balance=None)")
            return

        if current_balance > 0 and prev_buy_dict[ticker] is not None:
            buy_info = prev_buy_dict[ticker]
            stop_loss_price = buy_info['stop_loss']
            take_profit_price = buy_info['take_profit']

            if current_price >= take_profit_price:
                result = upbit.sell_market_order(ticker, current_balance)
                krw_tickers = get_krw_market_tickers()
                volume_df = get_ticker_volumes(krw_tickers)
                CANDIDATES = volume_df['market'][:22]
                caution_tickers = get_caution_tickers()
                CANDIDATES = [ticker for ticker in CANDIDATES if ticker not in caution_tickers]
                CANDIDATES = [ticker for ticker in CANDIDATES if "XRP" not in ticker]
                CANDIDATES = [ticker for ticker in CANDIDATES if "USDT" not in ticker]
                if result and 'uuid' in result:
                    earned_money = upbit.get_balance("KRW") - buy_info['buy_price']
                    earned_percentage = round(earned_money / buy_info['buy_price'] * 100, 2) * (-1)
                    print(f"[{ticker}] [익절 매도] 현재가: {current_price:.2f}, 수익률: {earned_percentage}%")
                    prev_buy_dict[ticker] = None
                    return

            elif current_price < stop_loss_price:
                result = upbit.sell_market_order(ticker, current_balance)
                krw_tickers = get_krw_market_tickers()
                volume_df = get_ticker_volumes(krw_tickers)
                CANDIDATES = volume_df['market'][:22]
                caution_tickers = get_caution_tickers()
                CANDIDATES = [ticker for ticker in CANDIDATES if ticker not in caution_tickers]
                CANDIDATES = [ticker for ticker in CANDIDATES if "XRP" not in ticker]
                CANDIDATES = [ticker for ticker in CANDIDATES if "USDT" not in ticker]
                if result and 'uuid' in result:
                    loss_money = upbit.get_balance("KRW") - buy_info['buy_price']
                    loss_percentage = round(loss_money / buy_info['buy_price'] * 100, 2) * (-1)
                    print(f"[{ticker}] [손절 매도] 현재가: {current_price:.2f}, 수익률: {loss_percentage}%")
                    prev_buy_dict[ticker] = None
                    return

        # 매수 조건
        
        if macd_now > signal_now and macd_prev <= signal_prev:
            if macd_now < 0 and signal_now < 0:
                # if current_price > ema_200 and price_ema_gap >= 0.01:
                if current_price > ema_200:
                    order_amount = krw_balance * 0.99
                    result = upbit.buy_market_order(ticker, order_amount)
                    CANDIDATES = [ticker]
                    if result and 'uuid' in result:
                        ticker_balance_after = upbit.get_balance(ticker)
                        actual_buy_price = current_price
                        stop_loss_price = max(ema_200, get_recent_low(ticker))
                        if stop_loss_price == get_recent_low(ticker):
                            take_profit_price = actual_buy_price + (actual_buy_price - get_recent_low(ticker)) * 2
                        else:
                            take_profit_price = actual_buy_price + (actual_buy_price - ema_200) * 1.5
                        prev_buy_dict[ticker] = {
                            'buy_price': actual_buy_price * ticker_balance_after,
                            'stop_loss': stop_loss_price,
                            'take_profit': take_profit_price
                        }
                        print(f"[{ticker}] [매수 성공] {order_amount}원 / 현재가: {actual_buy_price:.2f}")
                        print(f"[{ticker}] 손절가: {stop_loss_price:.2f}, 익절가: {take_profit_price:.2f}")
                        time.sleep(120)
                    else:
                        print(f"[{ticker}] [매수 실패] 주문 오류: {result}")
                    return

        # 거래 없음 로그
        else:
            current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            print(f"[{ticker:<13}] [거래 없음 ({current_time})] MACD: {macd_now:<14.4f}, Signal: {signal_now:<14.4f}, 가격: {current_price:<13.2f}, EMA200: {ema_200:<13.2f}")
            

    except Exception as e:
        print(f"[{ticker}] [오류 발생] {str(e)}")

# === 유의 종목 필터 ===
def get_caution_tickers():
    url = "https://api.upbit.com/v1/market/all?isDetails=true"
    headers = {"Accept": "application/json"}
    response = requests.get(url, headers=headers)
    data = response.json()
    caution_tickers = []
    for item in data:
        market = item.get("market")
        if "KRW" not in market:
            continue
        market_event = item.get("market_event", {})
        warning = market_event.get("warning", False)
        caution_flags = market_event.get("caution", {})
        if warning or any(caution_flags.values()):
            caution_tickers.append(market)
    return caution_tickers

# === 티커 정렬 및 루프 ===
def get_krw_market_tickers():
    url = "https://api.upbit.com/v1/market/all"
    headers = {"Accept": "application/json"}
    response = requests.get(url, headers=headers)
    data = response.json()
    krw_tickers = [item['market'] for item in data if item['market'].startswith('KRW-')]
    return krw_tickers

def get_ticker_volumes(tickers):
    url = f"https://api.upbit.com/v1/ticker?markets={','.join(tickers)}"
    response = requests.get(url)
    data = response.json()
    df = pd.DataFrame(data)
    df = df[['market', 'acc_trade_price_24h']]
    df = df.sort_values(by='acc_trade_price_24h', ascending=False).reset_index(drop=True)
    return df

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

if __name__ == "__main__":
    if not check_login():
        exit("프로그램 종료: 로그인 실패")

    global CANDIDATES
    krw_tickers = get_krw_market_tickers()
    volume_df = get_ticker_volumes(krw_tickers)
    CANDIDATES = volume_df['market'][:22]
    caution_tickers = get_caution_tickers()
    CANDIDATES = [ticker for ticker in CANDIDATES if ticker not in caution_tickers]
    CANDIDATES = [ticker for ticker in CANDIDATES if "XRP" not in ticker]
    CANDIDATES = [ticker for ticker in CANDIDATES if "USDT" not in ticker]


    # CANDIDATES = ['KRW-AERGO']
    prev_buy_dict = {ticker: None for ticker in CANDIDATES}

    print(f"=== 자동매매 시작: {', '.join(CANDIDATES)} ===")
    while True:
        try:
            for ticker in CANDIDATES:
                auto_trade(ticker)
        except Exception as e:
            print(f"[시스템 오류] {str(e)}")
            time.sleep(1)