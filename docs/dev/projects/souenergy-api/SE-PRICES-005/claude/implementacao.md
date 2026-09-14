# SE-PRICES-005 — Implementação

Revisão e correção end-to-end do pipeline de preços SouEnergy: mapeamento e
exportador (`souenergy-api`) + UI de preços brutos (`propostas-soloenergia`).

Decisões detalhadas em [`decisions.md`](./decisions.md).

## O que estava quebrado

A coleta (scraper) já funcionava corretamente (171/171 produtos, 0 falhas,
login validado). O bloqueio era 100% no export: `config/mapeamento_solo_prices.json`
só reconhecia módulos 620W/710W (SOLPLANET/HOYMILES), mas o catálogo real é de
módulos 415W (MAXEON). Resultado: 170 dos 171 produtos caíam como
`fuera_mapeo` e as 4 tabelas do `precios.json` publicado saíam vazias.

## Arquivos alterados

### `souenergy-api`

- `config/mapeamento_solo_prices.json` — mantidas as 4 tabelas legadas
  (620W/710W); adicionadas 5 tabelas reais (415W): `solplanet_string_415`,
  `hoymiles_micro_415`, `huawei_string_415`, `sungrow_string_415`, `outros`
  (catch-all).
- `exportar_precos.py`:
  - `_tabla_para`: campos de `reglas` (marca, tipo_inversor,
    modulo_potencia_w) agora são opcionais, no mesmo padrão que `fase` já
    usava — necessário para as tabelas aditivas mais amplas.
  - `proyectar_catalogo`: chave de agrupamento de ambiguidade passou de
    `(tabela, potencia_kwp)` para `(tabela, potencia_kwp, marca,
    tipo_inversor, fase, modulo_potencia_w)` — corrige falsos bloqueios entre
    produtos legitimamente diferentes que caem na mesma tabela aditiva (ver
    `decisions.md` §3).
- `schemas/precios.schema.json` — `tabelas.propertyNames.enum` e
  `tabelas.properties` estendidos com as 5 tabelas novas (opcionais; as 4
  legadas continuam em `required`); `fila.marca` passou a aceitar `null`
  (mesmo padrão de `marca_modulo`), para representar produtos sem marca
  detectada em vez de descartá-los.
- `tests/test_exportar.py` — `test_cuatro_tablas_y_contenido` atualizado para
  o comportamento aditivo (as 4 legadas continuam existindo e não regridem;
  o item HUAWEI 620W que antes ficava fora agora cai em `outros` em vez de
  ser descartado). Adicionados `test_familias_reais_415w_aditivo` e
  `test_familias_reais_nao_bloqueiam_por_fase_diferente`.
- **Correção pós-export-real (ver `decisions.md` §5)**: `exportar_precos.py`
  — chave de agrupamento em `proyectar_catalogo` estendida com `inversor` e
  `modulo`, para não bloquear como "conflito" variantes legítimas que só
  diferem no modelo do inversor (ex.: HOYMILES HMS-2250DW-4T vs
  HMS-2000DW-4T, mesmo módulo/estrutura/quantidade). `tests/test_exportar.py`
  — `test_ambiguedad_bloquea_linea` ajustado para o cenário de ambiguidade
  remanescente (mesmo inversor/modulo, `estrutura` diferente); teste novo
  `test_variantes_de_inversor_nao_bloqueiam` reproduz o par real do VPS.
- `.env.example` e `publicar_precos.py` — `REPO_PROPUESTAS_BRANCH` default
  corrigido de `master` para `main` (bug real: o repo `propostas-soloenergia`
  usa `main`; confirmado via `git remote show origin`).

### `propostas-soloenergia`

- `solo-prices/index.html`:
  - Nova função `rotularTabela()` — rótulo legível para o nome técnico da
    tabela/família (ex.: `solplanet_string_415` → "Solplanet string 415"),
    usada na coluna "Kit" da aba de preços brutos.
  - Novo filtro "Família" na aba de preços brutos (lado SouEnergy), populado
    dinamicamente a partir das tabelas presentes no `precios.json` recebido
    — antes só existia esse filtro do lado da tabela própria. Reaproveita o
    campo `familia` que `filtrarLinhas()` já suportava (não mudou lógica
    pura, só a UI).
  - `marca`/`fase` vazios (produtos em `outros`) agora mostram "Sem marca" /
    "—" em vez de célula em branco.
  - Login, sessão (`sess_solo_prices`), seletor de potência, combinador e
    fallback de `precios.json` ausente **não foram tocados**.

