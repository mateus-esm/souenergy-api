# SE-STUDY-002 — Estudio: souenergy-api + integração com solo-prices

- **Data:** 2026-09-13
- **Branch:** `study/souenergy-solo-prices`
- **Escopo:** estudo READ-ONLY — zero cambios de código, zero migraciones, zero escrituras em DB
- **Evidencia:** leitura direta de `api.py`, `scraper.py`, `deploy_update.py`, `.env.example`, `requirements.txt` (worktree) e do spec. Referencias `arquivo:linha` no anexo.
- **Nota de segurança:** nenhuna credencial real é citada neste documento. Referenciadas como "variáveis de ambiente" / "credenciales em deploy_update.py".

---

## Resumo executivo

**Sim, é viável** integrar souenergy-api com solo-prices mediante um scraper periódico completo que atualize uma base de dados. O scraper atual já percorre **todos** os produtos de cada categoria (a filtragem por potencia acontece depois, no agrupamento), portanto o full-scrape é uma evolução moderada, não uma reescritura.

O que bloquea hoje não é o scraping, é a **arquitectura de estado**:

1. **Credenciales reais expostas em git** — `.env.example` contém valores reales de variáveis de ambiente (USUARIO/SENHA/API_KEY) e `deploy_update.py` contém HOST/USER/PASSWORD em texto plano. Qualquer pessoa com acesso ao repo tem login do site e SSH root ao VPS. **P0.**
2. **Estado 100% em memória** — jobs e cache (`api.py:39,42`) se perdem com cada restart; o lock de threading (`api.py:45`) não protege contra múltiplos workers. **P0.**
3. **Fallo silencioso de login** — o scraper tenta logar 2 vezes e **continua mesmo se falha** (`scraper.py:248-254`), scrapeando precios de visitante (que podem diferir dos precios de cliente). Para um full-scrape periódico sem supervisão, isso é inaceptable. **P0.**
4. **Gap de dados para solo-prices** — o scraper não extrae a **fase (mono/tri)** nem normaliza `preco_pix` a número; solo-prices distingue mono/tri SOLPLANET 620W e micros HOYMILES 620W/710W. **P1.**

**Recomendações de arquitectura:** SQLite como fonte de verdade local no VPS (escritor único = scraper, leitores = API) + export de `precios.json` versionado no repo de propostas-soloenergia + cron diário com detecção de cambios e alertas. Postgres/Supabase só se aparecerem múltiplos consumidores ou necessidade de centralização.

---

## 1. Estado atual do souenergy-api

### 1.1 O que funciona

| Componente | Evidencia | Estado |
|---|---|---|
| API FastAPI async, 3 endpoints | `api.py:25-35, 102-191` | Funcional |
| `POST /jobs?potencia=X` → job async (202) | `api.py:116-147` | Funcional |
| `GET /jobs/{job_id}` → poll status | `api.py:150-163` | Funcional |
| `GET /precos?potencia=X` síncrono (solo tests) | `api.py:166-191` | Funcional |
| Auth por header `X-API-Key` | `api.py:21-23, 50-53` | Funcional |
| Cache em memória TTL 1h por potencia | `api.py:22, 58-63` | Funcional, frágil |
| Lock de threading (1 browser por vez) | `api.py:45, 81, 125, 182` | Funcional, frágil |
| Scraper Playwright headless + login | `scraper.py:73-86, 236-266` | Funcional |
| Deploy SFTP + `systemctl restart` | `deploy_update.py` | Funcional, inseguro |

### 1.2 O que é frágil

