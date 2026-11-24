"""Comandos do Telegram: /preco, /grafico, /status e /ajuda.

Duas classes com papéis separados:
- CommandHandler: recebe UMA mensagem e decide a resposta (testável sem rede).
- CommandListener: busca mensagens no Telegram e entrega ao handler.
"""

from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

import requests

import charts
from alerts import format_price
from binance_client import BinanceClient
from storage import Storage
from telegram_bot import TelegramBot

logger = logging.getLogger(__name__)

# Formato aceito para um par da Binance: letras maiúsculas e números.
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{5,20}$")
MAX_SYMBOLS_PER_COMMAND = 10
# Long polling: o Telegram segura a conexão por até este tempo esperando mensagens.
MAX_POLL_SECONDS = 25

HELP_TEXT = (
    "🤖 <b>Comandos disponíveis</b>\n\n"
    "/preco — preço das moedas monitoradas\n"
    "/preco BTC ETH — preço de moedas específicas\n"
    "/grafico BTC — gráfico das últimas 24h\n"
    "/status — situação do bot\n"
    "/ajuda — esta mensagem\n\n"
    "<i>Símbolos curtos (ex.: BTC) viram pares com USDT (BTCUSDT).</i>"
)


@dataclass
class BotStatus:
    """Informações sobre o funcionamento do bot, mostradas no /status."""

    started_at: float = field(default_factory=time.time)
    last_check_at: float | None = None
    checks_ok: int = 0
    checks_failed: int = 0


@dataclass
class Reply:
    """Resposta a um comando: texto, e opcionalmente uma imagem."""

    text: str
    photo: bytes | None = None


def normalize_symbol(raw: str) -> str:
    """Converte o que o usuário digitou num par da Binance.

    "btc" -> "BTCUSDT"; "ethbtc" -> "ETHBTC". Regra simples: símbolos com até
    5 letras são tratados como moeda e recebem "USDT" no final.
    """
    symbol = raw.strip().upper()
    if len(symbol) <= 5 and not symbol.endswith("USDT"):
        symbol += "USDT"
    return symbol


class CommandHandler:
    """Decide a resposta para cada mensagem recebida."""

    def __init__(self, client: BinanceClient, storage: Storage, allowed_chat_id: str,
                 symbols: list[str], status: BotStatus, interval_seconds: int) -> None:
        """Args:
            client: cliente da Binance, para buscar preços e velas.
            storage: banco de dados, para contar os alertas no /status.
            allowed_chat_id: único chat autorizado a usar os comandos.
            symbols: moedas monitoradas (padrão do /preco).
            status: objeto atualizado pelo main.py a cada verificação.
            interval_seconds: intervalo entre verificações (mostrado no /status).
        """
        self.client = client
        self.storage = storage
        self.allowed_chat_id = str(allowed_chat_id)
        self.symbols = symbols
        self.status = status
        self.interval_seconds = interval_seconds
        self._commands = {
            "/start": self._help,
            "/ajuda": self._help,
            "/help": self._help,
            "/preco": self._price,
            "/grafico": self._chart,
            "/status": self._status,
        }

    def handle(self, update: dict) -> Reply | None:
        """Processa uma atualização do Telegram e devolve a resposta (ou None).

        Segurança: só respondemos ao chat configurado no .env. Qualquer pessoa
        pode encontrar o bot no Telegram, então ignoramos os outros chats.
        """
        message = update.get("message") or {}
        chat_id = str(message.get("chat", {}).get("id", ""))
        if not chat_id:
            return None
        if chat_id != self.allowed_chat_id:
            # Mostramos o ID recebido: se for você, é sinal de que o
            # TELEGRAM_CHAT_ID do .env está errado.
            logger.warning("Mensagem ignorada do chat %s (não é o TELEGRAM_CHAT_ID "
                           "configurado).", chat_id)
            return None

        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            return Reply("Não entendi. Envie /ajuda para ver os comandos.")
        parts = text.split()
        # Em grupos o comando chega como "/preco@NomeDoBot": tiramos o "@...".
        command = parts[0].split("@")[0].lower()
        action = self._commands.get(command)
        if action is None:
            return Reply(f"Comando desconhecido: {html.escape(command)}\n"
                         "Envie /ajuda para ver os comandos.")
        logger.info("Comando recebido: %s", command)
        return action(parts[1:])

    # ------------------------------------------------------------ comandos

    def _help(self, args: list[str]) -> Reply:
        return Reply(HELP_TEXT)

    def _price(self, args: list[str]) -> Reply:
        """/preco [moedas...]: preço, variação e volume."""
        symbols = [normalize_symbol(a) for a in args] or self.symbols
        error = self._validate(symbols)
        if error:
            return Reply(error)
        try:
            tickers = self.client.get_24h_tickers(symbols)
        except requests.RequestException as exc:
            return Reply(_binance_error_text(exc))
        lines = ["💰 <b>Preços agora</b>", ""]
        for t in tickers:
            arrow = "🟢" if t.change_percent >= 0 else "🔴"
            lines.append(f"{arrow} <b>{t.symbol}</b>: {format_price(t.last_price)} "
                         f"({t.change_percent:+.2f}%)\n"
                         f"    Volume 24h: {t.quote_volume:,.0f} USDT")
        return Reply("\n".join(lines))

    def _chart(self, args: list[str]) -> Reply:
        """/grafico [moeda]: gráfico de preço das últimas 24h."""
        symbol = normalize_symbol(args[0]) if args else self.symbols[0]
        error = self._validate([symbol])
        if error:
            return Reply(error)
        try:
            candles = self.client.get_klines(symbol)
        except requests.RequestException as exc:
            return Reply(_binance_error_text(exc))
        return Reply(f"📊 <b>{symbol}</b>: últimas 24h", photo=charts.price_chart(symbol, candles))

    def _status(self, args: list[str]) -> Reply:
        """/status: há quanto tempo o bot roda, última verificação, alertas."""
        now = time.time()
        s = self.status
        last = (datetime.fromtimestamp(s.last_check_at).strftime("%H:%M:%S")
                if s.last_check_at else "nenhuma ainda")
        alerts_24h = self.storage.count_alerts_since(now - 24 * 3600)
        return Reply(
            "🟢 <b>Bot funcionando</b>\n\n"
            f"Ligado há: {_format_duration(now - s.started_at)}\n"
            f"Última verificação: {last}\n"
            f"Verificações: {s.checks_ok} ok, {s.checks_failed} com falha\n"
            f"Alertas nas últimas 24h: {alerts_24h}\n"
            f"Moedas: {', '.join(self.symbols)}\n"
            f"Intervalo: {self.interval_seconds}s"
        )

    def _validate(self, symbols: list[str]) -> str | None:
        """Confere os símbolos antes de chamar a API. Devolve o erro ou None."""
        if len(symbols) > MAX_SYMBOLS_PER_COMMAND:
            return f"Máximo de {MAX_SYMBOLS_PER_COMMAND} moedas por comando."
        invalid = [s for s in symbols if not SYMBOL_PATTERN.match(s)]
        if invalid:
            # html.escape evita que um texto digitado quebre a formatação HTML.
            return f"Símbolo inválido: {html.escape(', '.join(invalid))}"
        return None


