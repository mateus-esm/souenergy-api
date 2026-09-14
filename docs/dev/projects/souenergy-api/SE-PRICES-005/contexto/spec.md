# SE-PRICES-005 — Task Contract

Project: souenergy-api (+ propostas-soloenergia)
Agent: claude (revisão + correção end-to-end)
Branch: feat/se-prices-end-to-end
Risk: production (scraper + export + publicação + UI)

## Outcome

Fazer o pipeline de preços da SouEnergy funcionar **de ponta a ponta**, corrigindo o que
está quebrado e entregando os três objetivos do dono:

1. **Scraper de coleta de dados da SouEnergy** — coleta completa, confiável, com login
   validado e sem dados corrompidos.
2. **Armazenamento dos dados de forma útil** — o SQLite e o `precios.json` exportado
   devem representar o catálogo REAL, de forma consumível (não vazio, não forçado em
   tabelas legadas que não correspondem).
3. **Apresentação dos itens de forma clara em solo-prices** — a página
   (https://propostas.soloenergia.com.br/solo-prices/) deve mostrar os itens coletados
   com uma UI bonita e funcional.

## Context (evidência real, 14/09/2026)

### Estado da coleta (VPS, systemd `souenergy-scrape.service`)
- Scrape `931b1e0fc8de435f8b9f135eb8280df3`: status **`ok`**, **171 produtos descobertos**,
  **171 detalhes válidos**, **0 falhas**, 171 preços capturados, `historico_precios` = 171.
- Login funciona; paginação completa; os bugs anteriores de `quantidade_modulos`
  (lia 4 ou 2026) e `potencia_kwp` (lia o slug da URL) já foram corrigidos (PR #4, #5).

### O BLOQUEIO REAL (por que o `precios.json` sai vazio)
- `config/mapeamento_solo_prices.json` (herdado) mapeia SOMENTE 4 tabelas com
  `modulo_potencia_w` 620 ou 710:
  `mono_solplanet_620`, `tri_solplanet_620`, `micro_hoymiles_620`, `micro_hoymiles_710`.
- O catálogo real coletado é de módulos **415 W (MAXEON)**. Resultado: 170 de 171 produtos
  caem como `fuera_mapeo` e o `precios.json` publicado tem as 4 tabelas VAZIAS:
  `{"micro_hoymiles_620": [], "micro_hoymiles_710": [], "mono_solplanet_620": [], "tri_solplanet_620": []}`.
- `schemas/precios.schema.json` também fixa `required` + `propertyNames.enum` nas 4 tabelas
  legadas — precisa ser estendido de forma aditiva.

### Distribuição REAL do catálogo aprovado (171 produtos, todos `disponible`)
```
SOLPLANET  string  tri   415W  -> 40
(none)     string  none  415W  -> 24
(none)     string  tri   415W  -> 24
HOYMILES   micro   none  415W  -> 17
HUAWEI     string  none  415W  -> 17
HUAWEI     string  tri   415W  -> 14
SUNGROW    string  tri   415W  ->  9
(none)     string  mono  415W  ->  6
SOLPLANET  string  none  415W  ->  6
SUNGROW    string  none  415W  ->  4
HOYMILES   micro   bi    415W  ->  3
(none)     string  bi    415W  ->  1
(none)     string  tri   620W  ->  1
HOYMILES   string  none  none  ->  1
HOYMILES   string  none  415W  ->  1
HUAWEI     string  tri   710W  ->  1
SOLPLANET  string  mono  415W  ->  1
SUNGROW    string  bi    415W  ->  1
```

### Estado da UI (propostas-soloenergia, já em produção)
- `solo-prices/index.html` (~97 KB) já tem: login (`solo`/`solo2026`, `sess_solo_prices`),
  seletor de potência, **aba de preços brutos** (carrega `precios.json` same-origin com
  fallback) e **combinador de equipamentos** com subtotais e bloqueio de mistura de origens.
- A UI já renderiza **qualquer** nome de tabela (`Object.keys(tabelas)`), não só as 4 legadas.
- Verificação existente: `solo-prices/.verify-prices.js` (93 checagens) e `.verify-data.js`.
- Em produção `precios.json` responde 404 (nunca foi publicado).

## Perguntas a resolver (com decisão registrada)

1. **Mapeamento/export**: como publicar o catálogo REAL sem perder as 4 tabelas legadas?
   Preferência do dono: **aditivo** — manter as legadas (mesmo vazias) e acrescentar as
   famílias reais (ex.: `solplanet_string_415`, `hoymiles_micro_415`, `huawei_string_415`,
   `sungrow_string_415`, ...). Estender `schema` e `mapeamento` de forma compatível.
2. **Produtos sem marca/fase detectadas** (~70): como representar de forma útil em vez de
   descartar? Ex.: agrupar por marca inferida do inversor/módulo, ou uma tabela `outros`.
3. **Publicação**: `PUBLICAR_AUTOMATICAMENTE=false` e `REPO_PROPUESTAS` vazio no VPS.
   Como publicar `solo-prices/precios.json` no repo propostas (e o que precisa ser
   configurado no VPS sem expor segredos)?
4. **UI**: a apresentação dos itens coletados está clara e bonita? Revisar a aba de
   preços brutos com dados reais (filtros, ordenação, legibilidade, estado vazio,
   responsividade). Ajustar o que não estiver bom.
5. **Frescor/limite**: `DIAS_VALIDADE_PRECOS=30` é provisório — confirmar ou ajustar.
6. **Verificação**: como provar end-to-end (coleta → DB → export → publicação → UI)
   com evidência reproduzível.

## Constraints

- **Idioma: português brasileiro** em artefatos e na UI. Nunca espanhol.
- Não citar nem commitar credenciais reais (`.env`, `/etc/souenergy/souenergy.env`).
- Não alterar `main`/`master` diretamente. Não tocar em assets compartilhados nem pastas de clientes.
- Não inventar margem/fórmula comercial. Preço do distribuidor ≠ preço comercial; a UI já
  rotula e bloqueia mistura — manter.
- Manter o site funcional quando `precios.json` estiver ausente (fallback).
- Escrever somente dentro dos worktrees.

## Acceptance

- [ ] Coleta validada com evidência real (contagens, 0 falhas, preços)
- [ ] `precios.json` publica o catálogo REAL (não vazio), de forma útil e aditiva
- [ ] Schema e mapeamento estendidos sem quebrar o contrato existente
- [ ] `solo-prices` mostra os itens coletados com UI clara e funcional
- [ ] Verificação automatizada passando (sem regressão das existentes)
- [ ] PR(s) abertos e deploy verificado em produção
- [ ] Decisões comerciais abertas registradas, não inventadas

## Do not touch

- Segredos de produção / `.env`
- `main`/`master` diretamente
- Infraestrutura destrutiva
