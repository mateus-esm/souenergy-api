# SE-PRICES-002 — Task Contract

Project: souenergy-api (+ propostas-soloenergia)
Agent: codex (PM) → verboo (execução)
Branch: feat/souenergy-full-scraper
Risk: production (cron + DB + deploy)

## Outcome

Implementar um **scraper periódico completo** do site SouEnergy que atualiza uma base de dados (SQLite) e alimenta **solo-prices** (propostas.soloenergia.com.br/solo-prices/). Codex = PM (plano + validação), Verboo = execução. Deploy completo até que funcione em produção.

## Context (evidencia real)

### Estudio previo (SE-STUDY-002, Verboo, PR #1)
- `docs/dev/projects/souenergy-api/SE-STUDY-002/verboo/estudio-souenergy-solo-prices.md` (341 líneas)
- Veredicto: **sí es viable** el full-scrape — el scraper actual ya recorre TODOS los productos (el filtro por potencia es posterior)
- P0s: credenciales expuestas en git · estado 100% en memoria · fallo silencioso de login
- Arquitectura recomendada: SQLite local + export precios.json versionado en propostas-soloenergia + cron diario

### souenergy-api (repo: /srv/solo-dev/repos/souenergy-api, branch master)
- `api.py` (v2.0.0): FastAPI async, POST /jobs?potencia=X, GET /jobs/{id}, GET /precos?potencia=X. Auth X-API-Key. Cache en memoria TTL 1h. Lock threading.
- `scraper.py`: Playwright headless, login souenergy.com.br, scrapea SOLPLANET + HOYMILES, cards .product-item, extrae potencia kWp, navega subcategorías + paginación (hasta 5), abre cada producto (preco_pix, inversor, modulo, estrutura). Función principal: `consultar_precos(potencia_alvo)`.
- `deploy_update.py`: SFTP al VPS (72.61.219.156) + systemctl restart souenergy. ⚠️ Contiene credenciales reales — NO citarlas.
- `.env.example`: USUARIO/SENHA/API_KEY reales — NO citarlas.
- Git: 1 commit (d73f3f4), branch master.

### solo-prices (repo: /srv/solo-dev/repos/propostas-soloenergia)
- `solo-prices/index.html` estático, deployado en propostas.soloenergia.com.br/solo-prices/
- Login solo/solo2026, seletor de potencia kWp → módulos/inversor/geração/precios
- 4 tablas embebidas como JS const PRICE_DATA (mono SOLPLANET 620W, tri SOLPLANET 620W, micros HOYMILES 620W/710W) — 83 configuraciones, datos estáticos (tabela Agosto 2026)

## Preguntas que Codex (PM) debe resolver en el plan

1. **Cómo funciona el sitio SouEnergy**: ¿los precios son por kit individual, por proveedor, por módulo/marca/potencia? ¿Qué estructura real tiene (cards, subcategorías, productos)?
2. **Estrategia de scraping**: ¿full-scrape de TODOS los productos (no solo por potencia)? ¿Cómo adaptar `coletar_todos_produtos`/`processar_categoria` para recorrer todo? ¿Cómo manejar login (fallo silencioso), anti-bot, paginación?
3. **Modelo de datos**: ¿qué guardar en SQLite? Tablas sugeridas: productos (url, nome, marca, potencia_kwp, fase mono/tri, inversor, modulo, estrutura, preco_pix, preco_normalizado, fecha_scrapeo), histórico de precios (producto_id, preco, fecha). ¿Normalizar preco_pix a número?
4. **Integración con solo-prices**: ¿cómo consumir? Opciones: (a) generar PRICE_DATA embebido desde el scraper, (b) API que sirva JSON, (c) precios.json versionado en propostas-soloenergia. Recomendar con tradeoffs.
5. **Cron**: periodicidad (diario/semanal), cómo detectar cambios de precios, alertas, idempotencia.
6. **Deploy**: cómo desplegar el scraper + cron en el VPS (systemd timer vs crontab), cómo actualizar solo-prices en Netlify.
7. **Seguridad**: rotar credenciales expuestas, mover a secrets/env, nunca citar valores en código.

## Constraints

- Codex = PM: produce el PLAN en `docs/dev/projects/souenergy-api/SE-PRICES-002/codex/plano.md` (arquitectura, estrategia, modelo de datos, integración, cron, deploy, validación). NO implementa.
- Verboo = ejecución: implementa siguiendo el plan.
- NO citar credenciales reales (USUARIO/SENHA/API_KEY/PASSWORD) — referenciarlas como "env vars" / "secrets".
- El estudio SE-STUDY-002 debe ser la base del plan (leerlo primero).
- Entregable final: scraper periódico funcionando + DB actualizada + solo-prices consumiendo los datos + deploy verificado en producción.
- Idioma: PT-BR (nunca español).

## Acceptance

- [ ] Plan de Codex (PM) en docs/dev/projects/souenergy-api/SE-PRICES-002/codex/plano.md
- [ ] Implementación de Verboo siguiendo el plan
- [ ] Scraper periódico completo funcionando (recorre TODO el sitio)
- [ ] SQLite con precios actualizados
- [ ] solo-prices consume los datos (precios.json o API)
- [ ] Cron configurado (diario/semanal)
- [ ] Deploy verificado en producción (propostas.soloenergia.com.br/solo-prices/)
- [ ] PR(s) abiertos + report en canales
