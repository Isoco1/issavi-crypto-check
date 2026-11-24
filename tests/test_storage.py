"""Testes do armazenamento SQLite, usando um banco temporário na memória."""

import pytest

from alerts import CHANGE_UP, PRICE_ABOVE, Alert
from binance_client import Ticker
from storage import Storage


@pytest.fixture
def storage():
    """Cria um banco novo (vazio) para cada teste e fecha no final."""
    db = Storage(":memory:")
    yield db
    db.close()


def ticker(symbol: str, price: float) -> Ticker:
    return Ticker(symbol, price, change_percent=1.0, volume=10.0, quote_volume=1000.0)


def test_last_prices_returns_most_recent_per_symbol(storage):
    storage.save_prices([ticker("BTCUSDT", 60000), ticker("ETHUSDT", 1700)], now=100)
    storage.save_prices([ticker("BTCUSDT", 61000)], now=200)
    assert storage.last_prices() == {"BTCUSDT": 61000, "ETHUSDT": 1700}


def test_last_alert_times_returns_latest_per_symbol_and_kind(storage):
    storage.save_alert(Alert("BTCUSDT", CHANGE_UP, "msg"), now=100)
    storage.save_alert(Alert("BTCUSDT", CHANGE_UP, "msg"), now=500)
    storage.save_alert(Alert("BTCUSDT", PRICE_ABOVE, "msg"), now=300)
    assert storage.last_alert_times() == {("BTCUSDT", CHANGE_UP): 500,
                                          ("BTCUSDT", PRICE_ABOVE): 300}


def test_count_alerts_since(storage):
    for moment in (100, 200, 300):
        storage.save_alert(Alert("BTCUSDT", CHANGE_UP, "msg"), now=moment)
    assert storage.count_alerts_since(200) == 2


def test_delete_older_than_removes_only_old_prices(storage):
    storage.save_prices([ticker("BTCUSDT", 1)], now=100)
    storage.save_prices([ticker("BTCUSDT", 2)], now=900)
    assert storage.delete_older_than(500) == 1
    assert storage.last_prices() == {"BTCUSDT": 2}


def test_empty_database_returns_empty_results(storage):
    assert storage.last_prices() == {}
    assert storage.last_alert_times() == {}
    assert storage.count_alerts_since(0) == 0
