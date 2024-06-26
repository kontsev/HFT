from collections import deque
from typing import List, Optional, Tuple, Union, Deque, Dict
from sortedcontainers import SortedDict

import numpy as np

from Simulator.utils import Order, CancelOrder, OwnTrade, MdUpdate, update_best_positions, PriorQueue


class Sim:
    def __init__(self, market_data: List[MdUpdate], execution_latency: float, md_latency: float) -> None:
        """
            Args:
                market_data(List[MdUpdate]): market data
                execution_latency(float): latency in nanoseconds
                md_latency(float): latency in nanoseconds
        """
        # transform md to queue
        self.md_queue = deque(market_data)
        # action queue
        self.actions_queue: Deque[Union[Order, CancelOrder]] = deque()
        # SordetDict: receive_ts -> [updates]
        self.strategy_updates_queue = PriorQueue()
        # map : order_id -> Order
        self.ready_to_execute_orders: Dict[int, Order] = {}
        
        # current md
        self.md: Optional[MdUpdate] = None
        # current ids
        self.order_id = 0
        self.trade_id = 0
        # latency
        self.latency = execution_latency
        self.md_latency = md_latency
        # current bid and ask
        self.best_bid = -np.inf
        self.best_ask = np.inf
        # current trade
        self.trade_price = {'BID': -np.inf, 'ASK': np.inf}
        # last order
        self.last_order: Optional[Order] = None
        #Bid/Ask
        self.bid_orders = PriorHeap()
        self.ask_orders = PriorHeap()

    def get_md_queue_event_time(self) -> np.float:
        return np.inf if len(self.md_queue) == 0 else self.md_queue[0].exchange_ts

    def get_actions_queue_event_time(self) -> np.float:
        return np.inf if len(self.actions_queue) == 0 else self.actions_queue[0].exchange_ts

    def get_strategy_updates_queue_event_time(self) -> np.float:
        return self.strategy_updates_queue.min_key()

    def get_order_id(self) -> int:
        res = self.order_id
        self.order_id += 1
        return res

    def get_trade_id(self) -> int:
        res = self.trade_id
        self.trade_id += 1
        return res

    def update_last_trade(self) -> None:
        assert self.md is not None, "no current market data!"
        if self.md.trade is not None:
            self.trade_price[self.md.trade.side] = self.md.trade.price

    def delete_last_trade(self) -> None:
        self.trade_price['BID'] = -np.inf
        self.trade_price['ASK'] = np.inf

    def update_md(self, md: MdUpdate) -> None:
        # current orderbook
        self.md = md
        # update position
        self.best_bid, self.best_ask = update_best_positions(self.best_bid, self.best_ask, md)
        # update info about last trade
        self.update_last_trade()

        # add md to strategy_updates_queue
        self.strategy_updates_queue.push(md.receive_ts, md)

    def update_action(self, action: Union[Order, CancelOrder]) -> None:
        
        if isinstance(action, Order):
            # self.ready_to_execute_orders[action.order_id] = action
            # save last order to try to execute it aggressively
            self.last_order = action
        elif isinstance(action, CancelOrder):    
            # cancel order
            if action.id_to_delete in self.ready_to_execute_orders:
                self.ready_to_execute_orders.pop(action.id_to_delete)
                self.ask_orders.erase(action.id_to_delete)
                self.bid_orders.erase(action.id_to_delete)
        else:
            assert False, "Wrong action type!"

    def tick(self) -> Tuple[float, List[Union[OwnTrade, MdUpdate]]]:
        """
            Simulation tick

            Returns:
                receive_ts(float): receive timestamp in nanoseconds
                res(List[Union[OwnTrade, MdUpdate]]): simulation result. 
        """
        while True:     
            # get event time for all the queues
            strategy_updates_queue_et = self.get_strategy_updates_queue_event_time()
            md_queue_et = self.get_md_queue_event_time()
            actions_queue_et = self.get_actions_queue_event_time()
            
            # if both queue are empty
            if md_queue_et == np.inf and actions_queue_et == np.inf:
                break

            # strategy queue has minimum event time
            if strategy_updates_queue_et < min(md_queue_et, actions_queue_et):
                break

            call_execute = md_queue_et <= actions_queue_et
            if md_queue_et <= actions_queue_et:
                self.update_md(self.md_queue.popleft())
            if actions_queue_et <= md_queue_et:
                self.update_action(self.actions_queue.popleft())
                # execute last order aggressively
                self.execute_last_order()
            
            # execute orders with current orderbook
            if call_execute:
                self.execute_orders()
            # delete last trade
            self.delete_last_trade()
        key, res = self.strategy_updates_queue.pop()
        return key, res

    def execute_last_order(self) -> None:
        """
            this function tries to execute self.last order aggressively
        """
        # nothing to execute
        if self.last_order is None:
            return

        executed_price, execute = None, None
        if self.last_order.side == 'BID' and self.last_order.price >= self.best_ask:
            executed_price = self.best_ask
            execute = 'BOOK'
        elif self.last_order.side == 'ASK' and self.last_order.price <= self.best_bid:
            executed_price = self.best_bid
            execute = 'BOOK'

        if executed_price is not None:
            executed_order = OwnTrade(
                self.last_order.place_ts,  # when we place the order
                self.md.exchange_ts,  # exchange ts
                self.md.exchange_ts + self.md_latency,  # receive ts
                self.get_trade_id(),  # trade id
                self.last_order.order_id, 
                self.last_order.side, 
                self.last_order.size, 
                executed_price, execute)
            # add order to strategy update queue
            # there is no defaultsorteddict, so I have to do this
            self.strategy_updates_queue.push(executed_order.receive_ts, executed_order)
        else:
            self.ready_to_execute_orders[self.last_order.order_id] = self.last_order
            if self.last_order.side == 'BID':
                self.bid_orders.push(self.last_order.order_id, self.last_order.price)
            elif self.last_order.side == 'ASK':
                self.ask_orders.push(self.last_order.order_id, self.last_order.price)
        self.last_order = None

    def execute_orders(self) -> None:
        
        executed_orders_id = list()
        # order book bid order execution
        executed_orders_id += (self.bid_orders.greater_or_eq(self.best_ask))
        executed_orders_id += (self.ask_orders.less_or_eq(self.best_bid))

        n = len(executed_orders_id)

        executed_orders_id += (self.bid_orders.greater_or_eq(self.trade_price['ASK']))
        executed_orders_id += (self.ask_orders.less_or_eq(self.trade_price['BID']))

        for i, order_id in enumerate(executed_orders_id):
            order = self.ready_to_execute_orders.pop(order_id)
            
            self.ask_orders.erase(order_id)
            self.bid_orders.erase(order_id)

            execute = "BOOK" if i < n else "TRADE"
            executed_order = OwnTrade(
                order.place_ts,  # when we place the order
                self.md.exchange_ts,  # exchange ts
                self.md.exchange_ts + self.md_latency,  # receive ts
                self.get_trade_id(),  # trade id
                order_id, order.side, order.size, order.price, execute)

            self.strategy_updates_queue.push(executed_order.receive_ts, executed_order)

    def place_order(self, ts: float, size: float, side: str, price: float) -> Order:
        # добавляем заявку в список всех заявок
        order = Order(ts, ts + self.latency, self.get_order_id(), side, size, price)
        self.actions_queue.append(order)
        return order

    def cancel_order(self, ts: float, id_to_delete: int) -> CancelOrder:
        # добавляем заявку на удаление
        ts += self.latency
        delete_order = CancelOrder(ts, id_to_delete)
        self.actions_queue.append(delete_order)
        return delete_order

class PriorHeap:
    def __init__(self):
        self._map = {}
        self._heap = SortedDict()

    def __str__(self):
        return "map: " + str(self._map) + "\nheap: " + str(self._heap)

    def push(self, order_id: int, order_price: float):
        # add order to map
        self._map[order_id] = order_price
        
        if order_price not in self._heap.keys():
            self._heap[order_price] = set()
        self._heap[order_price].add(order_id)

    def erase(self, order_id):
        if order_id not in self._map:
            return False
        order_price = self._map.pop(order_id)
        self._heap[order_price].remove(order_id)
        if len(self._heap[order_price]) == 0:
            self._heap.pop(order_price)
        return True

    def greater_or_eq(self, price: float) -> List[int]:
        ind = self._heap.bisect_left(price)
        res = []
        for _price in self._heap.keys()[ind:]:
            res += self._heap[_price]
        return res
    
    def less_or_eq(self, price: float) -> List[int]:
        ind = self._heap.bisect_right(price)
        res = []
        for _price in self._heap.keys()[:ind]:
            res += self._heap[_price]
        return res