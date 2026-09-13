# SE-PRICES-002 — Implementação (Verboo + orquestração)

Status: ✅ Implementado e validado (74/74 testes verdes)

## O que foi entregue

Scraper periódico completo do site SouEnergy que grava em SQLite e exporta `precios.json` para o solo-prices consumir:

- **db.py** — SQLite (migrações versionadas, scrapes, observações, histórico de preços, promoção atômica, jobs, publicações, backup).
- **scraper.py** — full-scrape (BFS por categorias, paginação por toolbar/link next, detalhes de todos os produtos, auth estrita, detecção de bloqueio/listagem vazia, limites de páginas/tempo).
- **normalizacao.py** — dinheiro (centavos inteiros), potência (kWp vs kW), fase (mono/tri/bi).
- **catalogo.py** — configuração de entradas, classificação, URLs canônicas, cobertura.
- **coleta.py** — CLI do ciclo completo (lock, estados, limites, códigos de saída).
- **exportar_precos.py** — projeção das 4 tabelas (mono/tri SOLPLANET 620W, micros HOYMILES 620W/710W) → `precios.json` + schema.
- **publicar_precos.py** — publicação idempotente no repo propostas-soloenergia + confirmação de deploy.
- **alertas.py** — notificações com retry.
- **api.py** — lê da última coleta aprovada no SQLite (compat com endpoints legados).
- **deploy_update.py** — deploy via SSH seguro + rollback (credenciais movidas para env).
- **ops/** — systemd timer diário (`souenergy-scrape.timer` + `.service`), backup, README.
- **config/** — entradas do catálogo + mapeamento para solo-prices.
- **schemas/precios.schema.json** — contrato do precios.json.
- **tests/** — 74 testes (scraper, db, exportar, publicar, api, catálogo, normalização).

## Segurança (P0s tratados)

- `.env.example` agora só tem placeholders (credenciais reais removidas do git).
- `.gitignore` exclui `.env`, `.env.*`, `*.sqlite3`, backups.
- `deploy_update.py` não tem credenciais reais (usa env vars).
- Verificação: grep por valores sensíveis = OK.

## Validação

- `python3 -m pytest tests/ -q --no-header` → **74 passed, 0 failed**.
- Sintaxe Python OK, JSONs válidos, migration SQL aplicável.

## Próximos passos (deploy)

1. Merge do PR no souenergy-api.
2. Configurar `.env` no VPS (SOUENERGY_DB, credenciais, API_KEY).
3. Instalar deps + systemd timer (`ops/souenergy-scrape.timer`).
4. Rodar coleta inicial (`python3 -m coleta`) e validar cobertura.
5. Gerar `precios.json` e publicar no repo propostas-soloenergia.
6. Ligar solo-prices a consumir `precios.json` (fetch same-origin).
