# Legado — dashboard Streamlit (desativado)

Primeira versão do dashboard BKO: upload manual do CSV `ticket_x_productions`, ETL em pandas, dashboard em Streamlit, exportação XLSX/HTML e envio por e-mail.

**Fora de uso desde out/2026.** O dashboard oficial agora é o do Apps Script, em [`../apps_script/`](../apps_script/LEIAME.md), que busca os dados direto do Metabase.

Os arquivos foram mantidos só para consulta. Não recebem correções. Duas métricas daqui estão **erradas** e foram corrigidas apenas no Apps Script:

- **Tempo de resposta:** aqui usa `answered_at`, que no Metabase é `it.updated_at` (a última atualização do ticket). O correto é a primeira mensagem do Admin.
- **Analista:** aqui é o último remetente de qualquer tipo, então às vezes é o próprio consultor. O correto é o último remetente Admin.

A documentação completa desta versão está em [`PROJETO.md`](PROJETO.md).