## Verificação — comandos reais e saída real

### 1. Suíte de testes (souenergy-api), baseline vs. depois

Baseline (antes de qualquer mudança):
```
$ python3 -m pytest tests/ -q --no-header
........................................................................ [ 66%]
.....................................                                    [100%]
109 passed, 1 warning in 12.06s
```

Depois das mudanças:
```
$ python3 -m pytest tests/ -q --no-header
........................................................................ [ 64%]
.......................................                                  [100%]
111 passed, 1 warning in 10.76s
```
111 = 109 baseline + 2 testes novos (`test_familias_reais_415w_aditivo`,
`test_familias_reais_nao_bloqueiam_por_fase_diferente`). Nenhuma falha
mascarada — o único teste que precisou mudar (`test_cuatro_tablas_y_contenido`)
foi atualizado para refletir o comportamento aditivo pretendido, não para
esconder uma regressão.

### 2. Export real, com números — prova de que as tabelas não estão mais vazias

Não há acesso ao SQLite de produção (só existe no VPS). Para provar o
export **de verdade** (não um mock), reconstruí em SQLite real a distribuição
exata reportada em `contexto/evidencia-catalogo.txt` (171 produtos aprovados,
mesma quebra por marca/tipo/fase/W) e rodei `exportar_precos.construir_precios_json`
e `exportar_precos.exportar_precios` contra esse banco, usando o código do
repositório sem nenhum atalho:

```
=== VERIFICAÇÃO REAL (dados sintéticos reproduzindo evidencia-catalogo.txt, 171 produtos) ===
produtos na coleta: 171
elegidos (exportados): 170
no_mapeados: 1 -> [{'url': 'https://souenergy.com.br/kit-167.html', 'motivo': 'composicion_incompleta'}]
conflictos: 0 -> []
erros de contrato: []

contagem por tabela:
  mono_solplanet_620: 0
  tri_solplanet_620: 0
  micro_hoymiles_620: 0
  micro_hoymiles_710: 0
  solplanet_string_415: 47
  hoymiles_micro_415: 20
  huawei_string_415: 31
  sungrow_string_415: 14
  outros: 58

total exportado: 170
dataset_id: 5f9c8164b2b249e7...
```

Conferência: 47+20+31+14+58 = 170 = 171 − 1 (o único item sem
`modulo_potencia_w`, um kit "apagão" sem módulo — permanece fora do contrato
por composição incompleta, comportamento herdado e correto, não uma
regressão). Bate exatamente com a distribuição de
`evidencia-catalogo.txt` (ex.: SOLPLANET/string/415W = 40+6+1 = 47;
HOYMILES/micro/415W = 17+3 = 20; HUAWEI/string/415W = 17+14 = 31;
SUNGROW/string/415W = 9+4+1 = 14). As 4 tabelas legadas continuam presentes
no JSON (vazias, porque nenhum produto real bate com 620W/710W — só existe 1
item 620W no catálogo real e é de marca não detectada, então vai para
`outros`, não para `tri_solplanet_620`). `validar_contrato()` retornou `[]`
(zero erros) — o artefato gerado passa no schema estendido.

O arquivo foi escrito de verdade em disco via `exportar_precos.exportar_precios()`
(gravação atômica, fora dos dois worktrees, em `/tmp/solo-prices-verif/precios.json`,
só para essa checagem — não faz parte de nenhum dos repositórios).

### 2b. Correção pós-export-real: 58 conflitos causados por variante de inversor

A task trouxe evidência real do export contra `/var/lib/souenergy/catalogo.sqlite3`
no VPS (171 produtos): 58 dos 107 elegíveis caíam como `conflictos` e sumiam
do `precios.json`. Causa raiz, com exemplo real fornecido na task (ver
`decisions.md` §5): a chave de agrupamento do item anterior (§3) não incluía
o modelo do inversor, então variantes legítimas como HOYMILES HMS-2250DW-4T
vs HMS-2000DW-4T (mesmo módulo, estrutura e quantidade) caíam no mesmo grupo
e eram bloqueadas as duas.

Corrigido incluindo `inversor` e `modulo` na chave de agrupamento
(`exportar_precos.py:proyectar_catalogo`). Suíte local depois da correção:

