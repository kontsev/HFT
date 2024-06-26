from typing import List

import pandas as pd

from Simulator.simulator import MdUpdate
from Simulator.utils import AnonTrade, OrderbookSnapshotUpdate


def load_before_time(path, t):
    chunk_size = 10 ** 5
    chunks = []
    t0 = None
    for chunk in pd.read_csv(path, chunksize=chunk_size):
        if t0 is None:
            t0 = chunk['receive_ts'].iloc[0]
        chunks.append(chunk)
        if chunk['receive_ts'].iloc[-1] - t0 >= t:
            break
    df = pd.concat(chunks)
    mask = df['receive_ts'] - df['receive_ts'].iloc[0] < t
    df = df.loc[mask]

    return df


def load_trades(path: str, t: int) -> List[AnonTrade]:
    """
    This function downloads trades data

    """

    trades = load_before_time(path + 'trades.csv', t)
    trades = trades[['exchange_ts', 'receive_ts', 'aggro_side', 'size', 'price']].sort_values(["exchange_ts", 'receive_ts'])
    trades = [AnonTrade(*args) for args in trades.values]
    return trades


def load_books(path: str, t: int) -> List[OrderbookSnapshotUpdate]:
    """
    This function downloads orderbook market data

    """
    lobs = load_before_time(path + 'lobs.csv', t)
    names = lobs.columns.values
    ln = len('btcusdt:Binance:LinearPerpetual_')
    renamer = {name: name[ln:] for name in names[2:]}
    renamer[' exchange_ts'] = 'exchange_ts'
    lobs.rename(renamer, axis=1, inplace=True)
    
    receive_ts = lobs.receive_ts.values
    exchange_ts = lobs.exchange_ts.values 
    asks = [list(zip(lobs[f"ask_price_{i}"], lobs[f"ask_vol_{i}"])) for i in range(10)]
    asks = [[asks[i][j] for i in range(len(asks))] for j in range(len(asks[0]))]
    bids = [list(zip(lobs[f"bid_price_{i}"], lobs[f"bid_vol_{i}"])) for i in range(10)]
    bids = [[bids[i][j] for i in range(len(bids))] for j in range(len(bids[0]))]
    
    books = list(OrderbookSnapshotUpdate(*args) for args in zip(exchange_ts, receive_ts, asks, bids))
    return books


def merge_books_and_trades(books: List[OrderbookSnapshotUpdate], trades: List[AnonTrade]) -> List[MdUpdate]:
    """
    This function merges lists of orderbook snapshots and trades

    """
    trades_dict = {(trade.exchange_ts, trade.receive_ts): trade for trade in trades}
    books_dict = {(book.exchange_ts, book.receive_ts): book for book in books}
    
    ts = sorted(trades_dict.keys() | books_dict.keys())

    md = [MdUpdate(*key, books_dict.get(key, None), trades_dict.get(key, None)) for key in ts]
    return md


def load_md_from_file(path: str, run_time: int) -> List[MdUpdate]:
    """
    This function downloads orderbooks and trades and merges them
    
    """
    books = load_books(path, run_time)
    trades = load_trades(path, run_time)
    return merge_books_and_trades(books, trades)
