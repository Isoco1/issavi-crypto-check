"""Cliente para a API PÚBLICA de dados de mercado da Binance.

Este módulo só faz leituras (preços, variação, volume, velas). Ele NÃO usa
chave de API e NÃO tem nenhuma função de compra, venda ou acesso a conta.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

# Endpoints públicos usados pelo bot.
TICKER_24H_PATH = "/api/v3/ticker/24hr"   # preço, variação e volume de 24h
KLINES_PATH = "/api/v3/klines"            # velas (candles) para os gráficos

# Espera máxima entre tentativas, para o backoff não crescer sem limite.
MAX_BACKOFF_SECONDS = 60


@dataclass
class Ticker:
    """Dados de mercado de uma moeda nas últimas 24 horas."""

    symbol: str
    last_price: float
    change_percent: float
    volume: float          # volume na moeda base (ex.: quantos BTC)
    quote_volume: float    # volume na moeda de cotação (ex.: quantos USDT)


@dataclass
class Candle:
    """Uma vela (candle): usamos só o horário de abertura e o preço de fechamento."""

    open_time: datetime
    close: float


class BinanceClient:
    """Faz as requisições HTTP à API pública da Binance."""

    def __init__(self, base_url: str, timeout: float = 10, max_retries: int = 5) -> None:
        """Guarda a configuração e cria uma sessão HTTP reutilizável.

        Args:
            base_url: URL base da API (ex.: https://api.binance.com ou
                https://data-api.binance.vision).
            timeout: segundos de espera por resposta antes de desistir.
            max_retries: quantas tentativas fazer antes de lançar o erro.
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        # Uma Session reaproveita a conexão TCP entre chamadas (mais rápido).
        self.session = requests.Session()

    def get_24h_tickers(self, symbols: list[str]) -> list[Ticker]:
        """Busca preço, variação de 24h e volume de várias moedas de uma vez.

        Usar o parâmetro "symbols" faz UMA requisição para todas as moedas,
        o que consome menos do limite da API do que uma requisição por moeda.

        Args:
            symbols: lista de pares, ex.: ["BTCUSDT", "ETHUSDT"].

        Returns:
            Lista de objetos Ticker, um por moeda.

        Raises:
            requests.HTTPError: com status 400 se algum símbolo não existir.
        """
        # A API espera o formato ["BTCUSDT","ETHUSDT"] (JSON sem espaços).
        params = {"symbols": json.dumps(symbols, separators=(",", ":"))}
        data = self._get_with_retry(TICKER_24H_PATH, params)
        return [self._parse_ticker(item) for item in data]

    def get_klines(self, symbol: str, interval: str = "15m", limit: int = 96) -> list[Candle]:
        """Busca as últimas velas de uma moeda (usado nos gráficos).

        Args:
            symbol: par, ex.: "BTCUSDT".
            interval: duração de cada vela, ex.: "15m", "1h".
            limit: quantas velas buscar. 96 velas de 15 min = 24 horas.
        """
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        data = self._get_with_retry(KLINES_PATH, params)
        # Cada vela é uma lista: [abertura_ms, open, high, low, close, volume, ...]
        return [Candle(datetime.fromtimestamp(row[0] / 1000), float(row[4])) for row in data]

    def _get_with_retry(self, path: str, params: dict) -> list | dict:
        """Faz um GET com novas tentativas e espera crescente (backoff).

        - Falha de rede, timeout ou erro 5xx: tenta de novo (1s, 2s, 4s...).
        - 429/418 (excesso de requisições): espera o Retry-After da Binance.
        - Outros 4xx (ex.: símbolo inválido): erro nosso, não adianta repetir.

        Raises:
            requests.RequestException: se todas as tentativas falharem ou se
                a Binance recusar o pedido (4xx).
        """
        url = f"{self.base_url}{path}"
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as error:
                self._handle_failure(attempt, type(error).__name__, error)
                continue

            if response.status_code in (418, 429):
                wait = int(response.headers.get("Retry-After", 60))
                logger.warning("Limite da API atingido (%s). Aguardando %ss.",
                               response.status_code, wait)
                time.sleep(wait)
                continue
            if 400 <= response.status_code < 500:
                logger.warning("Binance recusou o pedido (HTTP %s): %s",
                               response.status_code, response.text[:200])
                response.raise_for_status()   # lança HTTPError, sem nova tentativa
            if response.status_code >= 500:
                error = requests.HTTPError(f"HTTP {response.status_code}", response=response)
                self._handle_failure(attempt, f"HTTP {response.status_code}", error)
                continue
            return response.json()
        # Só chega aqui se todas as tentativas foram consumidas por 418/429.
        raise requests.RequestException("Limite da API excedido em todas as tentativas.")

    def _handle_failure(self, attempt: int, reason: str, error: Exception) -> None:
        """Espera antes da próxima tentativa, ou relança o erro na última."""
        if attempt == self.max_retries:
            logger.error("Falha após %s tentativas: %s", attempt, reason)
            raise error
        wait = min(2 ** (attempt - 1), MAX_BACKOFF_SECONDS)
        logger.warning("Tentativa %s/%s falhou (%s). Nova tentativa em %ss.",
                       attempt, self.max_retries, reason, wait)
        time.sleep(wait)

    @staticmethod
    def _parse_ticker(item: dict) -> Ticker:
        """Converte um item do JSON da Binance em um Ticker.

        A Binance envia números como texto (ex.: "67000.01") para não perder
        precisão, por isso convertemos com float().
        """
        return Ticker(
            symbol=item["symbol"],
            last_price=float(item["lastPrice"]),
            change_percent=float(item["priceChangePercent"]),
            volume=float(item["volume"]),
            quote_volume=float(item["quoteVolume"]),
        )


def _demo() -> None:
    """Teste manual: imprime os preços atuais das moedas do config.json."""
    logging.basicConfig(level=logging.INFO)
    with open("config.json", encoding="utf-8") as file:
        config = json.load(file)

    client = BinanceClient(config["base_url"], config["request_timeout_seconds"],
                           config["max_retries"])
    for ticker in client.get_24h_tickers(config["symbols"]):
        print(f"{ticker.symbol:<10} preço: {ticker.last_price:>12,.2f}  "
              f"24h: {ticker.change_percent:>+6.2f}%  "
              f"volume: {ticker.quote_volume:>16,.0f} USDT")


if __name__ == "__main__":
    _demo()
