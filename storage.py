"""Armazenamento em SQLite: histórico de preços e de alertas enviados.

Por que SQLite? Ele já vem com o Python (módulo sqlite3), guarda tudo num
único arquivo (bot.db) e não precisa de servidor. É ideal para um bot que
roda num computador só.

Para que serve aqui:
- O cooldown sobrevive a reinícios (o bot lê quando cada alerta saiu).
- O alerta por cruzamento sabe qual era o preço antes de o bot reiniciar.
- O comando /status mostra quantos alertas saíram nas últimas 24h.
"""

from __future__ import annotations

import sqlite3

from alerts import Alert
from binance_client import Ticker

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT    NOT NULL,
    price         REAL    NOT NULL,
    change_pct    REAL    NOT NULL,
    quote_volume  REAL    NOT NULL,
    checked_at    REAL    NOT NULL      -- horário em segundos (time.time())
);
CREATE INDEX IF NOT EXISTS idx_price_symbol_time ON price_history (symbol, checked_at);

CREATE TABLE IF NOT EXISTS alerts_sent (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol    TEXT NOT NULL,
    kind      TEXT NOT NULL,
    message   TEXT NOT NULL,
    sent_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_key ON alerts_sent (symbol, kind, sent_at);
"""


class Storage:
    """Lê e grava os dados do bot num arquivo SQLite."""

    def __init__(self, path: str) -> None:
        """Abre (ou cria) o banco de dados e as tabelas.

        Args:
            path: caminho do arquivo, ex.: "bot.db". Nos testes usamos
                ":memory:", que cria um banco temporário só na memória.
        """
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        """Fecha a conexão com o banco."""
        self.conn.close()

    # ------------------------------------------------------------ preços

    def save_prices(self, tickers: list[Ticker], now: float) -> None:
        """Grava o preço atual de cada moeda."""
        rows = [(t.symbol, t.last_price, t.change_percent, t.quote_volume, now)
                for t in tickers]
        # "with self.conn" abre uma transação: grava tudo ou nada.
        with self.conn:
            self.conn.executemany(
                "INSERT INTO price_history (symbol, price, change_pct, quote_volume, checked_at)"
                " VALUES (?, ?, ?, ?, ?)", rows)

    def last_prices(self) -> dict[str, float]:
        """Devolve o último preço gravado de cada moeda, ex.: {"BTCUSDT": 60000.0}."""
        # Os "?" acima e este SQL fixo evitam SQL injection: nunca montamos
        # SQL juntando textos com dados.
        rows = self.conn.execute(
            "SELECT symbol, price FROM price_history AS p"
            " WHERE checked_at = (SELECT MAX(checked_at) FROM price_history"
            "                     WHERE symbol = p.symbol)")
        return dict(rows.fetchall())

    # ------------------------------------------------------------ alertas

    def save_alert(self, alert: Alert, now: float) -> None:
        """Registra um alerta que foi entregue."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO alerts_sent (symbol, kind, message, sent_at) VALUES (?, ?, ?, ?)",
                (alert.symbol, alert.kind, alert.message, now))

    def last_alert_times(self) -> dict[tuple[str, str], float]:
        """Quando cada alerta (moeda, tipo) foi enviado pela última vez."""
        rows = self.conn.execute(
            "SELECT symbol, kind, MAX(sent_at) FROM alerts_sent GROUP BY symbol, kind")
        return {(symbol, kind): sent_at for symbol, kind, sent_at in rows}

    def count_alerts_since(self, since: float) -> int:
        """Quantos alertas foram enviados a partir de um horário."""
        row = self.conn.execute("SELECT COUNT(*) FROM alerts_sent WHERE sent_at >= ?",
                                (since,)).fetchone()
        return row[0]

    # ------------------------------------------------------------ limpeza

    def delete_older_than(self, cutoff: float) -> int:
        """Apaga o histórico de preços antigo, para o arquivo não crescer sem fim.

        Os alertas são poucos e ficam guardados. Devolve quantas linhas foram apagadas.
        """
        with self.conn:
            cursor = self.conn.execute("DELETE FROM price_history WHERE checked_at < ?",
                                       (cutoff,))
        return cursor.rowcount
