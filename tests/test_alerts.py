"""Testes da lógica de alertas e cooldown.

Nenhum teste chama a API: criamos Tickers "falsos" com os valores que
queremos testar. Para rodar: python -m pytest
"""

import pytest

from alerts import (CHANGE_DOWN, CHANGE_UP, MODE_CROSS, MODE_STATE, PRICE_ABOVE,
                    PRICE_BELOW, AlertEngine, CooldownTracker, check_change,
                    check_price_crossing, check_price_targets)
from binance_client import Ticker


def make_ticker(symbol: str = "BTCUSDT", price: float = 60000.0,
                change: float = 0.0) -> Ticker:
    """Cria um Ticker simulado só com os campos que importam para o teste."""
    return Ticker(symbol=symbol, last_price=price, change_percent=change,
                  volume=1000.0, quote_volume=60_000_000.0)


# ---------- Alerta de variação ----------

def test_change_above_threshold_triggers_up_alert():
    alert = check_change(make_ticker(change=3.5), threshold_percent=3.0)
    assert alert is not None
    assert alert.kind == CHANGE_UP


def test_change_below_negative_threshold_triggers_down_alert():
    alert = check_change(make_ticker(change=-4.0), threshold_percent=3.0)
    assert alert is not None
    assert alert.kind == CHANGE_DOWN


def test_change_within_threshold_does_not_trigger():
    assert check_change(make_ticker(change=2.9), threshold_percent=3.0) is None
    assert check_change(make_ticker(change=-2.9), threshold_percent=3.0) is None


def test_change_exactly_at_threshold_triggers():
    # Caso de borda: exatamente 3% conta como "passou do limite".
    assert check_change(make_ticker(change=3.0), threshold_percent=3.0) is not None


# ---------- Alerta de preço-alvo ----------

def test_price_above_target_triggers():
    alerts = check_price_targets(make_ticker(price=66000), {"above": 65000})
    assert [a.kind for a in alerts] == [PRICE_ABOVE]


def test_price_below_target_triggers():
    alerts = check_price_targets(make_ticker(price=54000), {"below": 55000})
    assert [a.kind for a in alerts] == [PRICE_BELOW]


def test_price_between_targets_does_not_trigger():
    alerts = check_price_targets(make_ticker(price=60000), {"above": 65000, "below": 55000})
    assert alerts == []


def test_no_targets_configured_does_not_trigger():
    assert check_price_targets(make_ticker(price=60000), {}) == []


# ---------- Cooldown ----------

def test_cooldown_blocks_repeat_within_period():
    tracker = CooldownTracker(cooldown_seconds=1800)
    tracker.mark_sent("BTCUSDT", CHANGE_UP, now=0)
    assert not tracker.is_ready("BTCUSDT", CHANGE_UP, now=1799)


def test_cooldown_allows_after_period():
    tracker = CooldownTracker(cooldown_seconds=1800)
    tracker.mark_sent("BTCUSDT", CHANGE_UP, now=0)
    assert tracker.is_ready("BTCUSDT", CHANGE_UP, now=1800)


def test_cooldown_is_separate_per_symbol_and_kind():
    tracker = CooldownTracker(cooldown_seconds=1800)
    tracker.mark_sent("BTCUSDT", CHANGE_UP, now=0)
    assert tracker.is_ready("ETHUSDT", CHANGE_UP, now=10)     # outra moeda
    assert tracker.is_ready("BTCUSDT", PRICE_ABOVE, now=10)   # outro tipo


# ---------- AlertEngine (regras + cooldown juntos) ----------

def make_engine(mode: str = MODE_STATE) -> AlertEngine:
    return AlertEngine(change_threshold_percent=3.0,
                       price_targets={"BTCUSDT": {"above": 65000, "below": 55000}},
                       cooldown_minutes=30, price_alert_mode=mode)


def test_engine_sends_alert_then_blocks_repeat():
    engine = make_engine()
    tickers = [make_ticker(change=5.0)]
    alerts = engine.evaluate(tickers, now=0)
    assert len(alerts) == 1
    engine.mark_sent(alerts[0], now=0)                       # envio deu certo
    assert engine.evaluate(tickers, now=60) == []            # 1 min depois: bloqueado
    assert len(engine.evaluate(tickers, now=30 * 60)) == 1   # 30 min depois: libera


def test_engine_retries_alert_that_was_not_sent():
    # Se o envio falhou (mark_sent não foi chamado), o alerta volta na próxima rodada.
    engine = make_engine()
    tickers = [make_ticker(change=5.0)]
    engine.evaluate(tickers, now=0)
    assert len(engine.evaluate(tickers, now=60)) == 1


def test_engine_combines_change_and_price_alerts():
    engine = make_engine()
    alerts = engine.evaluate([make_ticker(price=66000, change=4.0)], now=0)
    assert {a.kind for a in alerts} == {CHANGE_UP, PRICE_ABOVE}


def test_engine_reversal_from_up_to_down_is_not_blocked():
    # Subiu 5% e depois caiu 5%: são alertas diferentes, os dois devem chegar.
    engine = make_engine()
    for alert in engine.evaluate([make_ticker(change=5.0)], now=0):
        engine.mark_sent(alert, now=0)
    alerts = engine.evaluate([make_ticker(change=-5.0)], now=60)
    assert [a.kind for a in alerts] == [CHANGE_DOWN]


# ---------- Alerta por cruzamento (versão 2) ----------

def test_crossing_up_triggers():
    alerts = check_price_crossing(make_ticker(price=65100), {"above": 65000},
                                  previous_price=64900)
    assert [a.kind for a in alerts] == [PRICE_ABOVE]


def test_crossing_down_triggers():
    alerts = check_price_crossing(make_ticker(price=54900), {"below": 55000},
                                  previous_price=55100)
    assert [a.kind for a in alerts] == [PRICE_BELOW]


def test_staying_above_target_does_not_trigger_again():
    # Já estava acima e continua acima: não houve cruzamento.
    alerts = check_price_crossing(make_ticker(price=66000), {"above": 65000},
                                  previous_price=65500)
    assert alerts == []


def test_crossing_without_previous_price_does_not_trigger():
    # Primeira vez que vemos a moeda: não dá para saber se cruzou.
    assert check_price_crossing(make_ticker(price=66000), {"above": 65000}, None) == []


def test_engine_cross_mode_uses_previous_evaluation():
    engine = make_engine(MODE_CROSS)
    assert engine.evaluate([make_ticker(price=64000)], now=0) == []      # guarda 64000
    alerts = engine.evaluate([make_ticker(price=65500)], now=60)         # cruzou
    assert [a.kind for a in alerts] == [PRICE_ABOVE]


def test_engine_cross_mode_uses_prices_loaded_from_database():
    # Simula um reinício: o último preço veio do SQLite.
    engine = AlertEngine(3.0, {"BTCUSDT": {"above": 65000}}, 30, MODE_CROSS,
                         last_prices={"BTCUSDT": 64000})
    alerts = engine.evaluate([make_ticker(price=65500)], now=0)
    assert [a.kind for a in alerts] == [PRICE_ABOVE]


def test_engine_respects_cooldown_loaded_from_database():
    # Simula um reinício 5 minutos depois de um alerta: continua bloqueado.
    engine = AlertEngine(3.0, {}, 30, last_sent={("BTCUSDT", CHANGE_UP): 0})
    assert engine.evaluate([make_ticker(change=5.0)], now=300) == []


def test_engine_rejects_invalid_mode():
    with pytest.raises(ValueError):
        AlertEngine(3.0, {}, 30, price_alert_mode="qualquer")
