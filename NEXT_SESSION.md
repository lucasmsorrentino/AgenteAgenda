# Próxima sessão na máquina de casa — trocar o treino

O plano de treino foi atualizado para o **Treino Casa ABCDE** (peso corporal,
sem academia), extraído do PDF `Treino_Casa_ABCDE`. O script
`scripts/setup_workout.py` foi reescrito para criar 5 páginas no Anytype
(uma por dia útil) e **substituir** o treino antigo (5x5 de academia).

> ⚠️ Rodar **na máquina de casa**, com o app do Anytype aberto e a Local API
> habilitada (Settings → Local API). O ambiente da nuvem não alcança o Anytype
> (`localhost:31009`) nem tem o `data/token.json` do Google, então isto não roda
> aqui — só na sua máquina.

## Mapa Segunda–Sexta

| Dia     | Treino | Foco                       | Grupos                       |
|---------|--------|----------------------------|------------------------------|
| Segunda | A      | Inferiores                 | Quadríceps / Glúteo          |
| Terça   | B      | Empurrar                   | Peito / Ombro / Tríceps      |
| Quarta  | C      | Puxar                      | Costas / Bíceps              |
| Quinta  | D      | Cadeia Posterior + Core    | Posteriores / Glúteo / Abdômen |
| Sexta   | E      | Metabólico (circuito)      | Gasto calórico, 4-5 voltas   |

## Como rodar

```bash
cd productivity   # diretório onde estão config/, scripts/, services/

# Só Anytype — cria as 5 páginas novas e apaga as páginas do treino antigo
python scripts/setup_workout.py

# Anytype + Google Calendar — também troca os 5 eventos semanais recorrentes
python scripts/setup_workout.py --calendar --time 18:00 --duration 60
```

### Flags úteis
- `--keep-old` — adiciona o novo plano **sem** apagar o treino antigo.
- `--force` — recria as páginas novas mesmo se já existirem com o mesmo nome.
- `--time HH:MM` / `--duration MIN` — horário e duração dos eventos do Calendar.

## O que a troca faz

- **Anytype**: cria `Treino - <Dia> - <Letra>: <Foco>` para cada dia e remove
  qualquer página cujo nome começa com `Treino - ` que não faça parte do novo
  conjunto (ex.: as antigas `Treino - Segunda - Agachamento` etc.). Páginas suas
  fora dessa convenção de nome não são tocadas.
- **Calendar** (com `--calendar`): cancela as séries recorrentes antigas cujo
  título contém `Treino` e cria 5 novas, repetindo de segunda a sexta.

Cada página traz: mobilidade, lista de exercícios com checkbox por série (ou por
volta no circuito de sexta), campo `Carga (mochila): _____` editável em cada
sessão, e uma seção `## Histórico` para registrar as sessões passadas.