| # | Fragilidad | Evidencia | Consequência |
|---|---|---|---|
| F1 | **Credenciales reales en git** | `.env.example` (valores reales de variáveis de ambiente), `deploy_update.py:5` (HOST/USER/PASSWORD root em texto plano) | Exposição total: login do site + SSH root ao VPS. Qualquer lector do repo pode comprometer ambos. |
| F2 | **Estado en memória (jobs)** | `api.py:39` — `jobs: dict = {}` | Um restart do serviço (que o próprio `deploy_update.py:41` faz em cada deploy) **destruye todos os jobs em curso**; o `job_id` devolto ao cliente devolve 404. Sem durabilidade, sem retries. |
| F3 | **Cache en memória** | `api.py:42` — `cache: dict = {}` | Cada restart = cache fria = 3-5 min de scraping de novo. TTL fixo de 1h (`api.py:22`) sem refresh forzado. |
| F4 | **Lock só intra-processo** | `api.py:45` — `threading.Lock()` | Com uvicorn multi-worker, cada worker tem seu lock → 2 browsers simultáneos. O check `scraper_lock.locked()` (`api.py:125,182`) é check-then-act (race). |
| F5 | **BackgroundTasks não duráveis** | `api.py:139` — `background_tasks.add_task(...)` | Executa no mesmo processo, sem persistência, sem cola, sem retry. Se o processo morre, o job morre. |
| F6 | **Login falho = scrape silencioso** | `scraper.py:248-254` — após 2 tentativas **continua igual** | Scrapea precios de visitante (guest) que podem diferir dos de cliente logado. Para automação periódica é crítico. |
| F7 | **1 único commit, sem README/tests/CI** | `git log` → `d73f3f4 .env update` | Zero historia, zero rede de segurança. O commit mais recente é literalmente "atualização do .env". |
| F8 | **Deploy inseguro** | `deploy_update.py:31` — `AutoAddPolicy()` (acepta qualquer host key → MITM); sem manejo de erro; `systemctl restart` ciego; credenciales hardcodeadas | Deploy frágil e inseguro; sem rollback. |
| F9 | **Auth frágil** | `api.py:50-53` — comparação `key != API_KEY`; se `API_KEY` não está definida, `None != None` é falso → **request sem header passa** | Edge case: env ausente = auth aberta. Comparação de strings não constante (timing). |
| F10 | **`GET /precos` bloqueante** | `api.py:166-191` — 3-5 min de resposta | Proxies/load balancers com timeout corto rompen; ocupa o lock durante todo o scrape. |

**Veredicto Q1:** o núcleo (scrape + API) funciona, mas é um prototipo de 1 commit: estado volátil, credenciales expostas, zero testes. Não está pronto para rodar um full-scrape periódico sem antes resolver F1, F2/F3 e F6.

---

## 2. Modelo de dados atual

### 2.1 Shape exato de `consultar_precos(potencia_alvo)`

`scraper.py:236-266` devolve:

```json
{
  "potencia_alvo_kwp": 7.0,
  "solplanet": [
    {
      "nome":      "Kit Solar ... 7,0 kWp",
      "potencia":  7.0,
      "preco_pix": "R$ 12.345,67",
      "inversor":  "SOLPLANET ...",
      "modulo":    "SOLPLANET 620W ...",
      "estrutura": "..."
    }
  ],
  "hoymiles": [ ]
}
```

- `potencia_alvo_kwp`: float — a potencia pedida.
- `solplanet` / `hoymiles`: listas de produtos (cada uma = resultado de `processar_categoria`).
- Campos por produto (`scraper.py:191-201`): `nome` (str), `potencia` (float kWp), `preco_pix` (str **raw**, não parseado), `inversor`, `modulo`, `estrutura` (strs extraídos por regex do texto da página).

### 2.2 Como se agrupa por potencia

`processar_categoria` (`scraper.py:206-231`):

1. `coletar_todos_produtos` (`scraper.py:130-159`) coleta **todos** os produtos com potencia identificada (`pot > 0`), deduplicados por URL.
2. `viaveis = [p for p in produtos if p['potencia'] >= potencia_alvo]` (`scraper.py:213`) — filtra por potencia ≥ alvo.
3. `pot_grupo = min(p['potencia'] for p in viaveis)` (`scraper.py:214`) — a potencia do grupo é a **menor potencia ≥ alvo** ("próximo tamanho acima"); se não há nenhuna, usa `max(produtos)`.
4. `grupo = [p for p in produtos if abs(p['potencia'] - pot_grupo) <= TOLERANCIA_KWP]` (`scraper.py:216`) — todos os produtos dentro de **±0.15 kWp** (`scraper.py:19`).
5. Para cada kit do grupo, navega à página do produto e extrae campos (`scraper.py:220-229`).

