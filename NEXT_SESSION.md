# Próxima sessão (rodar na máquina de casa)

> Este arquivo é um **TODO efêmero** para a próxima sessão de Claude Code que rodar
> na máquina onde o Anytype + Google Calendar estão configurados. Após executar
> com sucesso, apague este arquivo (`rm NEXT_SESSION.md`) e faça commit.

## Instruções para o Claude Code

A branch `claude/workout-plan-anytype-kIgOj` traz um plano semanal de treino
(Seg–Sex) que precisa ser materializado no Anytype + Google Calendar. O script
está pronto em `scripts/setup_workout.py`. **Execute conforme o protocolo abaixo.**

### Pré-checagem

Antes de rodar, confirme com o usuário que:

1. O **app Anytype** desktop está aberto e o Local API está habilitado (Settings → Local API).
2. Existe um `.env` válido com `ANYTYPE_API_KEY` e `ANYTYPE_SPACE_ID` (rodar `python scripts/setup_anytype.py` se não houver).
3. Para o calendário: `data/token.json` existe (rodar `python scripts/setup_google.py` se não houver).
4. O usuário confirma o **horário** do treino e a **duração** desejada para os eventos recorrentes — o default do script é `18:00` por `75 min`, mas pergunte antes (use `AskUserQuestion`).

### Execução

```bash
# A partir do diretório raiz do projeto:
python scripts/setup_workout.py --calendar --time HH:MM --duration NN
```

Substitua `HH:MM` e `NN` pelo que o usuário confirmar. Se o usuário preferir
não criar os eventos no Google Calendar agora, omita `--calendar` e rode
apenas a parte do Anytype.

O script é idempotente — re-execuções pulam páginas que já existem com o mesmo
nome. Use `--force` apenas se o usuário pedir para recriar do zero (isso vai
duplicar páginas se as antigas não forem apagadas antes).

### Verificação

Após rodar:

1. Confirme com o usuário que aparecem **5 páginas** novas no Anytype com o
   prefixo `Treino - <Dia da semana> - <Levantamento>`.
2. Se `--calendar` foi usado, confirme que aparecem **5 eventos recorrentes**
   semanais no Google Calendar (um por dia útil).
3. Reporte os IDs criados (saída do script).

### Após sucesso

Apague este arquivo e faça commit:

```bash
git rm NEXT_SESSION.md
git commit -m "chore: remove one-time workout setup todo (executed)"
```

---

## Resumo do design (referência rápida)

Decidido em conversa anterior (Opção B — objeto único por dia, sem timer):

| Dia | Mobilidade (5 min) | Levantamento Principal | Séries × Reps | Acessório / Core |
|-----|---------------------|------------------------|---------------|------------------|
| Seg | Alongamento de punhos; Espalmada na parede | **Barra Fixa (Carga)** | 5×5 | Paralelas; Abdominal Bicicleta |
| Ter | Agachamento profundo (hold); Cossack Squat | **Agachamento** | 5×5 | Abdominal Supra (com carga) |
| Qua | Rotação de ombros (bastão); Alongamento dinâmico de peito | **Supino Reto** | 5×5 | Barra Fixa; Abdominal Oblíquo |
| Qui | World's Greatest Stretch; Good Morning (só barra) | **Levantamento Terra** | 2×5 | Abdominal Infra (Elevação) |
| Sex | Rotação de escápulas; Mobilidade Torácica (parede) | **Militar (OHP)** | 5×5 | Remada Curvada; Prancha |

Aquecimento em pirâmide (antes do principal, todos os dias):
- 1×10 só com a barra (ou só corpo)
- 1×5 com 40% da carga final
- 1×3 com 70% da carga final

Descanso: 2 min entre séries do principal, 1 min entre acessórios.

**Sem timer automático no Anytype** — usar app de intervalo externo no celular.

**Persistência de carga:** o campo `Carga atual: _______` em cada página é
editado in-place a cada sessão. **Histórico:** ao final de cada sessão,
acrescentar uma linha na seção `## Histórico` no formato:

```
YYYY-MM-DD | Principal: SxR @ XXkg | AcessórioN: SxR @ YYkg | obs
```

Formato consistente entre os dias para que um agente futuro consiga parsear a
sequência e propor progressão de carga / novos exercícios.
