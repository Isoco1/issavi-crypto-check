"""Testes dos comandos do Telegram e dos gráficos (sem rede).

Em vez do BinanceClient de verdade, usamos um "falso" (fake) que devolve
dados fixos. Assim testamos o comportamento do bot sem depender da internet.
"""

from datetime import datetime, timedelta

import pytest
import requests

import charts
from binance_client import Candle, Ticker
from commands import BotStatus, CommandHandler, normalize_symbol
from storage import Storage

ALLOWED_CHAT = "111"


class FakeClient:
    """Imita o BinanceClient. Símbolos desconhecidos geram erro 400."""

    PRICES = {"BTCUSDT": 60000.0, "ETHUSDT": 1700.0}

    def get_24h_tickers(self, symbols):
        for s in symbols:
            if s not in self.PRICES:
                response = requests.Response()
                response.status_code = 400
                raise requests.HTTPError("400", response=response)
        return [Ticker(s, self.PRICES[s], 1.5, 10.0, 1_000_000.0) for s in symbols]

    def get_klines(self, symbol, interval="15m", limit=96):
        start = datetime(2026, 1, 1)
        return [Candle(start + timedelta(minutes=15 * i), 100.0 + i) for i in range(limit)]


@pytest.fixture
def handler():
    storage = Storage(":memory:")
    yield CommandHandler(FakeClient(), storage, ALLOWED_CHAT, ["BTCUSDT", "ETHUSDT"],
                         BotStatus(), interval_seconds=60)
    storage.close()


def message(text: str, chat_id: str = ALLOWED_CHAT) -> dict:
    """Monta uma atualização no mesmo formato que o Telegram envia."""
    return {"update_id": 1, "message": {"chat": {"id": int(chat_id)}, "text": text}}


def test_ignores_unauthorized_chat(handler):
    assert handler.handle(message("/preco", chat_id="999")) is None


def test_help_command(handler):
    assert "/preco" in handler.handle(message("/ajuda")).text


def test_price_without_args_uses_monitored_symbols(handler):
    text = handler.handle(message("/preco")).text
    assert "BTCUSDT" in text and "ETHUSDT" in text


def test_price_accepts_short_symbol(handler):
    text = handler.handle(message("/preco btc")).text
    assert "BTCUSDT" in text and "ETHUSDT" not in text


def test_price_unknown_symbol_gives_friendly_error(handler):
    assert "não encontrada" in handler.handle(message("/preco XYZ")).text


def test_price_rejects_invalid_characters(handler):
    reply = handler.handle(message("/preco <script>"))
    assert "inválido" in reply.text
    assert "<script>" not in reply.text   # o texto foi escapado


def test_unknown_command(handler):
    assert "desconhecido" in handler.handle(message("/comprar")).text


def test_command_with_bot_name_suffix(handler):
    # Em grupos o Telegram envia "/status@MeuBot".
    assert "funcionando" in handler.handle(message("/status@MeuBot")).text


def test_chart_command_returns_png(handler):
    reply = handler.handle(message("/grafico eth"))
    assert reply.photo is not None
    assert reply.photo.startswith(b"\x89PNG")   # assinatura de todo arquivo PNG


def test_normalize_symbol():
    assert normalize_symbol("btc") == "BTCUSDT"
    assert normalize_symbol("ethusdt") == "ETHUSDT"
    assert normalize_symbol("ETHBTC") == "ETHBTC"


def test_summary_chart_returns_png():
    candles = FakeClient().get_klines("BTCUSDT")
    png = charts.summary_chart({"BTCUSDT": candles, "ETHUSDT": candles})
    assert png.startswith(b"\x89PNG")


def test_chart_without_data_raises_clear_error():
    with pytest.raises(ValueError):
        charts.price_chart("BTCUSDT", [])
