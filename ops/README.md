# Operação — SouEnergy API

## Agenda semanal e gatilho manual

Por decisão de Mateus em 13/09/2026, a coleta roda **semanalmente, aos domingos
às 04:30 em America/Sao_Paulo**. A janela evita o horário comercial. O timer usa
`OnCalendar=Sun *-*-* 04:30:00 America/Sao_Paulo`, `Persistent=true` (recupera um
acionamento perdido) e atraso aleatório de até cinco minutos. Não instalar um
crontab adicional. O backup permanece **diário**, às 04:45; a API de backup do
SQLite permite execução durante a coleta com WAL ativo.

O **gatilho manual** é `python3 -m coleta`, executado na raiz da release, com o
venv ativo e as variáveis de ambiente configuradas. Ele usa o mesmo lock da
execução agendada. Não exige a API em execução. Para usar o ambiente de produção
sem carregar segredos no terminal, o equivalente operacional é:

```sh
sudo systemctl start souenergy-scrape.service
journalctl -u souenergy-scrape.service -n 100 --no-pager
```

Isso funciona mesmo com o timer desabilitado. O comando aguarda o serviço oneshot.
Para execução direta em ambiente de homologação, provisionar `USUARIO`, `SENHA`,
`SOUENERGY_DB`, `SOUENERGY_LOCK`, `EXPORT_DIR` e `PLAYWRIGHT_BROWSERS_PATH` no
ambiente e executar:

```sh
cd /opt/souenergy-api/current
. venv/bin/activate
python3 -m coleta --help
python3 -m coleta --no-promote
python3 -m coleta
```

`--help` não abre navegador. `--no-promote` faz coleta real, grava observações e
encerra como `parcial`, sem promover, exportar ou publicar. O comando completo
promove apenas cobertura aprovada, exporta e publica somente quando
`PUBLICAR_AUTOMATICAMENTE=true`. Para promover sem push, manter essa variável
como `false`. Nenhuma coleta real foi executada durante a revisão PM.

Saídas: **0** sucesso; **1** erro operacional, exportação ou publicação;
**2** login falhou; **3** cobertura rejeitada; **5** lock ocupado. Após interrupção,
a próxima execução marca o ciclo anterior como `interrompido` e inicia outro;
as observações já gravadas permanecem disponíveis para diagnóstico.

## Preparação do VPS

Executar o provisionamento inicial como administrador:

```sh
useradd -r -m -d /var/lib/souenergy souenergy
install -d -o souenergy -g souenergy /opt/souenergy-api/releases \
  /var/lib/souenergy/backups /var/lib/souenergy/export \
  /var/lib/souenergy/.cache/ms-playwright
install -d -m 700 /etc/souenergy
```

Criar `/etc/souenergy/souenergy.env` fora do checkout, proprietário root e modo
0600, a partir dos nomes de `.env.example`. O systemd lê esse arquivo antes de
executar como `souenergy`. Configurar DB e lock em `/var/lib/souenergy`,
`EXPORT_DIR=/var/lib/souenergy/export` e o caminho de browsers acima. Segredos
reais nunca entram no Git, em argumentos de shell ou em logs. Rotacionar os
segredos historicamente expostos; remover valores do HEAD não saneia o histórico.

Provisionar Python 3.11+, venv, Git, curl e bibliotecas de sistema do Chromium.
O usuário SSH deve ser `souenergy`, com chave e `known_hosts` previamente
validados e autorização sudo restrita ao restart do serviço `souenergy`.
Instalar as dependências de sistema do Playwright como administrador durante o
provisionamento; a instalação do browser ocorre como usuário de serviço.

## Instalação e atualização

1. Instalar localmente `requirements.txt` e `requirements-deploy.txt`. Configurar
   somente por ambiente `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_KNOWN_HOSTS`
   e, se necessário, `VPS_PORT`. Os exemplos de unidades assumem os caminhos
   padrão e serviço `souenergy`; customizações devem ser aplicadas em conjunto.
2. Na primeira instalação, copiar `ops/souenergy-api.service` para
   `/etc/systemd/system/souenergy.service`. Copiar também as unidades
   `souenergy-scrape.*` e `souenergy-backup.*` para `/etc/systemd/system/`.
   Executar `systemctl daemon-reload`, sem habilitar ainda o timer de coleta.