### 2.3 Observações clave para a integração

| # | Observação | Evidencia | Implicação |
|---|---|---|---|
| D1 | `preco_pix` é **string raw** ("R$ 12.345,67") | `scraper.py:168-175` | Imposible diff de precios sem normalizar a número. |
| D2 | **Não há campo fase (mono/tri)** | `scraper.py:191-201` | solo-prices distingue mono/tri SOLPLANET 620W; o scraper não captura essa dimensão. Gap real. |
| D3 | Extração por regex do texto (`extrair_campo`) | `scraper.py:180-189` | Frágil ante cambios de layout/traducción do Magento. |
| D4 | Potencia extraída por regex do título/desc. | `scraper.py:44-68` | Produto sem "kWp"/"kW" no título é classificado como **subcategoria** (`scraper.py:120-124`), não como produto. |
| D5 | `preco_pix` pode ser `None` | `scraper.py:168-175` | Produtos sem preço visível entran com `null`. |
| D6 | Solo 2 categorias (SOLPLANET, HOYMILES) | `scraper.py:16-17, 256-257` | Cobertura limitada às marcas que solo-prices usa — suficiente, mas a verificar. |

**Veredicto Q2:** o modelo é uma lista plana de produtos por marca, agrupados por potencia com tolerancia 0.15 kWp. Para solo-prices falta: normalização de preço, extração de fase (mono/tri) e regra de mapeo às 4 tabelas (marca + fase + potencia do módulo).

---

## 3. Viabilidade do scraper periódico completo

### 3.1 O scraper atual já percorre TODOS os produtos?

**Quase.** `coletar_todos_produtos` (`scraper.py:130-159`) não filtra por potencia alvo: coleta todos os produtos com `pot > 0` da URL raíz, desce a subcategorias e percorre paginação. A filtragem por potencia acontece **depois**, em `processar_categoria` (`scraper.py:213`). Portanto, a base do full-scrape já existe.

### 3.2 O que havería que cambiar

| # | Cambio | Evidencia | Esforço |
|---|---|---|---|
| C1 | **`processar_categoria`: agrupar TODOS os produtos** — eliminar o filtro `potencia_alvo` (`scraper.py:213`) e, em vez de eleger um único `pot_grupo`, clusterizar todos os produtos por potencia (tolerancia 0.15) e visitar **todos** os clusters (ou todos os produtos). | `scraper.py:206-231` | Baixo (lógica de clustering simples: ordenar por potencia, agrupar por tolerancia). |
| C2 | **Descenso a subcategorias incondicional** — hoje só desce `if not produtos and subcategorias` (`scraper.py:133`). Se a raíz tem produtos **e** subcategorias, as subcategorias nunca se percorren → produtos perdidos. | `scraper.py:133-139` | Baixo (cambiar condição). |
| C3 | **Verificar/eliminar o cap de 5 páginas** — `while pagina <= 5` (`scraper.py:143`). Se o catálogo cresce além de 5 páginas, produtos ficam fora. | `scraper.py:141-149` | Baixo (loop até página vazia). |
| C4 | **Manejo de fallos parciais** — `analisar_produto` devolve `None` em timeout (`scraper.py:164-166`); `processar_categoria` já captura excepciones por kit (`scraper.py:228-229`). Para full-scrape conviene registrar fallos por URL e reintentar. | `scraper.py:164-166, 228-229` | Médio. |
| C5 | **Nova função `scrapear_todo()`** (ou flag em `consultar_precos`) que devolva todos os produtos agrupados por marca e potencia, sem `potencia_alvo`. | `scraper.py:236-266` | Baixo. |
| C6 | **Extraer fase (mono/tri)** — novo campo em `analisar_produto` (do título ou specs). | `scraper.py:164-201` | Médio (depende de como o site expone a fase). |
| C7 | **Tempo do full-scrape** — cada página de produto leva ~2-5 s (navegación + `esperar_e_limpar` 2 s, `scraper.py:223-224`). Para ~83 configs de solo-prices + resto do catálogo: **estimación 5-15 min**. Durante esse tempo o lock (`api.py:45`) bloquea consultas da API. | `scraper.py:220-229` | Planejar janela de scrape fora de horas. |
| C8 | **Login duro** — abortar se após N tentativas não há login confirmado (ver F6). | `scraper.py:248-254` | Baixo. |

