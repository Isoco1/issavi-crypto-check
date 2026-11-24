"""Geração de gráficos PNG com matplotlib.

As funções recebem dados prontos (velas) e devolvem a imagem em bytes, sem
salvar arquivo em disco: os bytes vão direto para o Telegram (sendPhoto).
Isso também facilita os testes: basta conferir que saiu um PNG válido.
"""

from __future__ import annotations

import io

import matplotlib

# "Agg" desenha direto em memória, sem abrir janela. Sem isto, o matplotlib
# tentaria usar a tela, o que falha em servidores e em threads sem interface.
matplotlib.use("Agg")

import matplotlib.dates as mdates  # noqa: E402 (precisa vir depois do use("Agg"))
import matplotlib.pyplot as plt  # noqa: E402

from binance_client import Candle  # noqa: E402

UP_COLOR = "#0ECB81"     # verde usado para alta
DOWN_COLOR = "#F6465D"   # vermelho usado para queda


def price_chart(symbol: str, candles: list[Candle]) -> bytes:
    """Gráfico de linha do preço de uma moeda.

    Args:
        symbol: par, ex.: "BTCUSDT" (vai no título).
        candles: velas em ordem cronológica.

    Returns:
        A imagem PNG em bytes.
    """
    _require_data(candles)
    times = [c.open_time for c in candles]
    prices = [c.close for c in candles]
    color = UP_COLOR if prices[-1] >= prices[0] else DOWN_COLOR
    change = (prices[-1] / prices[0] - 1) * 100

    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=110)
    ax.plot(times, prices, color=color, linewidth=2)
    # Preenchimento leve abaixo da linha, só para facilitar a leitura.
    ax.fill_between(times, prices, min(prices), color=color, alpha=0.12)
    ax.set_title(f"{symbol}: últimas 24h ({change:+.2f}%)", loc="left", fontweight="bold")
    ax.set_ylabel("Preço (USDT)")
    _style_time_axis(ax)
    return _to_png(fig)


def summary_chart(series: dict[str, list[Candle]]) -> bytes:
    """Compara várias moedas no mesmo gráfico, em variação percentual.

    Moedas têm preços muito diferentes (BTC ~60.000, SOL ~70), então não dá
    para colocar todas na mesma escala de preço. Convertemos cada série para
    "% de variação desde o início do período": todas começam em 0%.

    Args:
        series: velas por moeda, ex.: {"BTCUSDT": [...], "ETHUSDT": [...]}.
    """
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=110)
    for symbol, candles in series.items():
        _require_data(candles)
        first = candles[0].close
        ax.plot([c.open_time for c in candles],
                [(c.close / first - 1) * 100 for c in candles],
                linewidth=2, label=symbol)
    ax.axhline(0, color="gray", linewidth=0.8, linestyle="--")
    ax.set_title("Variação nas últimas 24h", loc="left", fontweight="bold")
    ax.set_ylabel("Variação (%)")
    # Legenda abaixo do gráfico, numa linha, para não cobrir as curvas.
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=len(series),
              frameon=False)
    _style_time_axis(ax)
    return _to_png(fig)


def _require_data(candles: list[Candle]) -> None:
    """Gráfico sem dados não faz sentido: falha com uma mensagem clara."""
    if not candles:
        raise ValueError("Nenhuma vela recebida para montar o gráfico.")


def _style_time_axis(ax: plt.Axes) -> None:
    """Formata o eixo de tempo como horas (ex.: 14:00) e deixa o gráfico limpo."""
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.grid(alpha=0.3)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def _to_png(fig: plt.Figure) -> bytes:
    """Converte a figura em bytes PNG e libera a memória dela."""
    buffer = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buffer, format="png")
    # Sem close(), cada gráfico ficaria na memória para sempre (vazamento).
    plt.close(fig)
    return buffer.getvalue()
