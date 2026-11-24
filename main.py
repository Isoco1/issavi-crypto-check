"""Ponto de entrada do bot: loop principal, resumo diário, comandos e logs.

Como usar:
    python main.py                  roda continuamente (e responde comandos)
    python main.py --once           faz uma única verificação e encerra
    python main.py --test-summary   envia o resumo diário agora e encerra
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as dtime
from logging.handlers import RotatingFileHandler

import requests
from dotenv import load_dotenv

import charts
from alerts import MODE_CROSS, AlertEngine, format_price
from binance_client import BinanceClient, Ticker
from commands import BotStatus, CommandHandler, CommandListener
from storage import Storage
from telegram_bot import TelegramBot

logger = logging.getLogger("bot")

CONFIG_PATH = "config.json"
LOG_PATH = "bot.log"


# ---------------------------------------------------------------- configuração

def setup_logging() -> None:
    """Envia os logs para o terminal e para o arquivo bot.log.

    O RotatingFileHandler limita o arquivo a 1 MB e guarda 3 cópias antigas,
    para o log não crescer sem limite se o bot ficar rodando por meses.
    """
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                                  datefmt="%Y-%m-%d %H:%M:%S")
    console = logging.StreamHandler()
    file = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3,
                               encoding="utf-8")
    for handler in (console, file):
        handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[console, file])
    # Bibliotecas muito "falantes": mostramos só os avisos delas.
    for noisy in ("urllib3", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def load_config(path: str = CONFIG_PATH) -> dict:
    """Lê o config.json e confere se os campos obrigatórios existem.

    Raises:
        KeyError: se faltar algum campo obrigatório.
    """
    with open(path, encoding="utf-8") as file:
        config = json.load(file)
    required = ["base_url", "symbols", "check_interval_seconds",
                "change_threshold_percent", "cooldown_minutes", "daily_summary_time"]
    missing = [key for key in required if key not in config]
    if missing:
        raise KeyError(f"Campos faltando no {path}: {', '.join(missing)}")
    return config


def parse_args() -> argparse.Namespace:
    """Lê as opções da linha de comando."""
    parser = argparse.ArgumentParser(description="Issavi Crypto Check: bot de alertas do mercado cripto.")
    parser.add_argument("--once", action="store_true",
                        help="faz uma única verificação e encerra")
    parser.add_argument("--test-summary", action="store_true",
                        help="envia o resumo diário agora e encerra")
    return parser.parse_args()


@dataclass
class App:
    """Agrupa as peças do bot, para não passar 6 parâmetros para cada função."""

    client: BinanceClient
    bot: TelegramBot
    engine: AlertEngine
    storage: Storage
    config: dict


# ---------------------------------------------------------------- resumo diário

class DailySummarySchedule:
    """Decide quando enviar o resumo diário (uma vez por dia, no horário)."""

    def __init__(self, summary_time: str, now: datetime | None = None) -> None:
        """Args:
            summary_time: horário no formato "HH:MM" (horário do computador).
            now: horário atual; os testes passam um valor fixo.
        """
        self.summary_time = dtime.fromisoformat(summary_time)
        now = now or datetime.now()
        # Se o bot for iniciado DEPOIS do horário, o resumo de hoje é pulado,
        # para não mandar um resumo "atrasado" toda vez que o bot reinicia.
        self.last_sent: date | None = now.date() if now.time() >= self.summary_time else None

    def is_due(self, now: datetime | None = None) -> bool:
        """True se já passou do horário e o resumo de hoje ainda não saiu."""
        now = now or datetime.now()
        return now.time() >= self.summary_time and self.last_sent != now.date()

    def mark_sent(self, now: datetime | None = None) -> None:
        """Registra que o resumo de hoje foi enviado."""
        self.last_sent = (now or datetime.now()).date()


def build_summary(tickers: list[Ticker]) -> str:
    """Monta o texto do resumo diário com preço, variação e volume."""
    lines = [f"📊 <b>Resumo diário</b> ({datetime.now():%d/%m/%Y %H:%M})", ""]
    for t in tickers:
        arrow = "🟢" if t.change_percent >= 0 else "🔴"
        lines.append(f"{arrow} <b>{t.symbol}</b>: {format_price(t.last_price)} "
                     f"({t.change_percent:+.2f}%)\n"
                     f"    Volume 24h: {t.quote_volume:,.0f} USDT")
    return "\n".join(lines)


def send_summary(app: App, tickers: list[Ticker]) -> bool:
    """Envia o resumo em texto e, em seguida, o gráfico comparativo.

    O gráfico é um "extra": se ele falhar, o resumo em texto já foi entregue,
    então registramos o problema e consideramos o resumo enviado.
    """
    if not app.bot.send_message(build_summary(tickers)):
        return False
    try:
        series = {s: app.client.get_klines(s) for s in app.config["symbols"]}
        app.bot.send_photo(charts.summary_chart(series), caption="📈 Variação nas últimas 24h")
    except Exception:  # noqa: BLE001 - o gráfico nunca deve impedir o resumo
        logger.exception("Não foi possível gerar o gráfico do resumo.")
    return True


# ---------------------------------------------------------------- verificação

def check_alerts(app: App, tickers: list[Ticker]) -> None:
    """Avalia as regras e envia os alertas, iniciando o cooldown só se enviou."""
    alerts = app.engine.evaluate(tickers)
    for alert in alerts:
        if app.bot.send_message(alert.message):
            now = time.time()
            app.engine.mark_sent(alert, now)
            app.storage.save_alert(alert, now)   # cooldown sobrevive a reinícios
            logger.info("Alerta enviado: %s %s", alert.symbol, alert.kind)
    if not alerts:
        logger.info("Nenhum alerta novo.")


def log_prices(tickers: list[Ticker]) -> None:
    """Registra no log os preços consultados (útil para acompanhar o bot)."""
    resumo = " | ".join(f"{t.symbol} {t.last_price:,.2f} ({t.change_percent:+.2f}%)"
                        for t in tickers)
    logger.info(resumo)


def run_check(app: App) -> list[Ticker]:
    """Busca os dados, envia os alertas e grava os preços no histórico."""
    tickers = app.client.get_24h_tickers(app.config["symbols"])
    log_prices(tickers)
    check_alerts(app, tickers)
    app.storage.save_prices(tickers, time.time())
    return tickers


def cleanup_history(app: App) -> None:
    """Apaga preços mais antigos que history_retention_days."""
    days = app.config.get("history_retention_days", 30)
    deleted = app.storage.delete_older_than(time.time() - days * 24 * 3600)
    if deleted:
        logger.info("Histórico: %s registros antigos apagados.", deleted)


def run_cycle(app: App, schedule: DailySummarySchedule) -> None:
    """Uma rodada completa: verificação, e o resumo diário se for a hora."""
    tickers = run_check(app)
    if schedule.is_due() and send_summary(app, tickers):
        schedule.mark_sent()
        cleanup_history(app)   # aproveitamos a rotina diária para limpar o banco
        logger.info("Resumo diário enviado.")


def run_forever(app: App) -> None:
    """Loop principal. Qualquer erro numa rodada é registrado e o bot continua."""
    interval = app.config["check_interval_seconds"]
    schedule = DailySummarySchedule(app.config["daily_summary_time"])
    status = BotStatus()
    listener = build_listener(app, status)
    logger.info("Bot iniciado. Verificando %s a cada %ss. Comandos: %s. (Ctrl+C para parar)",
                ", ".join(app.config["symbols"]), interval,
                "ativados" if listener else "desativados")
    while True:
        started = time.monotonic()
        try:
            run_cycle(app, schedule)
            status.checks_ok += 1
        except requests.RequestException as error:
            # Falha de rede/API: esperada de vez em quando, uma linha basta.
            status.checks_failed += 1
            logger.error("Binance indisponível (%s); tentando de novo em %ss.",
                         type(error).__name__, interval)
        except Exception:  # noqa: BLE001 - de propósito: o bot nunca deve parar
            # Erro inesperado (provável bug): aí sim queremos o traceback.
            status.checks_failed += 1
            logger.exception("Erro na verificação; tentando de novo na próxima rodada.")
        status.last_check_at = time.time()
        # A próxima verificação acontece "interval" segundos depois do início
        # desta. Até lá, respondemos comandos (ou só esperamos).
        wait_until(started + interval, listener)


def build_listener(app: App, status: BotStatus) -> CommandListener | None:
    """Cria o ouvinte de comandos, se estiver ativado no config."""
    if not app.config.get("enable_commands", True):
        return None
    handler = CommandHandler(app.client, app.storage, app.bot.chat_id,
                             app.config["symbols"], status,
                             app.config["check_interval_seconds"])
    listener = CommandListener(app.bot, handler)
    listener.skip_pending()
    return listener


def wait_until(deadline: float, listener: CommandListener | None) -> None:
    """Espera até deadline, respondendo comandos se o listener existir."""
    if listener is not None:
        try:
            listener.listen_until(deadline)
        except Exception:  # noqa: BLE001
            logger.exception("Erro ao ouvir comandos.")
    time.sleep(max(0, deadline - time.monotonic()))


# ---------------------------------------------------------------- início

def main() -> int:
    """Monta as peças e inicia o bot. Devolve o código de saída do programa."""
    args = parse_args()
    setup_logging()
    # Carrega o .env para as variáveis de ambiente. override=True faz o .env
    # ter prioridade: sem isso, uma variável antiga que já exista no terminal
    # (o VS Code pode copiar o .env para o terminal quando ele é aberto)
    # seria usada no lugar do valor atual do arquivo.
    load_dotenv(override=True)

    try:
        config = load_config()
        bot = TelegramBot.from_env()
        storage = Storage(config.get("database_path", "bot.db"))
        engine = AlertEngine(config["change_threshold_percent"],
                             config.get("price_targets", {}), config["cooldown_minutes"],
                             config.get("price_alert_mode", MODE_CROSS),
                             last_sent=storage.last_alert_times(),
                             last_prices=storage.last_prices())
    except (OSError, KeyError, ValueError) as error:
        # Erros de configuração não são temporários: avisamos e encerramos.
        logger.error("Configuração inválida: %s", error)
        return 1

    client = BinanceClient(config["base_url"], config.get("request_timeout_seconds", 10),
                           config.get("max_retries", 5))
    app = App(client, bot, engine, storage, config)

    try:
        if args.test_summary:
            tickers = client.get_24h_tickers(config["symbols"])
            return 0 if send_summary(app, tickers) else 1
        if args.once:
            run_check(app)
            return 0
        cleanup_history(app)
        run_forever(app)
    except KeyboardInterrupt:
        logger.info("Bot encerrado pelo usuário.")
    except Exception:  # noqa: BLE001 - só chega aqui nos modos --once/--test-summary
        logger.exception("Falha na execução.")
        return 1
    finally:
        storage.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