### 3.3 Veredicto Q3

**Viável com cambios moderados (C1-C8), todos de baixo/médio esforço.** Não é reescritura: a coleta de todos os produtos já existe; falta a lógica de agrupar/visitar todos + robustez (login duro, fallos parciais, cobertura de subcategorias/paginas). O full-scrape é mais lento que a consulta puntual (5-15 min vs 3-5 min) e deve rodar em janela dedicada.

---

## 4. Base de dados proposta

### 4.1 Opções e tradeoffs

| Opção | Pros | Contras | Veredicto |
|---|---|---|---|
| **SQLite** (stdlib `sqlite3`) | Zero infra; arquivo único (backup = copiar); escritor único (o scraper) → sem conflitos; leitores múltiples (API) OK; suficiente para ~100-200 produtos + historial | Não centralizado; single-node; escrituras concorrentes limitadas (irrelevante aqui) | **Recomendada** como fonte de verdade local no VPS |
| **Postgres / Supabase** | Centralizado; multi-consumidor; histórico/consultas avançadas | Infra/ops extra; Supabase = dependencia externa (latencia, credenciales, coste); overkill para um consumidor estático | Só se aparecem múltiplos consumidores ou necessidade de centralização futura |
| **JSON versionado** (git) | Diffable (cambios de precios visibles em `git diff`); zero DB; natural para site estático | Sem consultas; sem histórico além de git; o API não consulta (teria que ler arquivo) | **Complemento ideal** para solo-prices, não substituto do SQLite |

### 4.2 Recomendações

**SQLite como fonte de verdade** (VPS, onde já corre o serviço `souenergy`) + **export `precios.json` versionado** para consumo de solo-prices. Postgres/Supabase fica como evolução futura documentada, não como requisito inicial.

### 4.3 Esquema propuesto (SQLite)

```sql
-- Catálogo atual (upsert por URL em cada scrape)
CREATE TABLE productos (
    url            TEXT PRIMARY KEY,
    nome           TEXT NOT NULL,
    marca          TEXT NOT NULL,          -- 'SOLPLANET' | 'HOYMILES'
    fase           TEXT,                   -- 'mono' | 'tri'  (pendente de extração, C6)
    potencia_kwp   REAL NOT NULL,
    inversor       TEXT,
    modulo         TEXT,
    estrutura      TEXT,
    preco_pix      TEXT,                   -- raw, tal como vem do site
    preco_numerico REAL,                   -- normalizado para diff (D1)
    primeira_visita TEXT NOT NULL,
    ultima_visita  TEXT NOT NULL
);

-- Historial de precios (uma fila por produto por scrape)
CREATE TABLE historial_precios (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    url            TEXT NOT NULL REFERENCES productos(url),
    capturado_em   TEXT NOT NULL,
    preco_pix      TEXT,
    preco_numerico REAL
);

-- Log de cada execução do scraper (para alertas e diagnóstico)
CREATE TABLE scrapes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    iniciado_em   TEXT NOT NULL,
    finalizado_em TEXT,
    status        TEXT NOT NULL,   -- 'ok' | 'erro' | 'login_falhou' | 'zero_produtos'
    n_productos   INTEGER,
    n_cambios     INTEGER,
    resumen       TEXT
);
```

**Veredicto Q4:** SQLite (simple, local, suficiente) + JSON versionado como ponte para solo-prices. Postgres/Supabase só quando haja um segundo consumidor real.

