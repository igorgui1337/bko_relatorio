# Apuração — Tempo de resposta do BackOffice (2026)

**Data da apuração:** 16/10/2026
**Base:** todas as mensagens de tickets abertos de **01/01 a 16/10/2026**, baixadas direto da pergunta 274 do Metabase. São 176.935 mensagens de **58.976 tickets**.
**Método:** cálculo independente em Python (pandas), sem usar o código do painel. Depois, comparação ticket a ticket com o que o painel mostra.

---

## Conclusão

**O tempo médio de resposta mostrado hoje está subestimado.** A causa é uma só e foi identificada:

> **9.530 tickets (16%) foram abertos pelo próprio BackOffice** (emissor = Admin). O painel mede a resposta como "1ª mensagem do BO − abertura". Nesses tickets, a 1ª mensagem do BO **é a própria abertura**, então o tempo entra como **0 minuto**.

Esses zeros puxam a média para baixo e inflam os indicadores de rapidez.

| Indicador (2026) | Painel hoje | **Real (corrigido)** |
|---|---|---|
| Tempo médio de resposta | 27,1 h (1d 3h) | **32,0 h (1d 8h)** |
| Mediana | 17,8 h | **19,7 h** |
| Respondidos em até 1h | 21,5% | **6,2%** |
| Respondidos em até 24h | 73,0% | **67,6%** |
| Respostas registradas como "0 min" | 9.538 | **9** |

O cálculo do painel em si está consistente: nos 57.879 tickets em comum, o tempo do painel bate com a fórmula "1ª mensagem Admin − abertura" em **todos os casos comparáveis** (0 diferenças). **O erro está na definição, não na conta.**

---

## Definições comparadas

| Definição | Como mede | Média | Mediana | Até 24h |
|---|---|---|---|---|
| **A · painel atual** | 1ª mensagem do BO − abertura | 27,1 h | 17,8 h | 73,0% |
| **B · corrigida (recomendada)** | **Ticket aberto pelo consultor:** 1ª mensagem do BO − abertura. **Ticket aberto pelo BO:** 1ª mensagem do BO **depois** da 1ª mensagem do consultor. | **32,0 h** | **19,7 h** | **67,6%** |
| B em **horas úteis** | Igual à B, descontando sábados, domingos e feriados nacionais | **21,8 h** | **18,2 h** | **80,4%** (até 1 dia útil) |
| C · `answered_at` (legado/Streamlit) | Última atualização do ticket − abertura | 119,2 h | 23,2 h | 51,9% |

Observações:
- **Definição C:** é o número que o Streamlit antigo mostrava, e provavelmente o que circulava antes. Ele **não é tempo de resposta**: o `answered_at` do Metabase é a última atualização do ticket (`it.updated_at`).
- **Tickets abertos pelo BO:**
  - **3.671** tiveram resposta do consultor e entram na definição B, com média de 27,7h.
  - **5.859** nunca tiveram mensagem do consultor e ficam fora da média, porque não há o que responder.
- **Tickets sem nenhuma resposta do BO:** 302 também ficam fora da média.

---

## Por mês (data de abertura)

| Mês | Tickets | Painel · média | **Real · média** | **Real · mediana** | Real · média em horas úteis |
|---|---|---|---|---|---|
| jan | 5.188 | 34,5 h | **40,0 h** | 23,9 h | 28,6 h |
| fev | 4.780 | 29,9 h | **35,3 h** | 21,3 h | 24,2 h |
| mar | 6.310 | 22,2 h | **26,3 h** | 19,0 h | 19,2 h |
| abr | 5.743 | 27,6 h | **33,1 h** | 18,7 h | 19,9 h |
| mai | 5.965 | 26,3 h | **30,4 h** | 19,7 h | 21,1 h |
| jun | 6.186 | 28,2 h | **32,7 h** | 20,6 h | 23,7 h |
| jul | 7.335 | 29,1 h | **33,7 h** | 20,2 h | 22,6 h |
| ago | 7.557 | 25,1 h | **31,6 h** | 18,3 h | 21,5 h |
| set | 6.881 | 24,6 h | **29,2 h** | 18,1 h | 19,3 h |
| out (até 16/10) | 3.031 | 23,3 h | **27,1 h** | 17,4 h | 15,9 h |

A tendência é de melhora ao longo do ano, de 40h em janeiro para 27–29h em set/out. Ela aparece nas duas definições. O painel só erra o nível.

---

## Por departamento (2026)

