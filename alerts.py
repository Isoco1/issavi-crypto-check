"""Regras de alerta e controle de cooldown.

Este módulo não faz nenhuma chamada de rede nem acessa o banco: ele só recebe
dados (Tickers) e decide quais alertas devem ser enviados. Separar a "decisão"
do "envio" permite testar toda a lógica com dados simulados, sem internet.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from binance_client import Ticker

# Tipos de alerta. Cada tipo tem o seu próprio cooldown por moeda, então
# um alerta de variação não bloqueia um alerta de preço-alvo da mesma moeda.
CHANGE_UP = "variacao_alta"
CHANGE_DOWN = "variacao_queda"
PRICE_ABOVE = "preco_acima"
PRICE_BELOW = "preco_abaixo"

# Modos do alerta de preço-alvo (campo "price_alert_mode" do config.json).
MODE_CROSS = "cruzamento"   # avisa só no momento em que o preço passa do alvo
MODE_STATE = "estado"       # avisa enquanto o preço estiver além do alvo


@dataclass(frozen=True)
class Alert:
    """Um alerta pronto para ser enviado."""

    symbol: str
    kind: str      # um dos tipos acima (CHANGE_UP, PRICE_ABOVE, ...)
    message: str   # texto já formatado para o Telegram (HTML)


class CooldownTracker:
    """Lembra quando cada alerta foi enviado pela última vez.

    A chave é o par (moeda, tipo), ex.: ("BTCUSDT", "preco_acima").
    """

    def __init__(self, cooldown_seconds: float,
                 last_sent: dict[tuple[str, str], float] | None = None) -> None:
        """Args:
            cooldown_seconds: tempo mínimo entre dois alertas iguais.
            last_sent: horários já conhecidos (lidos do SQLite ao iniciar),
                para o cooldown continuar valendo depois de um reinício.
        """
        self.cooldown_seconds = cooldown_seconds
        self._last_sent: dict[tuple[str, str], float] = dict(last_sent or {})

    def is_ready(self, symbol: str, kind: str, now: float) -> bool:
        """Diz se este alerta pode ser enviado agora (fora do cooldown)."""
        last = self._last_sent.get((symbol, kind))
        return last is None or now - last >= self.cooldown_seconds

    def mark_sent(self, symbol: str, kind: str, now: float) -> None:
        """Registra que o alerta foi enviado neste momento."""
        self._last_sent[(symbol, kind)] = now


def check_change(ticker: Ticker, threshold_percent: float) -> Alert | None:
    """Verifica se a variação de 24h passou do limite (para cima ou para baixo).

    Args:
        ticker: dados da moeda.
        threshold_percent: limite em %, ex.: 3.0 significa +3% ou -3%.

    Returns:
        Um Alert, ou None se a variação está dentro do limite.
    """
    change = ticker.change_percent
    if change >= threshold_percent:
        kind, emoji, word = CHANGE_UP, "📈", "subiu"
    elif change <= -threshold_percent:
        kind, emoji, word = CHANGE_DOWN, "📉", "caiu"
    else:
        return None
    message = (f"{emoji} <b>{ticker.symbol}</b> {word} {abs(change):.2f}% em 24h\n"
               f"Preço atual: {format_price(ticker.last_price)}")
    return Alert(ticker.symbol, kind, message)


def check_price_targets(ticker: Ticker, targets: dict[str, float]) -> list[Alert]:
    """Modo "estado": alerta se o preço ESTÁ acima ou abaixo dos alvos.

    Args:
        ticker: dados da moeda.
        targets: alvos desta moeda, ex.: {"above": 65000, "below": 55000}.
            As duas chaves são opcionais.
    """
    alerts = []
    price = ticker.last_price
    above = targets.get("above")
    below = targets.get("below")
    if above is not None and price >= above:
        alerts.append(_price_alert(ticker, PRICE_ABOVE, above, "está acima de"))
    if below is not None and price <= below:
        alerts.append(_price_alert(ticker, PRICE_BELOW, below, "está abaixo de"))
    return alerts


def check_price_crossing(ticker: Ticker, targets: dict[str, float],
                         previous_price: float | None) -> list[Alert]:
    """Modo "cruzamento": alerta só quando o preço PASSA pelo alvo.

    Compara o preço anterior com o atual. Exemplo com alvo "above" = 65000:
    anterior 64900 e atual 65100 -> cruzou para cima -> alerta. Se o preço
    continuar em 65200 na rodada seguinte, não há novo alerta.

    Args:
        ticker: dados atuais da moeda.
        targets: alvos desta moeda, ex.: {"above": 65000, "below": 55000}.
        previous_price: preço da verificação anterior. Se for None (primeira
            vez que vemos a moeda), não dá para saber se cruzou: sem alerta.
    """
    if previous_price is None:
        return []
    alerts = []
    price = ticker.last_price
    above = targets.get("above")
    below = targets.get("below")
    if above is not None and previous_price < above <= price:
        alerts.append(_price_alert(ticker, PRICE_ABOVE, above, "cruzou para cima de"))
    if below is not None and previous_price > below >= price:
        alerts.append(_price_alert(ticker, PRICE_BELOW, below, "cruzou para baixo de"))
    return alerts


def _price_alert(ticker: Ticker, kind: str, target: float, verb: str) -> Alert:
    """Monta a mensagem de um alerta de preço-alvo."""
    emoji = "🚀" if kind == PRICE_ABOVE else "⚠️"
    return Alert(ticker.symbol, kind,
                 f"{emoji} <b>{ticker.symbol}</b> {verb} {format_price(target)}\n"
                 f"Preço atual: {format_price(ticker.last_price)}")


class AlertEngine:
    """Junta as regras de alerta com o cooldown."""

    def __init__(self, change_threshold_percent: float,
                 price_targets: dict[str, dict[str, float]],
                 cooldown_minutes: float,
                 price_alert_mode: str = MODE_CROSS,
                 last_sent: dict[tuple[str, str], float] | None = None,
                 last_prices: dict[str, float] | None = None) -> None:
        """Args:
            change_threshold_percent: limite de variação de 24h, em %.
            price_targets: alvos por moeda, como no config.json.
            cooldown_minutes: minutos sem repetir o mesmo alerta.
            price_alert_mode: MODE_CROSS ("cruzamento") ou MODE_STATE ("estado").
            last_sent: horários dos últimos alertas (vindos do SQLite).
            last_prices: últimos preços conhecidos (vindos do SQLite).
        """
        if price_alert_mode not in (MODE_CROSS, MODE_STATE):
            raise ValueError(f"price_alert_mode inválido: {price_alert_mode!r} "
                             f"(use '{MODE_CROSS}' ou '{MODE_STATE}')")
        self.change_threshold_percent = change_threshold_percent
        self.price_targets = price_targets
        self.price_alert_mode = price_alert_mode
        self.cooldown = CooldownTracker(cooldown_minutes * 60, last_sent)
        self._last_prices: dict[str, float] = dict(last_prices or {})

    def evaluate(self, tickers: list[Ticker], now: float | None = None) -> list[Alert]:
        """Devolve os alertas que devem ser enviados agora.

        Alertas em cooldown são descartados. Este método NÃO marca nada como
        enviado: quem envia chama mark_sent() só se o envio der certo. Assim,
        se o Telegram falhar, o alerta é tentado de novo na próxima rodada
        em vez de ficar 30 minutos bloqueado sem ter chegado.

        Ele guarda os preços atuais como "preço anterior" da próxima rodada
        (necessário para detectar cruzamentos).

        Args:
            tickers: dados atuais das moedas.
            now: horário atual em segundos. Os testes passam um valor fixo
                para simular a passagem do tempo sem precisar esperar.
        """
        now = time.time() if now is None else now
        triggered = self._all_triggered(tickers)
        for ticker in tickers:
            self._last_prices[ticker.symbol] = ticker.last_price
        return [alert for alert in triggered
                if self.cooldown.is_ready(alert.symbol, alert.kind, now)]

    def mark_sent(self, alert: Alert, now: float | None = None) -> None:
        """Inicia o cooldown de um alerta que foi entregue com sucesso."""
        now = time.time() if now is None else now
        self.cooldown.mark_sent(alert.symbol, alert.kind, now)

    def _all_triggered(self, tickers: list[Ticker]) -> list[Alert]:
        """Aplica todas as regras, sem considerar o cooldown."""
        alerts = []
        for ticker in tickers:
            change_alert = check_change(ticker, self.change_threshold_percent)
            if change_alert:
                alerts.append(change_alert)
            targets = self.price_targets.get(ticker.symbol, {})
            if self.price_alert_mode == MODE_CROSS:
                previous = self._last_prices.get(ticker.symbol)
                alerts.extend(check_price_crossing(ticker, targets, previous))
            else:
                alerts.extend(check_price_targets(ticker, targets))
        return alerts


def format_price(value: float) -> str:
    """Formata um preço em USDT, ex.: 59904.01 -> "59,904.01 USDT"."""
    return f"{value:,.2f} USDT"
