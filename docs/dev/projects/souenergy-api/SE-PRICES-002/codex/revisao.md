# SE-PRICES-002 — Revisão PM

Data: 13/09/2026. Branch: `feat/souenergy-full-scraper`. PR #2.

## Escopo e resultado

Revisados o contrato, o estudo, o plano e o diff
`master...feat/souenergy-full-scraper` (35 arquivos, 5.078 inserções e 401
remoções na implementação recebida). A leitura cobriu API, scraper, CLI, banco,
normalização, catálogo, exportação, publicação, alertas, deploy, configurações,
migração, schema, unidades systemd e testes. Plano e estudo foram preservados.
Uma alteração concorrente no relatório do Verboo foi mantida fora deste commit.

A revisão local está aprovada com as correções abaixo e **94 testes passando**.
Isso não constitui aceite de produção: não houve coleta real, SSH, publicação de
preços, verificação do consumidor ou deploy Netlify nesta revisão. O pedido atual
proíbe coleta real e solicita revisão, documentação, commit e push do código.

## Correções

- **CLI:** erro de abertura do lock retorna código operacional sem traceback;
  homologação `--no-promote` encerra como `parcial`, sem exportar snapshot antigo
  nem deixar execução pendurada. Registra autenticação, persiste observações
  durante a navegação e conecta o publicador à opção de publicação automática.
  Falha de exportação/publicação retorna código 1 e preserva o banco aprovado.
  Publicação automática bloqueia conflitos, tabelas vazias e variações anormais.
- **Scraper/catálogo:** sessão conferida depois de navegar, inclusive no último
  detalhe; erro HTTP ou bloqueio interrompe a coleta. Links de conta/carrinho e
  domínios externos ficam fora do percurso. Entradas compartilham fila e
  deduplicação global, preservando categorias e evitando visitar detalhes
  repetidos. Índices com marcador de categorias podem ser percorridos sem cards.
  Disponibilidade reconhece português e quantidade aceita “painéis”.
- **Banco/API:** status autenticado é persistido; consulta legada volta a escolher
  o grupo de potência por marca, incluindo o maior disponível se alvo exceder a
  faixa, e não mistura outras marcas em SOLPLANET. Consultas usam transação de
  leitura para evitar cruzar duas promoções. Banco ausente/não migrado retorna
  503. Potência deve ser positiva e finita; comparação de chave suporta bytes
  UTF-8. Dados antigos deixam de aparecer eternamente atualizados.
- **Exportação:** hash comercial exclui também `capturado_em` de cada linha;
  motivo explícito de não exportação é respeitado. Schema exige quatro chaves e
  validação verifica datas com fuso, além de tipos e consistência comercial.
- **Publicação:** fechamento do descritor temporário; comparação do hash remoto
  antes de copiar evita commits por timestamps. Retentativa após rebase não cria
  commit vazio. Registro de hash pode ser reutilizado após falha; confirmação de
  deploy pode ser repetida sem novo commit; hash revertido é bloqueado. HTTP
  verifica schema e dataset. Erros Git/alertas não reproduzem URLs com segredos.
- **Deploy:** import de Paramiko corrigido, com erro claro quando falta a
  dependência de deploy. Pacote exclui caches, chaves e arquivos de ambiente.
  Identificador de release validado e argumentos remotos escapados. Cria o
  diretório de releases, prepara venv/dependências/Chromium e migração antes da
  promoção. Ponteiros são trocados atomicamente; rollback explícito usa
  `previous`, em vez da própria release atual. Healthcheck tem timeout e valida
  JSON; unidade de API foi adicionada e unidades usam venv da release.

## Agenda e gatilho manual

Prevalece a nova decisão de Mateus sobre a frequência diária do plano histórico:
**domingo, 04:30, America/Sao_Paulo**, com até cinco minutos de atraso aleatório.
`Persistent=true` recupera um acionamento perdido. Backup continua diário às
04:45, consistente com WAL mesmo que a coleta ainda esteja executando.

**Gatilho manual: `python3 -m coleta`**, na raiz da release com venv e ambiente
configurados, sem depender da API. Em produção,
`sudo systemctl start souenergy-scrape.service` executa a mesma CLI com usuário,
segredos e logs já configurados. `--no-promote` é homologação com coleta real;
`--help` é a validação sem navegação. A API sinaliza atraso após **192 horas**,
uma semana mais 24 horas de tolerância. Monitor independente deve observar esse
limite, substituindo as 36 horas previstas para a frequência diária.

## Validação e segurança

- `python3 -m pytest tests/ -q --no-header`: **94 passed, 0 failed**.
- Mesmo resultado com Python 3.11 em ambiente isolado e versões de
  `requirements.txt` e `requirements-deploy.txt`. Apenas aviso de depreciação
  transitiva da infraestrutura de teste.
- CLI `python3 -m coleta --help`, ajuda do deploy, compilação Python e
  `git diff --check` aprovados. Ciclos manual e automático exercitados com mocks;
  regressões de rollback, pacote, autenticação, hash entre coletas, deduplicação,
  persistência incremental e publicação incluídas.
- `systemd-analyze calendar 'Sun *-*-* 04:30:00 America/Sao_Paulo'` aceitou a
  expressão; próxima execução informada: 20/09/2026 às 07:30 UTC (04:30 local).
- `.env` não rastreado; `.env` e `.env.production` ignorados. Campos sensíveis
  de `.env.example` vazios. Nenhuma correspondência dos segredos históricos
  procurados nos arquivos atuais, sem imprimir os valores. Deploy usa variáveis
  `VPS_*`, chave SSH e `known_hosts`, sem senha embutida ou `AutoAddPolicy`.
  A rotação e o saneamento do histórico continuam sendo providências externas;
  a revisão não afirma que já ocorreram.

## Implantação e pendências de aceite

O procedimento completo está em `ops/README.md`: provisionar usuário/diretórios,
Python com venv e bibliotecas do Chromium; configurar arquivo de ambiente root
0600; instalar unidade da API como `souenergy.service`; instalar unidades de
coleta/backup; preparar acesso SSH por chave com sudo restrito; parar timer e
aguardar coleta ativa; obter backup consistente; executar `deploy_update.py`;
atualizar unidades e recarregar systemd; verificar autenticação da API; homologar
coleta e só então habilitar timer. Rollback recupera release anterior, não dados
nem unidades systemd; restauração do banco exige parar escritores e separar WAL.

Antes de declarar “deploy completo”, falta evidência operacional de:

1. Catálogo real reconciliado, seletores/preços autenticados e quatro tabelas
   homologadas. Entradas padrão de duas marcas mais descoberta de navegação não
   comprovam sozinhas cobertura integral do site.
2. Adaptador do repositório propostas-soloenergia consumindo schema v1 e regras
   comerciais conferidas, autorização de publicar conteúdo estático e branch
   de dados configurada. Este worktree não entrega o adaptador do outro repo.
3. Rotação de segredos, serviço saudável, coleta manual e agendada observadas,
   monitor externo configurado, alerta entregue e restauração ensaiada.
4. SHA dos dados e dataset servidos pelo Netlify confirmados. O publicador pode
   retornar erro enquanto o build está pendente; o guia documenta retentativa
   do mesmo candidato sem repetir o scrape.

Limitações remanescentes: respostas HTTP de erro abortam de forma conservadora,
sem política específica de Retry-After; cobertura e seletores exigem homologação
real. O publicador deve ser executado serialmente com a coleta, conforme o guia.
Não há comprovação de execução em produção nesta revisão.
