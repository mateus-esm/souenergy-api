# SE-PRICES-003 — Task Contract

Project: souenergy-api
Agent: codex (PM + execução)
Branch: fix/souenergy-scraper-selectors
Risk: production (scraper em produção no VPS)

## Outcome

Corrigir o scraper para que a coleta REAL no site SouEnergy funcione no VPS: hoje faz login OK mas falha na listagem. Entregar fix validado com coleta real no VPS, commit + push + PR.

## Context (evidência real do VPS, 2026-09-13 22:02 UTC)

Deploy SE-PRICES-002 JÁ ESTÁ EM PRODUÇÃO:
- API v3.0.0 rodando (`/opt/souenergy-api/current`, user `souenergy`), healthcheck `{"status":"ok","version":"3.0.0","source":"database"}`.
- systemd units instaladas: `souenergy.service` (API), `souenergy-scrape.service` + `.timer` (semanal, dom 04:30), `souenergy-backup.*`.
- Env de produção em `/etc/souenergy/souenergy.env` (0600).
- Login do scraper: **FUNCIONA** (log: "Login confirmado" às 22:00:36).

FALHAS REAIS na coleta (journalctl -u souenergy-scrape):
1. **`.product-item` existe mas `wait_for_selector` dá timeout:**
```
WARNING scraper: Sin .product-item tras 3 intentos: .../solplanet.html (Page.wait_for_selector: Timeout 12000ms exceeded.
  - waiting for locator(".product-item") to be visible
  - 10 × locator resolved to 20 elements. Proceeding with the first one: <li data-collapsible="true" data-role="product-item" class="item product product-item odd last">…</li>
  - 18 × locator resolved to 20 elements. Proceeding with the first one: <li ... class="item product product-item">…</li>
)
```
Ou seja: 20 elementos `.product-item` existem, mas o PRIMEIRO da lista não está visível (provavelmente está num menu/container oculto), e `page.wait_for_selector('.product-item')` espera visibilidade do primeiro match. Precisa usar `state="attached"` (ou `locator(...).first` com attached), ou esperar por um seletor mais específico do grid de produtos visível.

2. **`hoymiles.html` reporta "Listado vacío explícito" falso:**
```
INFO scraper: Listado vacío explícito: https://souenergy.com.br/inversores-e-microinversores/hoymiles.html
```
A detecção de listagem vazia (`es_listado_vacio`) está dando falso positivo nessa URL — provavelmente um seletor de "vazio" (`.message.info.empty` etc.) casa com algo presente, ou o regex do body casa texto que não significa vazio.

Resultado: coleta rejeitada (`status=3`, "1 falhas sem resolução"), DB sem dados aprovados (`coleta_aprovada_em: null`).

## Tarefas

1. Corrigir `cargar_url_y_colectar_cards` / `colectar_cards_de_pagina` para usar `state="attached"` no wait do `.product-item` (ou seletor equivalente que funcione no site real), sem exigir visibilidade do primeiro elemento.
2. Corrigir `es_listado_vacio` para não dar falso positivo no hoymiles.html (validar contra a página real).
3. Validar com COLETA REAL no VPS (via `sudo -u souenergy env $(grep -v '^#' /etc/souenergy/souenergy.env | xargs) /opt/souenergy-api/current/venv/bin/python -m coleta --no-promote` ou systemctl), confirmando: login OK, produtos descobertos > 0, sem "listado vacío" falso.
4. Atualizar testes se necessário (não quebrar os 94 existentes).
5. Commit + push + PR.

## Acesso ao VPS (para validação)

- SSH: `ssh -i /root/.ssh/souenergy_deploy root@72.61.219.156` (chave já instalada; known_hosts em /root/.ssh/known_hosts).
- Código em produção: `/opt/souenergy-api/current` (release). NÃO editar produção à mão — o deploy é por release.
- Para testar um fix no VPS sem deploy: copiar os arquivos alterados para `/opt/souenergy-api/current/` (é o que o deploy faria) OU rodar o scraper local com `PLAYWRIGHT_BROWSERS_PATH=/var/lib/souenergy/.cache/ms-playwright`.
- Credenciais do site: `/etc/souenergy/souenergy.env` (NUNCA citar valores).

## Constraints

- NUNCA citar credenciais reais (USUARIO/SENHA/API_KEY).
- Manter os 94 testes passando (adicionar, não quebrar).
- Escrever em PT-BR.
- Entregável: fix no repo + evidência de coleta real funcionando.

## Acceptance

- [ ] `.product-item` é detectado sem exigir visibilidade (attached)
- [ ] `hoymiles.html` não dá falso "listado vazio"
- [ ] Coleta real no VPS: produtos descobertos > 0, sem falhas não resolvidas
- [ ] 94+ testes passando
- [ ] Commit + push + PR