class CommandListener:
    """Busca mensagens no Telegram e responde usando o CommandHandler."""

    def __init__(self, bot: TelegramBot, handler: CommandHandler) -> None:
        self.bot = bot
        self.handler = handler
        self._offset: int | None = None

    def skip_pending(self) -> None:
        """Ignora mensagens enviadas enquanto o bot estava desligado.

        Sem isto, ao ligar o bot ele responderia a comandos antigos de uma vez.
        offset=-1 pede só a última atualização; confirmamos ela como lida.
        """
        updates = self.bot.get_updates(offset=-1, wait_seconds=0)
        if updates:
            self._offset = updates[-1]["update_id"] + 1

    def listen_until(self, deadline: float) -> None:
        """Responde comandos até o horário deadline (em time.monotonic()).

        É isto que o loop principal usa no lugar de time.sleep(): enquanto
        espera a próxima verificação, o bot fica atendendo os comandos.
        """
        while (remaining := deadline - time.monotonic()) > 0:
            if remaining < 1:
                time.sleep(remaining)
                return
            updates = self.bot.get_updates(self._offset, int(min(remaining, MAX_POLL_SECONDS)))
            if updates is None:
                # Telegram indisponível: espera um pouco para não tentar sem parar.
                time.sleep(min(5, remaining))
                continue
            for update in updates:
                # Avança o offset ANTES de processar: se a resposta der erro,
                # o comando não fica sendo reprocessado para sempre.
                self._offset = update["update_id"] + 1
                self._respond(update)

    def _respond(self, update: dict) -> None:
        """Gera e envia a resposta de uma atualização, sem nunca derrubar o bot."""
        try:
            reply = self.handler.handle(update)
        except Exception:  # noqa: BLE001 - um comando com bug não pode parar o bot
            logger.exception("Erro ao processar comando.")
            reply = Reply("Ocorreu um erro ao processar o comando. 😕")
        if reply is None:
            return
        if reply.photo is not None:
            self.bot.send_photo(reply.photo, caption=reply.text)
        else:
            self.bot.send_message(reply.text)


def _binance_error_text(error: requests.RequestException) -> str:
    """Traduz um erro da Binance numa resposta amigável."""
    response = getattr(error, "response", None)
    if response is not None and response.status_code == 400:
        return "Moeda não encontrada na Binance. Confira o símbolo (ex.: BTC, ETHUSDT)."
    return "A Binance não respondeu agora. Tente de novo em instantes."


def _format_duration(seconds: float) -> str:
    """Formata segundos como "2d 3h 15min"."""
    minutes = int(seconds // 60)
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    parts = [f"{days}d"] if days else []
    if days or hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}min")
    return " ".join(parts)