```
$ python3 -m pytest tests/ -q --no-header
........................................................................ [ 64%]
........................................                                 [100%]
112 passed, 1 warning in 11.44s
```
112 = 111 anterior + 1 teste novo (`test_variantes_de_inversor_nao_bloqueiam`).
`test_ambiguedad_bloquea_linea` foi ajustado (não removido) para o cenário de
ambiguidade que continua válido: mesmo inversor+modulo, `estrutura` diferente.

**Não pude rodar o export contra o banco real do VPS nesta correção.** Este
ambiente de execução (sandbox do worktree) não tem acesso de rede/SSH ao VPS
nem a `/tmp/se005/val/` ou `/var/lib/souenergy/catalogo.sqlite3` — tentativa
de `ssh` falhou por resolução de hostname, e os caminhos citados na task não
existem neste filesystem. Isso repete a limitação já registrada na verificação
original (item 2 abaixo) e na seção "Bloqueios" — mesmo problema, ainda sem
solução de acesso.

**Pendente de execução manual (por quem tiver acesso ao VPS)**, exatamente o
comando da task:
```
cd /tmp/se005/val && SOUENERGY_DB=/var/lib/souenergy/catalogo.sqlite3 \
  /opt/souenergy-api/current/venv/bin/python - <<'PY'
import sys, os
sys.path.insert(0, "/tmp/se005/val")
import db
from exportar_precos import construir_precios_json, validar_contrato
conn = db.conectar(os.environ["SOUENERGY_DB"])
datos, rep = construir_precios_json(conn)
tot = 0
for k, v in datos["tabelas"].items():
    print("%-24s %d" % (k, len(v))); tot += len(v)
print("TOTAL:", tot)
print("elegidos:", rep["elegidos"], "| conflictos:", len(rep["conflictos"]), "| no_mapeados:", len(rep["no_mapeados"]))
print("erros de contrato:", validar_contrato(datos) or "nenhum")
PY
```
Antes de rodar: copiar o `exportar_precos.py` e `tests/test_exportar.py`
atualizados deste worktree para `/tmp/se005/val/` (o VPS não enxerga este
worktree). Expectativa com a correção: `conflictos` cai de 58 para 0 (os dois
`no_mapeados` documentados — `composicion_incompleta` e
`divergencia_potencia` — permanecem, são legítimos) e `TOTAL` sobe para ~169.
Se algum conflito real remanescer, provavelmente é o caso legítimo de
`estrutura`/`quantidade_modulos` divergente para o mesmo inversor+modulo —
vale inspecionar antes de tratar como bug.

### 3. Verificação da UI (propostas-soloenergia)

```
$ node solo-prices/.verify-prices.js
[...]
verificações: 93 · falhas: 0
RESULTADO: PASSOU
```

```
$ node solo-prices/.verify-data.js
monofasico_solplanet_620w rows: 25 expected: 25
trifasico_solplanet_620w rows: 48 expected: 48
micros_hoymiles_225kw_620w rows: 5 expected: 5
micros_hoymiles_225kw_710w rows: 5 expected: 5
total rows: 83 (expected 83)
metadata tabela: true | revisao: true | nota: true
colunas: true
mismatches: 0
mono 18 modulos parcela_12x: 2235.64 (expected 2235.64)
min potencia: 2.48 max potencia: 43.4
```
Sem regressão nas 93 checagens existentes nem nas contagens da tabela própria.

Verificação adicional de sintaxe/estrutura (não pedida explicitamente, feita
por precaução já que não há navegador disponível neste ambiente):
```
$ node --check <script extraído de index.html>
SINTAXE OK
$ python3 -m http.server 8934 (em solo-prices/) && curl -s -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:8934/index.html
HTTP 200
```

**Limitação honesta**: não há navegador/Playwright disponível neste ambiente
para renderizar a página e testar visualmente os novos filtros e rótulos.
A verificação ficou em: sintaxe válida, servidor HTTP 200, e as 93 + 8
checagens automatizadas (que exercitam as mesmas funções puras usadas pela
UI) passando sem regressão. Recomendo abrir a página manualmente com o
`precios.json` real (ou a fixture) antes do deploy, para confirmar
visualmente o novo filtro "Família" e os rótulos "Sem marca"/"—".

## Decisões comerciais em aberto (não inventadas)

