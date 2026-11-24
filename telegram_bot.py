"""Comunicação com o Telegram usando a Bot API oficial.

Não usamos biblioteca extra: a Bot API é uma API HTTP simples, então o
"requests" (que já usamos para a Binance) é suficiente. Menos dependências =
menos coisas para quebrar e mais fácil de entender.
"""

from __future__ import annotations

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/{method}"

# Limites do Telegram: 4096 caracteres por mensagem, 1024 por legenda de foto.
MAX_MESSAGE_LENGTH = 4096
MAX_CAPTION_LENGTH = 1024


class TelegramBot:
    """Envia mensagens/fotos e lê os comandos recebidos."""

    def __init__(self, token: str, chat_id: str, timeout: float = 10,
                 max_retries: int = 3) -> None:
        """Guarda as credenciais e cria uma sessão HTTP reutilizável.

        Args:
            token: token do bot gerado pelo @BotFather (vem do .env).
            chat_id: ID do chat que recebe as mensagens (vem do .env).
            timeout: segundos de espera por resposta do Telegram.
            max_retries: quantas tentativas fazer antes de desistir.
        """
        if not token or not chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID são obrigatórios. "
                             "Confira o seu arquivo .env.")
        self._token = token
        self.chat_id = chat_id
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()

    @classmethod
    def from_env(cls, timeout: float = 10, max_retries: int = 3) -> "TelegramBot":
        """Cria o bot lendo o token e o chat_id das variáveis de ambiente.

        As variáveis são carregadas do arquivo .env pelo python-dotenv
        (função load_dotenv, chamada no main.py antes disto).
        """
        return cls(os.getenv("TELEGRAM_BOT_TOKEN", ""),
                   os.getenv("TELEGRAM_CHAT_ID", ""), timeout, max_retries)

    # ------------------------------------------------------------ métodos públicos

    def send_message(self, text: str) -> bool:
        """Envia uma mensagem de texto. Nunca lança exceção: devolve True ou False.

        Decisão de projeto: se o Telegram estiver fora do ar, o bot deve
        registrar o erro no log e continuar monitorando, e não parar.

        Args:
            text: texto da mensagem (aceita formatação HTML simples: <b>, <i>).
        """
        payload = {"chat_id": self.chat_id, "text": text[:MAX_MESSAGE_LENGTH],
                   "parse_mode": "HTML"}
        return self._call("sendMessage", json=payload) is not None

    def send_photo(self, image: bytes, caption: str = "") -> bool:
        """Envia uma imagem PNG com legenda opcional. Devolve True ou False."""
        data = {"chat_id": self.chat_id, "caption": caption[:MAX_CAPTION_LENGTH],
                "parse_mode": "HTML"}
        files = {"photo": ("grafico.png", image, "image/png")}
        return self._call("sendPhoto", data=data, files=files) is not None

    def get_updates(self, offset: int | None, wait_seconds: int) -> list[dict] | None:
        """Busca mensagens novas enviadas ao bot (técnica de "long polling").

        Com long polling, o Telegram segura a conexão aberta por até
        wait_seconds e responde assim que chega uma mensagem. Assim o bot
        responde quase na hora, sem ficar perguntando "tem novidade?" sem parar.

        Args:
            offset: ID da próxima atualização esperada. As anteriores são
                consideradas lidas e o Telegram não as devolve mais.
            wait_seconds: quanto tempo o Telegram pode esperar por mensagens.

        Returns:
            Lista de atualizações (pode ser vazia), ou None se a chamada falhou.
        """
        params = {"timeout": wait_seconds, "allowed_updates": '["message"]'}
        if offset is not None:
            params["offset"] = offset
        # Uma tentativa só: quem chama já repete o ciclo naturalmente.
        return self._call("getUpdates", params=params, http_timeout=wait_seconds + 10,
                          attempts=1)

    # ------------------------------------------------------------ parte interna

    def _call(self, method: str, http_timeout: float | None = None,
              attempts: int | None = None, **request_kwargs) -> dict | list | None:
        """Chama um método da Bot API com novas tentativas.

        Returns:
            O campo "result" da resposta, ou None se falhou.
        """
        url = TELEGRAM_API_URL.format(token=self._token, method=method)
        attempts = attempts or self.max_retries
        for attempt in range(1, attempts + 1):
            try:
                response = self.session.post(url, timeout=http_timeout or self.timeout,
                                             **request_kwargs)
                if response.status_code == 429:
                    # O Telegram informa quantos segundos esperar em "retry_after".
                    wait = response.json().get("parameters", {}).get("retry_after", 5)
                    logger.warning("Telegram pediu para esperar %ss.", wait)
                    time.sleep(wait)
                    continue
                if 400 <= response.status_code < 500:
                    # Erro 4xx = problema na configuração (token ou chat_id
                    # errado). Tentar de novo não resolve, então desistimos já.
                    # O campo "description" é o motivo dado pelo Telegram e
                    # não contém o token, então é seguro colocá-lo no log.
                    logger.error("Telegram recusou %s (HTTP %s): %s", method,
                                 response.status_code, _error_description(response))
                    return None
                response.raise_for_status()  # erros 5xx: tentamos de novo
                return response.json()["result"]
            except requests.RequestException as error:
                # Não logamos a URL nem a mensagem do erro, porque elas contêm
                # o token (é um segredo!). O tipo do erro já ajuda a diagnosticar.
                logger.warning("Chamada %s ao Telegram falhou (tentativa %s/%s): %s",
                               method, attempt, attempts, type(error).__name__)
                if attempt < attempts:
                    time.sleep(2 ** (attempt - 1))  # 1s, 2s, 4s...
        if method != "getUpdates":
            logger.error("Não foi possível completar %s no Telegram.", method)
        return None


def _error_description(response: requests.Response) -> str:
    """Extrai o motivo do erro que o Telegram envia no corpo da resposta."""
    try:
        return response.json().get("description", "sem descrição")
    except ValueError:  # a resposta não era JSON
        return "resposta inválida do Telegram"


def _demo() -> None:
    """Teste manual: envia uma mensagem de teste para o seu chat."""
    from dotenv import load_dotenv

    logging.basicConfig(level=logging.INFO)
    load_dotenv(override=True)  # o .env tem prioridade (ver main.py)
    bot = TelegramBot.from_env()
    if bot.send_message("✅ <b>Teste</b>: o bot de alertas está conectado!"):
        print("Mensagem enviada! Confira o Telegram.")
    else:
        print("Falha no envio. Veja o log acima e confira o .env.")


if __name__ == "__main__":
    _demo()
