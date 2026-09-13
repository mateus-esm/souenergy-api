# SE-STUDY-002 — Task Contract

Project: souenergy-api
Agent: verboo
Branch: study/souenergy-solo-prices
Risk: read-only study (zero code changes)

## Outcome

Estudio READ-ONLY (sin cambios de código) sobre el `souenergy-api` y su integración con `solo-prices`. Entregable: documento de estudio en `docs/dev/projects/souenergy-api/SE-STUDY-002/verboo/` con hallazgos, arquitectura propuesta y recomendaciones priorizadas.

## Context (evidencia real levantada 2026-09-13)

### souenergy-api (repo: /srv/solo-dev/repos/souenergy-api)
- Stack: FastAPI + Uvicorn + Playwright + python-dotenv. Sin README.
- `api.py` (v2.0.0): API async con 3 endpoints:
  - `POST /jobs?potencia=X` — inicia job async, retorna job_id (202)
  - `GET /jobs/{job_id}` — status (queued|running|done|error) + resultado
  - `GET /precos?potencia=X` — síncrono bloqueante (3-5 min), solo para tests
  - Auth: API key via header `X-API-Key` (env `API_KEY`)
  - Cache en memoria (dict) con TTL 1 hora por potencia
  - Lock de threading para impedir 2 browsers simultáneos
  - Estado en memoria (jobs dict) — se pierde al reiniciar
- `scraper.py`: Playwright headless Chrome, login en souenergy.com.br (USUARIO/SENHA de env), scrapea:
  - URL_SOLPLANET y URL_HOYMILES (inversores-e-microinversores)
  - Cards `.product-item`, extrae potencia kWp del título/descripción
  - Navega subcategorías + paginación (hasta 5 páginas)
  - Abre cada producto, extrae: preco_pix, inversor, modulo, estructura
  - Tolerancia 0.15 kWp para agrupar por potencia
  - Función principal: `consultar_precos(potencia_alvo)` → dict
- `deploy_update.py`: push por SFTP al VPS (72.61.219.156) + systemctl restart souenergy. ⚠️ Contiene credenciales reales en texto plano (HOST/USER/PASSWORD) — NO citarlas en el estudio.
- `.env.example`: contiene USUARIO/SENHA/API_KEY reales — NO citarlas.
- Git: 1 solo commit (`d73f3f4 .env update`), branch master.

### solo-prices (repo: /srv/solo-dev/repos/propostas-soloenergia)
- App estática en `solo-prices/index.html` (deployada en propostas.soloenergia.com.br/solo-prices/)
- Login solo/solo2026, seletor de potencia kWp → muestra módulos/inversor/geração/precios
- 4 tablas embebidas como JS const PRICE_DATA (mono SOLPLANET 620W, tri SOLPLANET 620W, micros HOYMILES 620W/710W) — 83 configuraciones
- Datos actualmente estáticos (tabela Agosto 2026, manual)

### Pregunta del estudio
¿Es posible integrar souenergy-api con solo-prices para un **scraper periódico completo** de precios que actualice una base de datos (en vez de consulta puntual por potencia)?

## Preguntas a responder (con evidencia)

1. **Estado actual del souenergy-api**: qué funciona, qué es frágil (cache en memoria, estado en memoria, lock, credenciales expuestas, 1 commit).
2. **Modelo de datos actual**: qué devuelve `consultar_precos` (shape exacto), cómo se agrupa por potencia, qué campos por producto.
3. **Viabilidad del scraper periódico completo**: ¿el scraper actual puede recorrer TODOS los productos (no solo por potencia alvo)? ¿Qué habría que cambiar en `coletar_todos_produtos`/`processar_categoria` para hacer un full-scrape?
4. **Base de datos propuesta**: ¿dónde guardar los precios scrapeados? Opciones: SQLite (simple), Postgres/Supabase (si se quiere centralizar), JSON versionado. Recomendar con tradeoffs.
5. **Integración con solo-prices**: ¿cómo consumiría solo-prices los datos? Opciones: (a) generar el `PRICE_DATA` embebido desde el scraper, (b) API que sirva JSON, (c) archivo JSON versionado en el repo de propostas-soloenergia. Recomendar.
6. **Periodicidad y automatización**: cron sugerido (diario/semanal), cómo detectar cambios de precios, alertas.
7. **Riesgos**: credenciales expuestas, bloqueo del sitio (anti-bot), login que expire, Playwright en headless, concurrencia.

## Constraints

- READ-ONLY: no modificar código, no ejecutar migraciones, no escribir en bases de datos.
- Puedes leer el repo (worktree), leer los archivos del souenergy-api y solo-prices.
- NO citar credenciales reales (USUARIO/SENHA/API_KEY/PASSWORD del deploy) — referenciarlas como "env vars" / "credenciales en deploy_update.py".
- Entregable: `docs/dev/projects/souenergy-api/SE-STUDY-002/verboo/` con estudio completo (Markdown), en PORTUGUÉS BRASILEIRO (PT-BR), con evidencia y recomendaciones priorizadas (P0/P1/P2).

## Acceptance

- [ ] Estudio completo en docs/dev/projects/souenergy-api/SE-STUDY-002/verboo/
- [ ] Las 7 preguntas respondidas con evidencia
- [ ] Recomendaciones priorizadas P0/P1/P2 con impacto/esfuerzo
- [ ] Sin cambios de código (solo docs)
- [ ] En PT-BR (nunca español)