3. Antes de atualizar, parar o timer e aguardar a coleta ativa terminar. Fazer
   backup consistente com `ops/backup.py`; não copiar apenas o SQLite com WAL
   ativo. Executar `python3 deploy_update.py`. O script envia o pacote completo
   com checksums, cria venv por release, instala dependências/Chromium, aplica
   migrações aditivas e troca `current` atomicamente. O DB fica persistente fora
   das releases. O healthcheck de `/` verifica processo e acesso ao banco;
   não comprova preços homologados nem autenticação dos endpoints protegidos.
4. Instalar novamente as unidades alteradas e executar `systemctl daemon-reload`.
   Verificar 403 em `/precos?potencia=7` sem chave e com chave inválida; com chave
   válida, esperar 503 antes da primeira coleta e 200 após homologação.
5. Fazer coleta manual de homologação, reconciliar as entradas do catálogo,
   verificar produtos/composição das quatro tabelas e aprovar a integração do
   JSON com o adaptador do solo-prices. As entradas padrão são SOLPLANET e
   HOYMILES, ampliadas pela navegação; isso não comprova cobertura total do site.
6. Só então habilitar os timers:

```sh
systemctl enable --now souenergy.service souenergy-backup.timer
systemctl enable --now souenergy-scrape.timer
systemd-analyze calendar 'Sun *-*-* 04:30:00 America/Sao_Paulo'
systemctl list-timers souenergy-scrape.timer
journalctl -u souenergy-scrape.service -n 100 --no-pager
```

Observar uma execução realmente agendada após a instalação. Push de código não
significa deploy realizado; a revisão local não acessou o VPS nem o Netlify.

## Publicação do solo-prices

Configurar checkout dedicado em `REPO_PROPUESTAS`, branch em
`REPO_PROPUESTAS_BRANCH`, credencial Git restrita e
`PRECIOS_URL_PUBLICA` apontando para o JSON publicado. Implantar primeiro o
adaptador compatível com schema v1 no repositório consumidor e confirmar as
regras comerciais. `PUBLICAR_AUTOMATICAMENTE=true` conecta coleta e publicação.
Tabelas vazias, conflitos ou variação de preço acima de `VARIACION_MAXIMA_PCT`
bloqueiam a publicação. A DB aprovada é preservada se exportação/push falharem.

O publicador não duplica commits por datas de captura, usa checkout temporário
e registra separadamente envio Git e confirmação HTTP/schema/dataset. Netlify
pode ainda estar construindo após o push; repetir a publicação do mesmo candidato
posteriormente, sem nova coleta:

```sh
python3 - <<'PY'
import json, os
from pathlib import Path
import db
from publicar_precos import publicar
c = db.conectar(os.environ['SOUENERGY_DB'])
p = Path(os.environ['EXPORT_DIR']) / 'precios.json'
try:
    publicar(c, p, json.loads(p.read_text())['dataset_id'],
             url_publica=os.environ['PRECIOS_URL_PUBLICA'])
finally:
    c.close()
PY
```

Executar sob o mesmo usuário/ambiente, sem concorrência com a coleta. Uma nova
coleta sobrescreve o candidato local; preservar o artefato em caso de revisão.
Preços publicados em arquivo estático são públicos: a liberação comercial deve
estar resolvida antes da ativação da publicação automática.

## Monitoramento, backup e rollback

A API marca dados como desatualizados após **192 horas** (7 dias + 24 horas de
tolerância), em vez das 36 horas previstas para coleta diária. Configurar monitor
externo independente consultando `/` e alertando quando `desactualizado=true` ou
quando o serviço estiver inacessível. `OnFailure` sozinho não detecta timer parado.
Configurar o destino `ALERTA_*` e verificar entrega durante a implantação.

Backups: `python3 ops/backup.py --db /var/lib/souenergy/catalogo.sqlite3
--dest /var/lib/souenergy/backups --keep 30` (comando em uma linha).
Para restaurar, parar timer, coleta e API, guardar DB/WAL/SHM atuais fora do caminho
ativo e restaurar backup com proprietário `souenergy`. Executar
`PRAGMA integrity_check` na cópia restaurada antes de reiniciar. Nunca misturar
WAL antigo com arquivo de backup restaurado.

`python3 deploy_update.py --rollback` restaura o ponteiro `previous`, preservado
após deploy bem-sucedido; não restaura dados nem unidades systemd. No primeiro
deploy ainda não existe release anterior. Migrações incompatíveis exigem plano
próprio. Para JSON incorreto, pausar publicação, marcar o hash como `revertido` em
`publicacoes` e reverter o commit de dados no consumidor. Não reativar até validar
a correção e o artefato servido.