---

## 5. Integração com solo-prices

### 5.1 Opções

| Opção | Como funciona | Pros | Contras |
|---|---|---|---|
| **(a) Generar `PRICE_DATA` embebido** | Un build step regenera o JS const no repo propostas a partir do scrape | Site 100% estático, zero CORS, zero dependencia runtime | Acopla os repos em build time; o "dato" vive dentro de código JS (difícil de comparar via diff); requer redeploy do site em cada cambio |
| **(b) API que sirva JSON** | `index.html` faz `fetch()` a souenergy-api em runtime | Sempre fresco; sem redeploy | CORS no API; ponto único de fallo (site depende da API); o site tem login propio (solo/solo2026) — expone o API a tráfico do site |
| **(c) JSON versionado no repo propostas** | O scraper exporta `precios.json`; se commitea/pushea ao repo propostas; `index.html` faz fetch (ou inline em build) | Estático + Diffable (historial de precios em git); sem CORS; sem dependencia runtime; desacoplado | Requiere push cross-repo (automatizable com CI/token) |

### 5.2 Recomendações

**(c) JSON versionado como principal** + **(b) endpoint de consulta como complemento** (verificação on-demand e consumo futuro). (a) é o mais acoplado e converte dados em código — evitar.

### 5.3 Fluxo propuesto

```
[cron diário no VPS]
   └─ full scrape (scraper.py modificado, C1-C8)
        └─ upsert en SQLite (productos + historial_precios + scrapes)
             └─ export precios.json  (estructura alineada às 4 tabelas)
                  └─ commit/push ao repo propostas-soloenergia
                       └─ index.html faz fetch('precios.json') (o inline em build)
```

### 5.4 Nota de mapeo (a verificar contra PRICE_DATA real)

solo-prices tem 4 tabelas: **mono SOLPLANET 620W, tri SOLPLANET 620W, micros HOYMILES 620W, micros HOYMILES 710W** (83 configs, tabela Agosto 2026). O scraper extrae por marca (SOLPLANET/HOYMILES) mas **não extrae fase** (D2). A regra de correspondência produto→tabela precisa: `marca + fase + potencia do módulo`. Como o repo propostas está fora do diretório permitido deste estudo, a estrutura exata de `PRICE_DATA` deve ser confirmada lendo `solo-prices/index.html` antes de definir o schema do JSON de export.

**Veredicto Q5:** JSON versionado (c) como integração principal — encaja com site estático, dá historial de precios em git e elimina dependencia runtime. API (b) como complemento opcional.

---

## 6. Periodicidade e automação

### 6.1 Cron sugerido

- **Diário** (recomendado): ~04:00-05:00 hora local, janela de baixo tráfico (o full-scrape bloquea o lock da API, C7). Tabelas de precios de distribuidores cambian no máximo diariamente; a tabela atual é de Agosto 2026 (mensual).
- **Mínimo semanal** se o diário é inviable.
- Fora de horas laborales para não interferir com consultas da API.

### 6.2 Detecção de cambios

1. Normalizar `preco_pix` → `preco_numerico` (parsear "R$ 12.345,67" → 12345.67) na escritura (D1).
2. Comparar por URL contra a última captura; registrar todo en `historial_precios`.
3. `n_cambios` por scrape em `scrapes` → base para alertas e para o diff do JSON versionado.

### 6.3 Alertas

| Evento | Alerta | Por quê |
|---|---|---|
| Cambio de preço (≥1 produto) | Telegram/webhook/email com diff (produto, antes → depois) | É o propósito do sistema |
| `status = login_falhou` | **Alarma** | Sem login, os precios podem ser de visitante (F6) — dados inválidos |
| `status = zero_produtos` ou caída de contagem > X% | **Alarma** | Layout do site cambió / selectors rotos — fallo silencioso (dados obsoletos parecen válidos) |
| Produtos não vistos (descontinuados) | Info | Mantener catálogo limpo |

