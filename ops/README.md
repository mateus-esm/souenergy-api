# Operación — SouEnergy API (coleta periódica)

Documento de operación del colector completo, SQLite y publicación de
`precios.json`. Plano: `docs/dev/projects/souenergy-api/SE-PRICES-002/codex/plano.md`.

## Arquitectura

```
systemd timer (04:30 America/Sao_Paulo)
  └─ souenergy-scrape.service (oneshot, User=souenergy)
       └─ coleta.py  →  scraper.scrapear_tudo()  →  observaciones SQLite
            └─ validación de cobertura → promoção atómica (scrapes ok)
                 └─ exportar_precios.py → precios.json candidato (validado)
                      └─ publicar_precos.py → propostas-soloenergia (opcional)
systemd timer (04:45) → souenergy-backup.service → ops/backup.py (rotación 30)
API (uvicorn) → lee SQLite en modo lectura (última coleta aprovada)
```

## Instalación (producción)

1. **Directorios y permisos** (usuario dedicado, sin superusuario):
   ```sh
   useradd -r -m -d /var/lib/souenergy souenergy
   mkdir -p /opt/souenergy-api/releases /var/lib/souenergy/backups \
            /var/lib/souenergy/.cache/ms-playwright /etc/souenergy
   chown -R souenergy:souenergy /var/lib/souenergy /opt/souenergy-api
   chmod 700 /etc/souenergy
   ```
2. **Segredos** en `/etc/souenergy/souenergy.env` (modo 0600, fuera del
   checkout). Solo nombres/placeholders en `.env.example`:
   `USUARIO`, `SENHA`, `API_KEY`, `SOUENERGY_DB`, `DB_BUSY_TIMEOUT_MS`,
   `CATALOGO_ENTRADAS`, `EXPORT_DIR`, `REPO_PROPUESTAS`,
   `REPO_PROPUESTAS_BRANCH`, `PUBLICAR_AUTOMATICAMENTE`, `ALERTA_*`,
   `VPS_*`, `LIMITE_*`.
3. **Release inmutable** (deploy_update.py): venv aislado, `pip install -r
   requirements.txt`, Chromium con bibliotecas de sistema compatibles:
   ```sh
   python -m venv /opt/souenergy-api/venv
   /opt/souenergy-api/venv/bin/pip install -r requirements.txt
   /opt/souenergy-api/venv/bin/playwright install --with-deps chromium
   ```
4. **Timer** (solo timer, nunca crontab y timer a la vez):
   ```sh
   cp ops/souenergy-scrape.{service,timer} /etc/systemd/system/
   cp ops/souenergy-backup.{service,timer} /etc/systemd/system/
   systemctl daemon-reload
   systemctl enable --now souenergy-scrape.timer souenergy-backup.timer
   systemctl list-timers souenergy-scrape.timer
   systemd-analyze calendar '*-*-* 04:30:00 America/Sao_Paulo'
   ```
   Habilitar el timer SOLO después de coleta manual de homologación aprobada.

## Operación diaria

- **Ver coleta**: `journalctl -u souenergy-scrape.service -n 100 --no-pager`
- **Ver próxima ejecución**: `systemctl list-timers souenergy-scrape.timer`
- **Coleta manual** (sin push): `python coleta.py --no-promote`
- **Coleta manual completa**: `python coleta.py`
- **Códigos de salida**: 0 ok · 1 erro · 2 login_falhou · 3 rejeitado
  (cobertura incompleta / queda > 20% / zero produtos) · 4 interrompido
  (recuperación) · 5 lock ocupado.
- **Estado de la DB**:
  ```sql
  SELECT id, status, iniciado_em, finalizado_em, descobertos, alteracoes
    FROM scrapes ORDER BY iniciado_em DESC LIMIT 10;
  SELECT motivo, COUNT(*) FROM historico_precios GROUP BY motivo;
  ```
- **Backup**: `ls -lt /var/lib/souenergy/backups/` · restaurar:
  ```sh
  systemctl stop souenergy   # procesos desconectados
  cp /var/lib/souenergy/backups/catalogo-XXXX.sqlite3 \
     /var/lib/souenergy/catalogo.sqlite3
  systemctl start souenergy
  ```
  Probar `PRAGMA integrity_check` tras restaurar.

## Recuperación y rollback

- **Coleta falló**: se conserva el snapshot anterior; la API sigue sirviendo
  datos con edad explícita (`desactualizado`). No re-promover días distintos.
- **Publicación falló**: la DB aprobada se conserva; reenviar el mismo
  artefacto (idempotente por hash en `publicacoes`).
- **Datos incorrectos**: pausar timer/publicador, bloquear el hash rejeitado,
  restaurar release anterior en Netlify por commit normal (no solo rollback
  visual) y en la API volver al puntero de release segura. Nunca volver a una
  versión con credenciales expuestas o autenticación abierta.
- **Deploy**: `python deploy_update.py` (release inmutable + checksum +
  healthcheck) · `python deploy_update.py --rollback`.

## Monitor

Alertas (canal en `ALERTA_*`): login falho, 403/CAPTCHA/429 persistente, zero
produtos, cobertura incompleta, queda de contagem, mapeo inválido, variación
anormal, disco cheio, backup falho, publicación/deploy falho. La ausencia de
coleta aprovada por 36 h requiere un monitor independiente (OnFailure no
detecta un timer que dejó de ejecutar).

## Seguridad

- Credenciales solo por env/`EnvironmentFile` (modo 0600). Nada real en Git.
- SSH por clave con `known_hosts` validado, sin `AutoAddPolicy`, sin
  contraseña embebida.
- La API no arranca sin `API_KEY`; comparación en tiempo constante.
- `precios.json` es contenido estático público: confirmar con el responsable
  comercial que los precios de la cuenta pueden integrar el mismo contenido.