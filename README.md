# AgenteAgenda

Bot pessoal de produtividade em Português que integra **Telegram + Google Calendar + Anytype**, com classificação e busca inteligentes feitas pelo **Claude Code** em modo headless (usa a assinatura Max do usuário — sem necessidade de API key paga).

> Roda como um único processo Python local (long-polling + APScheduler). Sem servidor web, sem deploy.

---

## Recursos

- **Agenda bidirecional** — Google Calendar é a fonte da verdade; eventos são espelhados para o Anytype como objetos estruturados do tipo `Compromisso` com `start`, `end`, `location`, `recurring`.
- **Recorrência em linguagem natural** — `repete seg, qua, sex`, `dias uteis`, `mensal dia 15`, `semanal ate 30/06`, `3 semanas`, `diario 10 vezes` (parser em [`services/recurrence.py`](services/recurrence.py)).
- **Edição com escopo** — para eventos recorrentes, o bot pergunta via botões inline se a mudança aplica-se a esta instância ou a toda a série.
- **Classificação automática (`/ia`)** — mensagens em linguagem natural viram tarefa/compromisso/nota + tags/área/prioridade numa única chamada ao Claude.
- **Classificação em lote (`/classificar`)** — processa todos os itens com `classified_at` vazio de uma vez.
- **Busca semântica (`/buscar`)** — pergunta livre sobre suas tarefas/notas/eventos, resposta em pt-BR com IDs dos itens citados.
- **Allowlist** — somente o dono e IDs aprovados podem usar o bot.

---

## Requisitos