**Veredicto Q6:** cron diário ~04:00-05:00; diff por URL com preço normalizado; alertas obrigatorias para login falho e zero produtos (o fallo silencioso é o maior risco operacional).

---

## 7. Riscos

| # | Risco | Severidad | Evidencia | Mitigación |
|---|---|---|---|---|
| R1 | **Credenciales expostas** (site + SSH root) | **Crítica** | `.env.example` (valores reales), `deploy_update.py:5` | **Rotar todas** (USUARIO/SENHA/API_KEY/PASSWORD); `.env.example` só com placeholders; deploy por SSH keys/env vars; nunca commitear credenciales |
| R2 | **Anti-bot / rate-limit / bloqueo** | Alta | Scrape diário full-site contra Magento | Throttle (delays aleatorios), user-agent, janela off-peak, respeitar robots.txt, monitorizar 403/429 |
| R3 | **Login que expire / cambie** | Alta | `scraper.py:248-254` (continua após 2 fallos) | Login duro: abortar scrape com `status=login_falhou` + alerta (6.3) |
| R4 | **Layout cambia → selectors rotos → 0 produtos** | Alta | Selectors Magento hardcodeados (`scraper.py:24-28, 105, 165-175`) | Alerta por zero_produtos/caída de contagem; tests de extração com fixtures |
| R5 | **Playwright headless no VPS** | Médio | Browser + deps de sistema; OOM em VPS pequeno | `playwright install chromium` + deps; monitorizar memória; janela dedicada |
| R6 | **Concorrencia multi-worker** | Médio | `threading.Lock` intra-processo (`api.py:45`) | Lock a nível de processo (lockfile) ou worker único; flag global "scraping em curso" em SQLite |
| R7 | **Estado volátil (jobs/cache)** | Médio | `api.py:39,42` | Persistência em SQLite; jobs duráveis (cola) se necessário |
| R8 | **Precios dependentes da conta** | Médio | Login com conta específica | Documentar que os precios são os da lista de precios dessa conta; alertar se cambia o grupo de cliente |
| R9 | **Extracción frágil (regex)** | Médio | `scraper.py:44-68, 180-189` | Normalizar precios; fixtures de teste; revisión manual periódica |
| R10 | **Deploy inseguro (MITM, sin rollback)** | Médio | `deploy_update.py:31` (`AutoAddPolicy`) | SSH keys + known_hosts; script com rollback; CI |

---

## Recomendações priorizadas

| Prioridade | Ação | Impacto | Esforço |
|---|---|---|---|
| **P0** | Rotar todas as credenciales expostas (site, API, SSH); `.env.example` com placeholders; deploy por env vars/SSH keys | Elimina exposição total (R1) | Baixo |
| **P0** | Login duro no scraper: abortar + `status=login_falhou` + alerta | Elimina scrape silencioso de precios guest (R3, F6) | Baixo |
| **P0** | Persistência: SQLite (productos + historial + scrapes) substituindo jobs/cache em memória | Estado sobrevive restarts; base para todo o resto (F2/F3) | Médio |
| **P0** | Lock a nível de processo (lockfile) + worker único para o scraper | Evita 2 browsers simultáneos (R6, F4) | Baixo |
| **P1** | Full-scrape: `scrapear_todo()` + clustering por potencia + subcategorias incondicional + paginação até página vazia (C1-C3, C5) | Habilita o objetivo do estudo (Q3) | Médio |
| **P1** | Extraer fase (mono/tri) e normalizar `preco_numerico` (C6, D1) | Alinha o modelo às 4 tabelas de solo-prices (D2) | Médio |
| **P1** | Export `precios.json` versionado + push ao repo propostas + `fetch` em index.html (5.3) | Integração principal com solo-prices (Q5) | Médio |
| **P1** | Cron diário ~04:00-05:00 + diff por URL + alertas (login_falhou, zero_produtos, cambios) (Q6) | Automação com supervisão | Médio |
| **P2** | Endpoint API `GET /precios` (dataset completo) + `GET /precios/cambios` | Consumo on-demand complementario (5.2) | Baixo |
| **P2** | Tests de extração com fixtures + CI + README + commits atómicos | Rede de segurança (F7) | Médio |
| **P2** | Migração a Postgres/Supabase se aparecem mais consumidores | Centralização futura (Q4) | Alto |