| Departamento | Tickets | Abertos pelo BO | Painel · média | **Real · média** | **Real · mediana** | Real · média em horas úteis | Respondidos em até 1 dia útil |
|---|---|---|---|---|---|---|---|
| CS/CX | 33.796 | 702 | 25,4 h | **25,9 h** | 18,6 h | 17,4 h | 89% |
| **Operações** | 12.155 | **6.204** | **16,8 h** | **32,6 h** | 22,4 h | 22,9 h | 69% |
| Comissões | 2.447 | 21 | 37,5 h | **37,8 h** | 22,8 h | 25,0 h | 68% |
| W1 Capital | 2.423 | 7 | 40,3 h | **40,4 h** | 15,0 h | 28,1 h | 76% |
| **Especialistas** | 2.082 | **1.912** | **2,4 h** | **25,7 h** | 18,0 h | 17,4 h | 83% |
| TECH | 1.523 | 31 | 78,7 h | **79,8 h** | 47,7 h | 53,7 h | 39% |
| Financeiro | 1.315 | 66 | 49,6 h | **51,3 h** | 24,1 h | 34,7 h | 59% |
| **Jurídico** | 1.253 | **583** | **28,5 h** | **49,9 h** | 22,4 h | 35,9 h | 62% |
| Treinamentos | 946 | 3 | 38,2 h | **38,3 h** | 21,2 h | 28,1 h | 67% |
| Fiscal | 561 | 1 | 90,2 h | **90,2 h** | 60,3 h | 61,3 h | 33% |
| Sales Ops | 372 | 0 | 56,9 h | **56,9 h** | 34,0 h | 42,3 h | 56% |
| Marketing | 50 | 0 | 262,1 h | **262,1 h** | 135,6 h | 183,7 h | 31% |

**Os departamentos mais distorcidos são os que mais abrem tickets para o consultor:**
- **Operações:** 6.204 abertos pelo BO, com média de 16,8h no painel contra 32,6h real.
- **Especialistas:** 1.912 abertos pelo BO, com 2,4h no painel contra 25,7h real.
- **Jurídico:** 583 abertos pelo BO, com 28,5h no painel contra 49,9h real.

Os assuntos com mais abertura pelo BO são:
- Operações - Validação de PPs (3.916);
- Produtos Parceiras - Acompanhamento, Envio de Formulários e Cancelamento (2.288);
- Produtos Parceiras - Pendências de Seguro de Vida (1.912);
- CS/CX - W1 Acompanhamento (Cancelamento) (578);
- Jurídico - Dúvidas sobre Liberação de BP no W1nner (568).

---

## Onde isso aparece nos painéis

- **Painel completo (`Index`):** o cartão "Tempo médio de resposta" mostra a média da definição A ("1d 3h" em 2026), e o cartão "Respondidos em até 10h" também está inflado. O mesmo vale para a coluna "TM resposta" nas tabelas por departamento, escritório e analista.
- **Painel de tempo real (`?v=painel`):** o cartão "1ª resposta (mediana)" usa a definição A. É por isso que a semana de 12–18/10 mostrava **3min**: boa parte dos tickets da semana foi aberta pelo BO.
- **Relatório de atrasos e SLA:** **não são afetados.** O atraso usa a última mensagem e a transferência, e não o tempo de 1ª resposta.

---

## Correção aplicada (16/10/2026)

A definição B foi aplicada **em horas úteis**, a pedido do Igor:
- em `Code.gs`: `consolidar_` com `issuer_type` e `horasUteis_`;
- versão dos dados `VERSAO_DADOS = 4`.

**Conferência:** nos **52.838 tickets** com tempo de resposta, o valor do painel é **igual** ao desta apuração independente (0 diferenças). Os tickets sem tempo também coincidem (6.140).

**Números de 2026 no painel:**
- **média:** 21h 47min;
- **mediana:** 18h 12min;
- **80% respondidos em até 1 dia útil.**

## Correção proposta (registro)

Em `apps_script/Code.gs` (`consolidar_`), a 1ª resposta passa a ser:
- **ticket aberto pelo consultor** (`issuer_type = Consultant`): 1ª mensagem Admin − abertura (como hoje);
- **ticket aberto pelo BO** (`issuer_type = Admin`): 1ª mensagem Admin **depois da 1ª mensagem do consultor** − essa mensagem do consultor;
- **sem mensagem do consultor:** sem tempo de resposta (fica fora da média).

A coluna `issuer_type` já vem na pergunta 274. Como os valores salvos mudam, é preciso subir `VERSAO_DADOS`, o que força a carga completa na próxima atualização.

**Para decidir:** mostrar em horas corridas (como hoje) ou em **horas úteis**, coerente com o SLA de 1 dia útil. Em horas úteis, a média de 2026 cai de 32,0h para 21,8h, e "até 1 dia útil" fica em 80,4%.