Conforme restrição da task, nenhuma destas foi decidida nem tem fórmula
inventada — continuam pendentes de aprovação do dono, e a UI já as declara
explicitamente na aba "Combinador" (seção "Decisões comerciais pendentes de
aprovação"):

- **Margem / fórmula comercial (custo → preço de venda)**: não existe
  fórmula aprovada. O combinador nunca soma custo do distribuidor com preço
  comercial.
- **Preço unitário de componente**: nem a SouEnergy nem a tabela própria
  publicam preço de inversor/módulo/estrutura isolado, só o kit completo.
- **Faixa aceitável de razão DC/AC** (`FAIXA_DC_AC_MIN`/`MAX` = 0,90–1,35 no
  código da UI): parâmetro provisório de engenharia, a confirmar com o dono.
- **`DIAS_VALIDADE_PRECOS` = 30 dias** (limite de frescor da coleta, também
  no código da UI): decisão provisória já sinalizada como tal no próprio
  código; não foi alterada nesta task por ser parâmetro de negócio, não bug.
- **Fases com micro em sistema trifásico**: a UI já emite aviso
  (`R_MICRO_FASE`) explicando que microinversor é monofásico e que um
  sistema trifásico com micros exige balanceamento entre fases — não bloqueia
  automaticamente, só avisa; regra de negócio final não definida aqui.

## Publicação (`precios.json` no repo de propostas) — o que falta configurar

Não é possível nem seguro fazer isso a partir daqui (sem acesso ao VPS nem
aos segredos). Documentando o que precisa ser configurado em
`/etc/souenergy/souenergy.env` no VPS (fora deste repositório, nunca
commitado):

1. `REPO_PROPUESTAS=<caminho do checkout local do propostas-soloenergia no
   VPS>` — hoje vazio.
2. `REPO_PROPUESTAS_BRANCH=main` — **atenção**: o código e o `.env.example`
   antigos assumiam `master` por padrão; o repo real usa `main`. Corrigido
   nesta task (ver `decisions.md` §4), mas se o `.env` do VPS tiver
   `REPO_PROPUESTAS_BRANCH=master` explícito, vai sobrescrever o default
   correto e a publicação vai falhar contra o branch errado — checar
   manualmente.
3. `PUBLICAR_AUTOMATICAMENTE=true` — hoje `false`; enquanto estiver `false`,
   `publicar_si_cambia()` sempre retorna sem publicar nada (por desenho,
   `publicar_precos.py:206-210`).
4. `PRECIOS_URL_PUBLICA=https://propostas.soloenergia.com.br/solo-prices/precios.json`
   (opcional, mas recomendado) — sem isso, o publicador não confirma que o
   deploy realmente serviu o `dataset_id` esperado depois do push.
5. Credencial de máquina do checkout local em `REPO_PROPUESTAS` precisa ter
   permissão de push no repo `propostas-soloenergia` (branch `main`,
   caminho `solo-prices/precios.json` apenas — o publicador só toca nesse
   arquivo).

Com isso configurado, o fluxo `coleta → aprovação → export → publicar_si_cambia
→ push → verificação de deploy` já está implementado e testado
(`tests/test_exportar.py::test_publicacion_idempotente`); só falta a
configuração de ambiente no VPS, que está fora do escopo de arquivos deste
worktree.

## Bloqueios / itens que não pude fechar sozinho

- Sem acesso ao SQLite de produção real nem ao VPS: a "prova com números" do
  item 2 usa dados sintéticos que reproduzem fielmente a distribuição
  reportada em `evidencia-catalogo.txt`, não o banco real. Recomendo rodar
  `python3 -c "import db, exportar_precos; ..."` (mesmo padrão usado aqui)
  diretamente no VPS contra o banco real antes de considerar o item
  definitivamente fechado, e comparar as contagens com as impressas acima.
- **Ainda sem acesso ao VPS na correção do item 2b** (chave de agrupamento
  com inversor+modulo): a evidência real dos 58 conflitos foi fornecida pela
  task, mas este ambiente não tem SSH/rede até o VPS para reexecutar o export
  e confirmar `conflictos: 0` depois da correção. Comando pronto e critério
  de aceite documentados no item 2b — falta alguém com acesso ao VPS rodá-lo
  e colar a saída.
- Sem navegador disponível neste ambiente para captura visual da UI (ver
  limitação na seção de verificação acima).
- Publicação em produção depende de configuração de segredos no VPS (seção
  anterior) — não configurável nem verificável a partir daqui.
- PR(s) e deploy: não abro PR nem faço commit/push — por instrução da task,
  isso é responsabilidade do orquestrador (OpenClaw).