**Orden de execução sugerido:** P0 (seguridad + persistência + login duro) → P1 (full-scrape + fase/precio + JSON + cron/alertas) → P2 (endpoints, tests, Postgres opcional).

---

## Anexo: evidencia (referencias arquivo:linha)

### api.py
- `api.py:21-23` — `API_KEY = os.getenv("API_KEY")`, header `X-API-Key`
- `api.py:22` — `CACHE_TTL_SEC = 3600`
- `api.py:39` — `jobs: dict = {}` (estado en memória)
- `api.py:42` — `cache: dict = {}` (cache en memória)
- `api.py:45` — `scraper_lock = threading.Lock()`
- `api.py:50-53` — `verificar_chave` (comparação `!=`)
- `api.py:58-63` — `cache_valido` (TTL 1h)
- `api.py:68-92` — `executar_job` (cache → lock → scrape → cache)
- `api.py:116-147` — `criar_job` (202, 429 en `api.py:125-126`)
- `api.py:139` — `background_tasks.add_task(...)`
- `api.py:150-163` — `obter_job` (404 se não existe)
- `api.py:166-191` — `get_precos_sync` (bloqueante, 500 en error)

### scraper.py
- `scraper.py:12-13` — USUARIO/SENHA desde env
- `scraper.py:15-17` — URL_HOME, URL_SOLPLANET, URL_HOYMILES
- `scraper.py:19` — `TOLERANCIA_KWP = 0.15`
- `scraper.py:44-56` — `extrair_kwp` (regex kWp/kW)
- `scraper.py:58-68` — `extrair_potencia_card`
- `scraper.py:73-86` — `tentar_logar`
- `scraper.py:91-127` — `carregar_url_e_coletar_cards` (`.product-item`, 3 tentativas)
- `scraper.py:120-124` — produto sem potencia → subcategoria
- `scraper.py:130-159` — `coletar_todos_produtos` (subcategorias `scraper.py:133-139`, paginação ≤5 `scraper.py:141-149`, dedup por URL `scraper.py:151-158`)
- `scraper.py:164-201` — `analisar_produto` (selectors de preço `scraper.py:169-175`, `extrair_campo` `scraper.py:180-189`, campos `scraper.py:191-201`)
- `scraper.py:206-231` — `processar_categoria` (viaveis `scraper.py:213`, pot_grupo `scraper.py:214`, grupo `scraper.py:216`)
- `scraper.py:236-266` — `consultar_precos` (login loop `scraper.py:248-254`, return `scraper.py:262-266`)

### deploy_update.py
- `deploy_update.py:5` — HOST/PORT/USER/PASSWORD (credenciales reales — não citadas)
- `deploy_update.py:7` — FILES a subir
- `deploy_update.py:31` — `AutoAddPolicy()`
- `deploy_update.py:41-44` — `systemctl restart souenergy` + status + curl

### .env.example
- Contiene valores reales de variáveis de ambiente (USUARIO/SENHA/API_KEY) — não citados

### requirements.txt
- `fastapi`, `uvicorn[standard]`, `playwright`, `python-dotenv`

### Git
- 1 commit: `d73f3f4 .env update`; branch `study/souenergy-solo-prices`; `docs/` untracked

### solo-prices (segundo o spec; repo externo no accesible desde este worktree)
- App estática `solo-prices/index.html` en propostas.soloenergia.com.br/solo-prices/
- Login solo/solo2026; seletor de potencia kWp
- 4 tabelas embebidas como JS const `PRICE_DATA` (mono SOLPLANET 620W, tri SOLPLANET 620W, micros HOYMILES 620W/710W) — 83 configuraciones
- Datos estáticos (tabela Agosto 2026, manual)