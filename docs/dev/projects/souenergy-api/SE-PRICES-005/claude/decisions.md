# SE-PRICES-005 — Decisões

## 1. Mapeamento/export: aditivo (decisão do dono, aplicada)

Mantidas as 4 tabelas legadas em `config/mapeamento_solo_prices.json`
(`mono_solplanet_620`, `tri_solplanet_620`, `micro_hoymiles_620`,
`micro_hoymiles_710`) exatamente como estavam — mesmas regras, mesma posição
no JSON, sempre presentes no `precios.json` mesmo vazias.

Adicionadas 5 tabelas novas, para o catálogo real (módulos 415W MAXEON):

- `solplanet_string_415` — marca SOLPLANET, inversor string, módulo 415W (sem
  restrição de fase: agrega tri/mono/sem-fase-detectada).
- `hoymiles_micro_415` — marca HOYMILES, microinversor, módulo 415W.
- `huawei_string_415` — marca HUAWEI, inversor string, módulo 415W.
- `sungrow_string_415` — marca SUNGROW, inversor string, módulo 415W.
- `outros` — catch-all sem nenhuma regra: tudo que é um kit com composição
  completa mas não bate com nenhuma das tabelas acima (nem com as legadas).

`schemas/precios.schema.json` foi estendido de forma aditiva: o `required`
da seção `tabelas` continua exigindo só as 4 legadas (compatibilidade com
qualquer consumidor que dependa delas existirem); as 5 novas foram
adicionadas em `propertyNames.enum` e `properties` como opcionais.

## 2. Produtos sem marca/fase detectadas: tabela "outros", não descarte

Em vez de reportá-los como `fuera_mapeo` e sumir do `precios.json` (como
acontecia antes), esses produtos (marca não detectada, ou combinação que não
bate com nenhuma família conhecida) agora aparecem na tabela `outros`, com
os mesmos campos de qualquer outra linha — incluindo `marca: null` quando
aplicável.

Para isso, `schemas/precios.schema.json` também precisou aceitar `marca:
null` (campo `fila.marca` passou de `{"type": "string", "minLength": 1}`
para `{"type": ["string", "null"]}`, no mesmo padrão que `marca_modulo` já
usava). Só afeta consumidores que leem as tabelas novas — as 4 legadas
continuam com marca sempre não-nula, como sempre foi.

## 3. Correção de robustez descoberta durante a implementação: agrupamento de ambiguidade

O código original de `exportar_precos.py` agrupava produtos elegíveis por
`(tabela, potencia_kwp)` para decidir "são a mesma oferta comercial, escolher
a de menor PIX" vs. "são ofertas diferentes com o mesmo kWp, bloquear como
ambíguo". Isso fazia sentido quando cada tabela já fixava marca+tipo+fase+W
(as 4 legadas). Nas tabelas aditivas, que deliberadamente deixam campos livres
(`solplanet_string_415` não fixa fase; `outros` não fixa nada), esse
agrupamento por potência sozinho gerava falsos positivos: um kit monofásico e
um trifásico com o mesmo kWp — ou dois produtos de marcas diferentes que
caem em `outros` com o mesmo kWp — eram tratados como "a mesma oferta com
composições conflitantes" e bloqueados dos dois, mesmo sendo produtos
legítimos e diferentes.

Corrigido incluindo `marca`, `tipo_inversor`, `fase` e `modulo_potencia_w` na
chave de agrupamento, além de `tabela` e `potencia_kwp`. Nas tabelas legadas,
esses campos já são fixos por tabela, então a chave fica redundante e o
comportamento não muda (confirmado pelos testes existentes, que continuam
passando). Testes novos: `test_familias_reais_nao_bloqueiam_por_fase_diferente`
em `tests/test_exportar.py`.

## 4. Branch padrão do repo de publicação: `main`, não `master`

`publicar_precos.py` e `.env.example` assumiam `REPO_PROPUESTAS_BRANCH=master`
como padrão. O repo `propostas-soloenergia` real usa `main` (confirmado via
`git remote show origin` no worktree: `HEAD branch: main`). Corrigido o
default no código e no `.env.example`. Se o VPS tiver um `.env` com
`REPO_PROPUESTAS_BRANCH=master` explícito (sobrescrevendo o default), a
publicação real vai falhar contra o branch errado — checar/corrigir isso no
`/etc/souenergy/souenergy.env` do VPS é um passo de configuração pendente,
fora do escopo de arquivos deste repositório.

## 5. Correção pós-export-real: chave de agrupamento ganhou inversor + modulo

Rodando o export contra o banco real do VPS (171 produtos, não dados
sintéticos), 58 dos 107 produtos elegíveis (~1/3) caíam como `conflictos` e
sumiam do `precios.json`. Causa: a chave de agrupamento do §3
(`tabela, potencia_kwp, marca, tipo_inversor, fase, modulo_potencia_w`) ainda
não distinguia o **modelo do inversor**. Exemplo real (tabela
`hoymiles_micro_415`, potência 1.66 kWp): dois kits com o mesmo
marca/tipo/fase/W — um com HOYMILES HMS-2250DW-4T, outro com HMS-2000DW-4T,
ambos 4× MAXEON 415W em MESA SOLO — caíam no mesmo grupo com composições
"diferentes" (o campo `inversor` diferia) e eram bloqueados os dois, mesmo
sendo dois produtos reais e distintos com preços distintos.

Corrigido incluindo `inversor` e `modulo` na chave de agrupamento
(`exportar_precos.py:proyectar_catalogo`). Com isso, cada variante de
inversor/módulo vira seu próprio grupo (e sua própria linha no export); o
bloqueio por ambiguidade passa a cobrir só divergência real e não resolvível
dentro de um grupo já fixado por inversor+modulo — ou seja, `estrutura` ou
`quantidade_modulos` diferentes para o mesmo inversor+modulo (dado
inconsistente, não uma variante legítima).

`test_ambiguedad_bloquea_linea` foi ajustado para esse cenário remanescente
(mesmo inversor/modulo, `estrutura` diferente → continua bloqueando).
Teste novo `test_variantes_de_inversor_nao_bloqueiam` reproduz o par real
HMS-2250DW-4T/HMS-2000DW-4T e confirma que ambos exportam sem conflito.

## Decisões comerciais NÃO tomadas (fora do escopo desta task)

Ver `implementacao.md` §"Decisões comerciais em aberto".