- Python 3.11+
- Windows / macOS / Linux
- [Claude Code CLI](https://docs.claude.com/claude-code) instalado e autenticado na assinatura Max (comando `claude` no `PATH`)
- [Anytype desktop](https://anytype.io/) com a **Local API** ativada (Configurações → Local API)
- Projeto no Google Cloud Console com **Google Calendar API** habilitada (OAuth desktop)
- Bot do Telegram criado com [@BotFather](https://t.me/BotFather)

---

## Setup

```bash
git clone https://github.com/lucasmsorrentino/AgenteAgenda.git
cd AgenteAgenda
pip install -r requirements.txt

cp .env.example .env   # edite com seus tokens
```

Todos os scripts assumem que o diretório corrente é a raiz do projeto.

### 1. Google Calendar

1. No [Google Cloud Console](https://console.cloud.google.com/), crie credenciais OAuth 2.0 do tipo **Desktop** e baixe o JSON para `data/credentials.json`.
2. Rode:

   ```bash
   python scripts/setup_google.py
   ```

   Abrirá o fluxo OAuth no navegador; o token fica em `data/token.json`.

### 2. Anytype

1. Abra o Anytype desktop, vá em **Configurações → Local API** e ative.
2. Rode:

   ```bash
   python scripts/setup_anytype.py
   ```

   Um código de 4 dígitos aparecerá no Anytype; digite-o no terminal. O script cria as propriedades e tipos customizados (`Compromisso`, `classified_at`, `area`, `prioridade`, etc.) e salva o mapeamento em `data/anytype_schema.json`. Copie `ANYTYPE_API_KEY` e `ANYTYPE_SPACE_ID` impressos ao final para o `.env`.

### 3. Telegram

1. Converse com [@BotFather](https://t.me/BotFather), crie um bot, cole o token em `TELEGRAM_BOT_TOKEN`.
2. Inicie o bot, envie `/start` — o chat_id aparece no log. Coloque-o em `TELEGRAM_CHAT_ID`.

---

## Uso

```bash
python scripts/run_bot.py
```

O bot registra os comandos no menu do Telegram automaticamente.

### Comandos principais

| Comando | Descrição |
| --- | --- |
| `/add Texto @prazo` | Cria tarefa `[TODO]` no Calendar + Tarefa no Anytype. |
| `/novo Texto @quando [ate HH:MM] [em local] [repete regra]` | Cria compromisso real (não-`[TODO]`), com ou sem recorrência. |
| `/agenda [dias]` | Lista compromissos com prefixo de 6 caracteres do ID; 🔁 marca recorrentes. |
| `/editar <id> campo=valor` | Campos: `titulo`, `inicio` (DD/MM HH:MM), `fim` (HH:MM), `local`. Para recorrentes, pergunta o escopo. |
| `/cancelar <id>` | Mesma pergunta de escopo para recorrentes. |
| `/today` | Eventos e tarefas de hoje. |
| `/prazos` | Agrupa tarefas por urgência (atrasadas → hoje → amanhã → semana → depois). |
| `/done N` | Marca tarefa N (de `/todos`) como concluída. |
| `/note Texto` | Salva nota rápida no Anytype; hashtags viram tags. |
| `/recap` | Gera o resumo noturno manualmente. |
| `/sync` | Força sincronização Calendar → Anytype. |
| `/allowlist` | Gerencia IDs autorizados (só o dono). |

### Comandos com IA (usam `claude -p` via subprocess)

| Comando | Descrição |
| --- | --- |
| `/ia <texto livre>` | Parser one-shot. Ex.: `/ia marca dentista quarta 14h`, `/ia anota ideia sobre tcc`, `/ia cancela reuniao de amanha`. Retorna ação + classificação numa única chamada. |
| `/classificar` | Classifica em lote todos os itens do Anytype com `classified_at` vazio. Taxonomia fixa em [`config/labels.py`](config/labels.py). |
| `/buscar <pergunta>` | Busca natural sobre tarefas/notas/eventos recentes. Retorna resposta em pt-BR + IDs citados. |

---

## Arquitetura

```
┌────────────────┐    ┌───────────────────┐    ┌────────────────┐
│  Telegram Bot  │────│  services/*       │────│ integrations/* │
│ (long-polling) │    │  (business logic) │    │ (API clients)  │
└────────────────┘    └───────────────────┘    └────────────────┘
       │                      │                        │
       │                      │                        ├─→ Google Calendar
       │                      ├─→ ai_subprocess───────→│   Anytype (local REST)
       │                      │   (claude -p)
       │                      └─→ APScheduler (reminders, sync)
       │
       └─→ .env, data/ (tokens, sync_state, schema)
```

### Diretórios

- `config/` — `settings.py` (env vars), `labels.py` (taxonomia fixa de áreas/prioridades/tags).
- `integrations/` — `google_calendar.py`, `anytype_client.py`, `telegram_bot.py`.
- `services/` — `calendar_sync.py`, `recurrence.py`, `reminders.py`, `morning_summary.py`, `evening_recap.py`, `task_manager.py`, `ai_subprocess.py`, `ai_parser.py`, `ai_classifier.py`, `ai_search.py`.
- `scripts/` — entry points: `run_bot.py`, `run_morning.py`, `run_evening.py`, `run_classify.py`, `setup_*.py`.
- `data/` — artefatos de runtime (gitignorados exceto `anytype_schema.json`).
- `tests/` — bateria com ~100 testes unitários (pytest).

### Fluxo de sincronização

`Calendar` é a fonte da verdade. `services/calendar_sync.py` mantém `data/sync_state.json` mapeando cada `google_event_id` → `{anytype_id, updated, start_iso, type}`. A cada sync:

- **Novo evento** (id não visto antes) → cria objeto no Anytype.
- **Alterado** (`updated` timestamp do Google mudou) → aplica patch.
- **Faltando dentro da janela** → deleta o objeto.

Sincronizações incrementais após `/novo`, `/editar`, `/cancelar` e `/ia` passam `only_event_ids={...}` para evitar falso-positivo de delete. O sync geral roda a cada 6 h.

### Classificação

- Propriedade `classified_at` (date) no Anytype marca itens já processados. É a única fonte da verdade para a fila.
- `/ia` seta `classified_at` + tags/área/prioridade no momento da criação (1 subprocess).
- `/classificar` pega todos os itens com `classified_at` vazio, faz uma única chamada com até ~80 itens por batch, aplica tudo de volta.
- `clamp_to_taxonomy()` sanitiza o retorno do LLM contra `config/labels.py` — valores inventados viram defaults seguros (`area="pessoal"`, `prioridade="baixa"`).

---

## Testes

```bash
pip install pytest pytest-asyncio
python -m pytest
```

A suite cobre:

- `services/recurrence.py` — 27 casos de parsing pt-BR → RRULE
- `services/ai_subprocess.py` — extração de JSON com prosa, fences, strings com chaves
- `services/ai_classifier.py` — clamp de taxonomia, batching, falhas do LLM, mismatch de ids
- `services/ai_parser.py` e `services/ai_search.py` — normalização da resposta, erros
- `services/calendar_sync.py` — migração do formato legado, roundtrip, descrição formatada
- `integrations/anytype_client.py` — CRUD via `httpx.MockTransport`, classificação condicional
- `integrations/telegram_bot.py` — parsers `_parse_deadline`, `_parse_novo`, `_parse_editar_fields`
- `config/labels.py` — invariantes da taxonomia

---

## Segurança

Todos os segredos são lidos de variáveis de ambiente via [`config/settings.py`](config/settings.py) — **nunca hardcoded**. Artefatos sensíveis (`.env`, `data/credentials.json`, `data/token.json`, `data/anytype_key.txt`, logs) estão em `.gitignore`. O bot aplica uma allowlist baseada em `TELEGRAM_CHAT_ID` + `TELEGRAM_ALLOWED_IDS` e recusa comandos de chats não autorizados.

Antes de contribuir, confirme que nenhum token vazou:

```bash
git log -p | grep -Ei "token|api.key|secret" | head
```

---

## Licença

[MIT](LICENSE).

---

## Agradecimentos

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- [APScheduler](https://apscheduler.readthedocs.io/)
- [Anytype](https://anytype.io/) (local API)
- [Claude Code](https://docs.claude.com/claude-code)
