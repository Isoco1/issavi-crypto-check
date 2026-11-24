# Issavi Crypto Check

Bot em Python que monitora o mercado de criptomoedas pela **API pública da
Binance** e envia alertas, resumos e gráficos pelo **Telegram**.

> Só lê dados públicos de mercado: não usa chave de API e não tem nenhuma
> função de compra, venda ou acesso a conta.

<p align="center">
  <img src="docs/telegram.png" alt="Bot respondendo no Telegram com resumo, gráfico e /status" width="800">
</p>

## Funcionalidades

- **Alerta de variação:** avisa quando a variação de 24h passa de ±3% (configurável).
- **Alerta de preço-alvo:** avisa quando o preço cruza um valor definido, para cima ou para baixo.
- **Cooldown:** não repete o mesmo alerta por 30 minutos, mesmo após reiniciar o bot.
- **Resumo diário:** preço, variação e volume de cada moeda, com gráfico comparativo das últimas 24h.
- **Comandos no Telegram:** `/preco`, `/grafico`, `/status` e `/ajuda`, respondidos na hora e só para o chat autorizado.
- **Histórico em SQLite:** preços e alertas enviados ficam salvos localmente.

## Tecnologias

| | |
|---|---|
| **Python 3** | `requests`, `python-dotenv`, `logging`, `sqlite3`, `argparse` |
| **API pública da Binance** | `/api/v3/ticker/24hr` e `/api/v3/klines` |
| **Telegram Bot API** | `sendMessage`, `sendPhoto` e `getUpdates` (long polling) |
| **matplotlib** | Gráficos gerados em memória e enviados como imagem |
| **pytest** | 43 testes com dados simulados, sem acesso à rede |

## Destaques técnicos

- **Resiliência:** timeout em toda requisição e novas tentativas com espera crescente (1s, 2s, 4s…). O bot respeita o `Retry-After` da Binance (HTTP 429/418) e não repete erros 4xx, que não se resolvem tentando de novo. Uma falha temporária nunca derruba o bot.
- **Uso eficiente da API:** todas as moedas são consultadas numa única requisição, com intervalo configurável (padrão de 60s).
- **Comandos sem threads:** o tempo entre uma verificação e outra é usado para escutar o Telegram, então o bot responde na hora com um único fluxo de execução.
- **Lógica testável:** as regras de alerta não acessam rede nem banco, e o horário é passado como parâmetro para os testes simularem a passagem do tempo.
- **Segurança:** token e chat_id ficam no `.env` (fora do Git), o token nunca vai para o log, o bot só responde ao chat configurado e o SQL usa consultas parametrizadas.

## Como rodar

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # preencha TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID
python main.py              # roda continuamente
```

| Comando | Uso |
|---|---|
| `python main.py --once` | Faz uma verificação e encerra |
| `python main.py --test-summary` | Envia o resumo diário agora |
| `python -m pytest` | Roda os testes |

Moedas, limites, preços-alvo e horário do resumo ficam no [`config.json`](config.json).
Se `api.binance.com` estiver bloqueado na sua rede, troque `base_url` para
`https://data-api.binance.vision`.

**Telegram:** crie o bot com o [@BotFather](https://t.me/BotFather), mande uma
mensagem para ele e pegue o `chat.id` em
`https://api.telegram.org/bot<TOKEN>/getUpdates`. Cuidado para não copiar o
`update_id` no lugar dele.

## Estrutura

```
main.py            loop principal, resumo diário, logs
binance_client.py  API pública da Binance (com retry)
alerts.py          regras de alerta e cooldown
telegram_bot.py    mensagens, fotos e leitura de comandos
commands.py        /preco, /grafico, /status, /ajuda
storage.py         histórico em SQLite
charts.py          gráficos com matplotlib
tests/             testes com pytest
```
