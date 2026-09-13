# SE-PRICES-002 — Plano de implementação e validação

Data: 13/09/2026. Repositório: souenergy-api. Branch de trabalho prevista: `feat/souenergy-full-scraper`.

Codex atua como PM; Verboo executará a implementação. Este documento é o único entregável desta etapa: nenhuma implementação, alteração de configuração, rotação, conexão ao VPS, publicação ou commit foi realizada. O objetivo da execução posterior é entregar coleta periódica completa, SQLite durável e solo-prices atualizado em produção, com evidências de funcionamento.

## 1. Base de evidências e decisões

Foram lidos o contrato `contexto/spec.md`, o estudo `contexto/estudio-souenergy-solo-prices.md` (SE-STUDY-002), `api.py`, `scraper.py`, `deploy_update.py`, os nomes das variáveis de `.env.example` e `requirements.txt`. Referências de linha abaixo correspondem ao código atual. Credenciais não são reproduzidas.

Não houve inspeção autenticada do site, do VPS, da configuração Netlify ou do código do repositório propostas-soloenergia nesta etapa. As quatro tabelas e as 83 configurações são informações do contrato e do estudo, não uma contagem atual do catálogo. Seletores novos, categorias existentes, semântica dos campos comerciais e formato interno de `PRICE_DATA` precisam ser confirmados por Verboo antes da integração. Não usar estimativas do estudo como comprovação de cobertura ou desempenho.

Decisões principais:

- SQLite local como fonte de verdade; coleta por processo dedicado, com exclusão entre processos; API consulta a última coleta aprovada.
- Percorrer todo o catálogo comercial navegável da SouEnergy, incluindo marcas além de SOLPLANET e HOYMILES e produtos sem potência identificada. As quatro tabelas são apenas uma projeção do catálogo completo.
- Publicar `solo-prices/precios.json` versionado em propostas-soloenergia e consumido por `fetch` na mesma origem. Manter API autenticada como consulta operacional, sem chave no navegador.
- Agendamento diário por systemd timer; publicação automática somente após validação de cobertura, qualidade, mapeamento e diferença comercial.
- Uma coleta incompleta ou com autenticação não confirmada nunca substitui o catálogo aprovado nem o JSON em produção.

## 2. Como o site é tratado hoje e o que o preço representa

| Evidência | Conclusão e consequência |
| --- | --- |
| `scraper.py:15-17,256-257` | As duas entradas são categorias de inversores/microinversores por marca. Isso não comprova que sejam todo o catálogo. |
| `scraper.py:105-124` | Cards `.product-item` contêm `.product-item-link`, nome e URL. O código chama de produto o card com kWp/kW reconhecido; qualquer outro link vira suposta subcategoria. Essa classificação perde produtos sem potência e confunde equipamento avulso com kit. |
| `scraper.py:133-149` | Só desce se a raiz não tiver produtos, para na primeira subcategoria produtiva, ignora níveis seguintes e pagina a raiz com `?p=2` até `?p=5`. Não pagina corretamente cada subcategoria. |
| `scraper.py:151-158` | Deduplicação apenas por URL literal e dentro daquela coleta. |
| `scraper.py:213-225` | Filtra pela menor potência maior ou igual ao alvo, ou pela maior disponível quando não há suficiente; aplica tolerância de ±0,15 kWp e só abre detalhes desse grupo. |
| `scraper.py:164-201` | Preço e componentes vêm da página individual `.product-info-main`. O resultado descarta a URL, não tem fase, SKU, quantidade de módulos nem potência unitária do módulo. |
| `scraper.py:169-175` | `preco_pix` pode vir do seletor de preço final ou de qualquer `.price`; o nome do campo não garante que o valor seja PIX. Pode ser nulo. |

A unidade de preço observável pelo código é a oferta na página individual de produto, frequentemente um kit com composição e estrutura específicas. SouEnergy é a fonte/distribuidora; SOLPLANET e HOYMILES são rótulos de marca das categorias, não fornecedores independentes nem garantia da marca do módulo. Não existe evidência de um preço único por marca, potência ou fornecedor. Dois kits de mesma potência podem ter inversor, módulo, fase, estrutura, quantidade e preço diferentes; preservar ambos.

Distinguir potência total fotovoltaica em kWp, potência nominal do inversor em kW e potência unitária do painel em W. `extrair_kwp` aceita kW como kWp e remove todos os pontos (`scraper.py:44-55`), podendo transformar `7.44` em `744`. Corrigir antes de usar potência para seleção ou exportação. A tolerância atual serve ao comportamento legado de consulta e não deve unir identidades de produtos.

O estudo §3 acerta que a filtragem por potência vem depois da descoberta, mas a expressão “todos os produtos” é condicionada pelas limitações acima. A implementação deve remover essas limitações, não apenas retirar `potencia_alvo`.

## 3. Arquitetura e arquivos previstos

Fluxo: descoberta autenticada → detalhes de todas as URLs → observações por execução → validação → transação de promoção em SQLite → exportação determinística → publicação Git → deploy Netlify → verificação do artefato servido.

| Arquivo a criar/modificar na execução | Responsabilidade |
| --- | --- |
| `scraper.py` | Descoberta recursiva, paginação por categoria, autenticação estrita, leitura de detalhes e `scrapear_tudo()`, sem persistência ou Git. |
| `normalizacao.py` | Dinheiro, unidades, fase, marca do inversor, tipo de inversor, potência/quantidade do módulo e proveniência. |
| `catalogo.py` | Configuração de entradas, classificação, URLs canônicas e relatório de cobertura. |
| `db.py`, `migrations/001_catalogo.sql` | Conexões SQLite, migrações versionadas, observações, promoção atômica, histórico e consultas. |
| `coleta.py` | CLI do ciclo completo, lock, retomada controlada, estados, limites e códigos de saída. |
| `exportar_precos.py`, `schemas/precios.schema.json`, `config/mapeamento_solo_prices.json` | Projeção das quatro tabelas, regras verificáveis e validação do contrato. |
| `publicar_precos.py`, `alertas.py` | Publicação idempotente, acompanhamento de deploy e notificações com tentativas limitadas. |
| `api.py` | Consultas em SQLite, autenticação fechada por padrão e compatibilidade documentada dos endpoints. |
| `deploy_update.py` | Substituir envio parcial por release verificável, autenticação SSH segura, verificação de saída e rollback. |
| `.env.example`, `.gitignore`, `requirements.txt` | Apenas nomes/placeholders de configuração; excluir segredos, DB, backups e sessões; fixar versões testadas. |
| `ops/souenergy-scrape.service`, `ops/souenergy-scrape.timer`, `ops/README.md` | Instalação, timer, permissões, recuperação, backup e operação. |
| `tests/`, fixtures sanitizadas e CI | Testes de descoberta, normalização, persistência, contrato e falhas. |
| Em propostas-soloenergia: `solo-prices/index.html`, `solo-prices/precios.json`, adaptador JS e testes | Consumir o contrato sem alterar implicitamente margem, geração estimada ou regras comerciais. Ajustar a configuração Netlify/CI existente após inspecioná-la. |

Antes de programar o adaptador, ler `solo-prices/index.html` e registrar: chaves e unidades reais de `PRICE_DATA`, seleção de potência, quantidade de módulos, preço de compra versus venda, margem, frete, estrutura, arredondamento e estimativa de geração. O estudo §5.4 explicitamente deixa essa inspeção pendente. Não substituir preço final de proposta por custo PIX do distribuidor sem verificar a fórmula. Nenhuma linha antiga é assumida equivalente apenas por potência.

### API e concorrência

`api.py:39-45,68-92,139` usa memória e `BackgroundTasks`; reiniciar perde jobs e o lock não protege processos diferentes. Retirar execução de Playwright dos endpoints. `GET /precos?potencia=` consulta o snapshot aprovado, preservando nomes e agrupamento legado quando necessário, mas com `source=database`, data da coleta e indicação de desatualização. Sem snapshot, responder 503; dados antigos permanecem disponíveis com idade explícita.

Preservar `POST /jobs` e `GET /jobs/{id}` como camada de compatibilidade: criar resultado de consulta já concluído, persistido em `jobs_consulta`, retornando 202 e URL de consulta como hoje; nenhuma fila volátil de scraping. Registrar essa mudança semântica e validar consumidores. A seleção legada acima/abaixo do alvo pode permanecer nesses endpoints; a interface de propostas deve avisar quando não existe configuração suficiente, sem prometer atender uma potência maior com kit menor.

API abre catálogo em modo leitura; a tabela de resultados de consulta recebe transações curtas separadas das transações de promoção. O coletor é o único escritor do catálogo. Um `flock` adquirido atomicamente antes de abrir browser protege execuções manuais e agendadas; nunca confiar em verificar a existência de um arquivo ou em `threading.Lock`. Publicador também tem exclusão própria.

## 4. Coleta completa e confiável

### 4.1 Inventário e travessia

1. Em sessão autenticada, inventariar menu de categorias e páginas de listagem; sitemap, se disponível, é fonte complementar para confrontar cobertura. Restringir navegação ao domínio autorizado e a rotas de catálogo; excluir carrinho, logout, conta e combinações ilimitadas de filtros. Registrar entradas descobertas e escopo em configuração. Não limitar a duas marcas.
2. Refatorar `carregar_url_e_coletar_cards` para retornar separadamente candidatos a produto, categorias filhas, próxima página e estado de carregamento. Ausência de seletor por timeout é erro, não “catálogo vazio”. Verificar estado explícito de listagem vazia.
3. `coletar_todos_produtos` executa fila BFS/DFS com conjuntos globais de categorias, páginas e produtos visitados. Enfileirar categorias mesmo quando há produtos na página; percorrer irmãos e todos os níveis. Não usar potência como classificador: inspecionar tipo de página/DOM, dados estruturados e detalhe quando ambíguo.
4. Paginar cada categoria pelo link real de próxima página. Se houver apenas parâmetro numérico, preservar os demais parâmetros pertinentes e incrementar o parâmetro daquela categoria. Detectar repetição por URL canônica e assinatura dos cards. Encerrar com fim explícito ou vazio confirmado, não com falha de rede. Remover teto de cinco; limite operacional de páginas/tempo ainda existe, mas alcançá-lo marca execução incompleta e bloqueia promoção.
5. Scroll deve verificar estabilização da quantidade de cards e eventual carregamento incremental, em vez de quatro movimentos fixos (`scraper.py:37-42`). Limites de estabilização também precisam gerar diagnóstico se houver conteúdo pendente.
6. Canonicalizar URLs relativas, fragmentos e parâmetros de rastreamento. Preservar parâmetros que identificam variantes; seguir URL canônica somente se não colapsar variantes comercialmente distintas. Deduplicar globalmente por identidade canônica; SKU/ID do site serve como evidência adicional. Não deduplicar por nome, preço ou potência. Produto em várias categorias mantém todas as associações.
7. `processar_categoria` visita todos os candidatos únicos, sem `potencia_alvo`, clusters ou tolerância. `scrapear_tudo()` mantém uma sessão e devolve inventário, observações e métricas. Produtos avulsos ou sem campos solares permanecem no banco, com classificação explícita, e não são forçados nas quatro tabelas.

### 4.2 Login, extração e bloqueios

- Validar `USUARIO` e `SENHA` antes de iniciar. Após cada tentativa, inclusive a última, confirmar marcador autenticado e ausência de erro/formulário de login. O fluxo atual `scraper.py:248-254` não garante isso. No máximo duas tentativas por ciclo; falha gera estado `login_falhou`, saída não zero e nenhum preço promovido.
- Conferir sessão nas transições relevantes e em detalhes. Expiração exige reautenticação limitada e repetição da página afetada; se não confirmada, descartar promoção. Não misturar observações de visitante e cliente. Registrar identificador interno não sensível do contexto de preço e alertar se mudar.
- Confirmar seletor específico de PIX dentro da área do produto. Preço normal, promocional, parcelado e PIX são campos distintos; não preencher PIX com fallback genérico. Capturar texto original, seletor/origem e moeda. Sem PIX comprovado, manter nulo e motivo; nunca converter ausência em zero.
- Extrair fase da ficha técnica e título com precedência documentada; conflito ou ausência fica nulo. Microinversor é tipo de equipamento, não fase elétrica. Capturar `marca` como marca do inversor quando comprovada, `marca_modulo` separadamente, modelo, quantidade de módulos e potência unitária em W. Potência total pode ser calculada por quantidade × W / 1000 somente com composição comprovada, guardando origem; divergência com ficha vai para revisão.
- Capturar disponibilidade e SKU quando publicados; uma página válida sem preço pode ser produto indisponível, não erro de transporte. Erros de parsing inesperados não devem ser disfarçados de indisponibilidade.
- Um navegador, concorrência inicial de uma página de detalhe, intervalo inicial de 2–5 segundos com variação e ajuste após medição. Respeitar restrições de acesso e rate limit. Em 429 observar `Retry-After`; em timeout/5xx usar até três tentativas com espera progressiva. Em 403 persistente, CAPTCHA ou bloqueio, encerrar e alertar; não contornar desafios. Não presumir que anti-bot foi observado: é risco previsto pelo estudo §7.
- Fechar browser/contexto em `finally`; registrar por URL tentativas e erro sanitizado. Evidências HTML devem excluir dados de conta e nunca incluir cookies, tokens ou senhas.

### 4.3 Critério de completude

Cada execução gera contagens por categoria: páginas planejadas/visitadas, produtos únicos descobertos, detalhes válidos, indisponíveis comprovados, falhas, duplicatas e itens fora do mapeamento. Exigir fila esgotada, nenhuma falha de navegação/detalhe sem resolução e sessão autenticada. Um produto legitimamente sem potência não é falha de cobertura.

Primeiro ciclo exige reconciliação manual do inventário com navegação/sitemap/contadores existentes. Depois, bloquear promoção se zero produtos ou queda superior a 20% contra o último ciclo aprovado, por total ou categoria anteriormente não vazia. Esse é um limite inicial conservador proposto, ajustável com justificativa registrada; não é frequência ou comportamento comprovado do site. Desaparecimento confirmado em duas coletas completas consecutivas torna o produto inativo; falhas parciais nunca causam remoção. Caso queda legítima exija revisão, registrar aprovação operacional com motivo antes de promover.

## 5. Modelo SQLite

Usar `sqlite3` da biblioteca padrão, `foreign_keys=ON` em todas as conexões, WAL e `busy_timeout` definido. Banco fora do repositório e do diretório de releases, em disco local. Datas UTC em ISO 8601. Valores monetários autoritativos em centavos inteiros; o campo `preco_normalizado` abaixo significa centavos BRL, evitando erros de comparação em ponto flutuante. Na exportação fornecer também reais numéricos, derivados desses centavos.

Esquema inicial proposto, a aplicar por migração versionada:

```sql
CREATE TABLE scrapes (
  id TEXT PRIMARY KEY,
  iniciado_em TEXT NOT NULL,
  finalizado_em TEXT,
  status TEXT NOT NULL CHECK(status IN
    ('executando','ok','parcial','erro','login_falhou','rejeitado','interrompido')),
  versao_coletor TEXT NOT NULL,
  contexto_preco TEXT NOT NULL,
  autenticado INTEGER NOT NULL DEFAULT 0 CHECK(autenticado IN (0,1)),
  descobertos INTEGER NOT NULL DEFAULT 0,
  processados INTEGER NOT NULL DEFAULT 0,
  falhas INTEGER NOT NULL DEFAULT 0,
  alteracoes INTEGER NOT NULL DEFAULT 0,
  cobertura_json TEXT,
  erro_codigo TEXT
);
CREATE TABLE productos (
  id INTEGER PRIMARY KEY,
  url TEXT NOT NULL UNIQUE,
  sku TEXT,
  nome TEXT NOT NULL,
  tipo_produto TEXT NOT NULL DEFAULT 'desconhecido',
  marca TEXT,
  marca_modulo TEXT,
  potencia_kwp REAL CHECK(potencia_kwp > 0),
  fase TEXT CHECK(fase IN ('mono','tri','bi')),
  tipo_inversor TEXT CHECK(tipo_inversor IN ('string','micro','outro')),
  inversor TEXT,
  modulo TEXT,
  modulo_potencia_w INTEGER CHECK(modulo_potencia_w > 0),
  quantidade_modulos INTEGER CHECK(quantidade_modulos > 0),
  estrutura TEXT,
  preco_pix TEXT,
  preco_normalizado INTEGER CHECK(preco_normalizado > 0),
  moeda TEXT NOT NULL DEFAULT 'BRL' CHECK(moeda = 'BRL'),
  disponibilidade TEXT NOT NULL DEFAULT 'desconhecida',
  origem_campos_json TEXT NOT NULL,
  motivo_nao_exportavel TEXT,
  primeira_visita TEXT NOT NULL,
  fecha_scrapeo TEXT NOT NULL,
  ultimo_scrape_id TEXT NOT NULL REFERENCES scrapes(id),
  ausencias_completas INTEGER NOT NULL DEFAULT 0,
  ativo INTEGER NOT NULL DEFAULT 1 CHECK(ativo IN (0,1))
);
CREATE TABLE observacoes (
  scrape_id TEXT NOT NULL REFERENCES scrapes(id),
  url TEXT NOT NULL,
  capturado_em TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('ok','indisponivel','erro')),
  dados_json TEXT,
  erro_codigo TEXT,
  tentativas INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(scrape_id, url)
);
CREATE TABLE produto_categorias (
  producto_id INTEGER NOT NULL REFERENCES productos(id),
  categoria_url TEXT NOT NULL,
  PRIMARY KEY(producto_id, categoria_url)
);
CREATE TABLE historico_precios (
  id INTEGER PRIMARY KEY,
  producto_id INTEGER NOT NULL REFERENCES productos(id),
  scrape_id TEXT NOT NULL REFERENCES scrapes(id),
  preco INTEGER CHECK(preco > 0),
  preco_pix TEXT,
  fecha TEXT NOT NULL,
  motivo TEXT NOT NULL CHECK(motivo IN
    ('inicial','alterado','indisponivel','restabelecido')),
  UNIQUE(producto_id, scrape_id)
);
CREATE INDEX idx_historico_produto_fecha
  ON historico_precios(producto_id, fecha);
CREATE INDEX idx_produtos_tabela
  ON productos(ativo, marca, fase, tipo_inversor, modulo_potencia_w, potencia_kwp);
CREATE TABLE publicacoes (
  id INTEGER PRIMARY KEY,
  scrape_id TEXT NOT NULL REFERENCES scrapes(id),
  hash_conteudo TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK(status IN
    ('pendente','enviado','implantado','erro','revertido')),
  commit_sha TEXT,
  deploy_id TEXT,
  criado_em TEXT NOT NULL,
  confirmado_em TEXT,
  erro_codigo TEXT
);
CREATE TABLE jobs_consulta (
  id TEXT PRIMARY KEY,
  potencia REAL NOT NULL,
  criado_em TEXT NOT NULL,
  scrape_id TEXT REFERENCES scrapes(id),
  status TEXT NOT NULL CHECK(status IN ('done','error')),
  resultado_json TEXT,
  erro_codigo TEXT
);
```

Nomes `productos`, `historico_precios` e `fecha_scrapeo` mantêm a proposta do contrato; o restante da documentação e mensagens deve usar português. Fase admite `bi` para não perder dados do catálogo; somente mono/tri confirmados entram nas respectivas tabelas SOLPLANET. Campos desconhecidos podem ser nulos, nunca inventados.

Normalização: remover símbolo e espaços, validar agrupamento brasileiro, remover ponto de milhar e converter vírgula decimal com `Decimal`; `R$ 12.345,67` (exemplo fictício) resulta em 1.234.567 centavos e 12345.67 reais. Formato estruturado com ponto decimal tem parser separado. Rejeitar texto de parcelas, múltiplos valores, formato ambíguo e moeda inesperada. Potência tem parser próprio: `7,44 kWp` e `7.44 kWp` devem resultar em 7.44; `5 kW` do inversor não basta para afirmar potência fotovoltaica.

Persistir observações incrementalmente, sem transação aberta durante navegação. Só após validar execução inteira, uma transação curta faz upsert por URL, atualiza categorias/ausências, grava histórico e marca scrape como `ok`. Histórico registra primeiro preço e mudanças reais, inclusive valor → nulo confirmado e restabelecimento; não repetir preço igual diariamente. Observações preservam prova das visitas sem mudança. Reexecutar promoção com o mesmo `scrape_id` não duplica histórico. Processo interrompido mantém observações de diagnóstico e é marcado `interrompido` na recuperação; iniciar nova coleta autenticada para promoção, sem juntar dias diferentes.

Backup por API de backup SQLite ou ferramenta consistente equivalente; não copiar apenas o arquivo principal enquanto WAL está ativo. Testar restauração e `integrity_check`. Propor 30 backups diários; histórico comercial permanece, observações detalhadas podem ter retenção de 90 dias após medir volume e necessidade operacional.

## 6. Contrato com solo-prices

### 6.1 Opções e recomendação

| Opção | Vantagens | Custos e limites |
| --- | --- | --- |
| Gerar `PRICE_DATA` embutido | Mantém carregamento estático e dispensa fetch. | Acopla exportador ao código do HTML; mistura revisão de dados e código; requer deploy. |
| API JSON no navegador | Atualização sem deploy e consultas dinâmicas. | Depende do VPS em tempo de uso; exige desenho de acesso/CORS/cache. `X-API-Key` não pode ficar em JS público. |
| `precios.json` versionado | Histórico revisável, artefato validável e reversível, mesma origem no Netlify, sem dependência do VPS ao abrir a página. | Requer publicação entre repositórios e deploy por atualização; dado fica público como qualquer arquivo estático. |

Adotar a terceira opção, conforme estudo §5. A autenticação existente no HTML não deve ser presumida uma barreira de confidencialidade do JSON. Confirmar com o responsável comercial que os preços da conta podem integrar o mesmo conteúdo estático; se exigirem confidencialidade, trocar a entrega por backend autenticado/proxy e controle real de acesso antes de publicar. Nunca incluir credenciais, informações da conta ou cookies no artefato.

### 6.2 Mapeamento determinístico

| Chave pública proposta | Critérios obrigatórios |
| --- | --- |
| `mono_solplanet_620` | Kit, inversor SOLPLANET, tipo string, fase mono, módulo unitário 620 W. |
| `tri_solplanet_620` | Kit, inversor SOLPLANET, tipo string, fase tri, módulo unitário 620 W. |
| `micro_hoymiles_620` | Kit, microinversor HOYMILES, módulo unitário 620 W. |
| `micro_hoymiles_710` | Kit, microinversor HOYMILES, módulo unitário 710 W. |

Potência total é chave de seleção dentro da tabela, não critério para descobrir o catálogo. Na tabela micro, fase continua atributo real do kit e pode ser nula; “micro” não vira fase. Marca de categoria é apenas pista; confirmar o inversor do produto. Não pressupor módulo SOLPLANET por o inversor ter essa marca.

Exportar somente produtos ativos, disponíveis, com PIX autenticado válido e composição suficiente para reproduzir os campos usados pela interface. Produtos fora dessas regras continuam no banco com motivo; emitir relatório de elegíveis, não mapeados e conflitos. Não forçar marcas/potências novas nas quatro tabelas.

Preservar variantes por identidade de produto e composição. Se a UI aceitar só uma linha por potência, definir em configuração a composição elegível (inclusive estrutura) para aquela linha após ler `PRICE_DATA`; entre equivalentes escolher menor PIX, com desempate por URL. Sem equivalência comprovada, não escolher silenciosamente: bloquear a linha e reportar conflito. Quantidade de módulos deve ser extraída/confirmada; não arredondar potência para inventar quantidade. Manter regras de geração e preço de venda no adaptador, com testes de regressão.

### 6.3 Formato `precios.json` v1

Exemplo de contrato proposto, com valores fictícios e coleção reduzida. Não é reprodução do `PRICE_DATA` atual. Em produção todas as quatro chaves são obrigatórias e a primeira publicação exige produtos validados em cada tabela ou decisão comercial documentada sobre indisponibilidade.

```json
{
  "schema_version": 1,
  "dataset_id": "sha256-do-conteudo-comercial",
  "fonte": "SouEnergy",
  "moeda": "BRL",
  "coleta_aprovada_em": "2026-09-13T08:00:00Z",
  "conteudo_alterado_em": "2026-09-13T08:00:00Z",
  "tipo_preco": "pix_kit",
  "tabelas": {
    "mono_solplanet_620": [
      {
        "produto_id": "souenergy:identificador-estavel",
        "url": "https://souenergy.com.br/kit-exemplo-ficticio.html",
        "nome": "Kit ilustrativo 7,44 kWp",
        "marca": "SOLPLANET",
        "marca_modulo": null,
        "fase": "mono",
        "tipo_inversor": "string",
        "potencia_kwp": 7.44,
        "modulo_potencia_w": 620,
        "quantidade_modulos": 12,
        "inversor": "Modelo confirmado na ficha",
        "modulo": "Modelo confirmado na ficha",
        "estrutura": "Composição confirmada na ficha",
        "preco_pix_centavos": 1234567,
        "preco_pix": 12345.67,
        "capturado_em": "2026-09-13T07:50:00Z"
      }
    ],
    "tri_solplanet_620": [],
    "micro_hoymiles_620": [],
    "micro_hoymiles_710": []
  }
}
```

IDs públicos devem ser estáveis, derivados de SKU/variante confirmado ou hash da URL canônica, sem depender do inteiro local do SQLite. Schema valida tipos, moeda, datas, enumerações, unicidade, positividade, quatro chaves e consistência reais/centavos e quantidade × W versus kWp, com precisão justificada da fonte. Campo desconhecido é nulo apenas se aceito explicitamente pelo schema e pela interface. Alteração incompatível exige nova versão e adaptador compatível antes da publicação.

Ordenar tabelas/linhas de forma estável por potência e identidade. Hash comercial cobre seleção, composição, disponibilidade, preço e versão de mapeamento; exclui timestamps e ID da execução. Sem mudança comercial, não gerar commit diário por mudança de data: o artefato servido continua mostrando a coleta que o originou. Registrar a última verificação diária no VPS; heartbeat público semanal opcional renova `coleta_aprovada_em` mediante coleta aprovada, sem confundir isso com alteração de preço. Se adotado, usar identidade de publicação própria para o heartbeat além do hash comercial único. Na primeira versão, preferir sem heartbeat e nomear a data na UI como “dados desta publicação”, não “última verificação diária”.

O HTML carrega JSON antes de habilitar cálculo, valida versão e apresenta erro legível em falha. Manter snapshot anterior conhecido como fallback local da mesma release, com data e aviso explícitos; nunca inventar preços nem exibir fallback como atualizado. Testar tratamento de tabela vazia e alvo fora da faixa. Cache de JSON deve revalidar conforme configuração Netlify existente, evitando HTML novo com contrato antigo.

## 7. Periodicidade, mudança e alertas

Executar diariamente às 04:30 em `America/Sao_Paulo` (07:30 UTC na data deste plano), timezone explícito no timer. Periodicidade diária segue recomendação do estudo; a frequência real de mudanças não foi medida. Semanal fica como contingência por bloqueio/custo comprovado, com idade dos dados visível e revisão após uma semana de métricas.

Comparar centavos por produto contra snapshot aprovado; separar mudanças de preço, composição, disponibilidade e mapeamento. Publicação tem segunda barreira: tabelas esperadas não podem desaparecer inesperadamente; alteração absoluta acima de 30% em preço exige revisão operacional inicial. Registrar motivo de liberação, sem arredondar ou ignorar anomalia automaticamente. A DB pode guardar coleta tecnicamente válida enquanto publicação comercial fica pendente.

Alertas previstos, enviados apenas na execução e pelo canal operacional configurado: login falho, 403/CAPTCHA/429 persistente, zero produtos, cobertura incompleta, queda de contagem, mapeamento inválido, variação anormal, disco cheio, backup falho, publicação/deploy falho e ausência de coleta aprovada por 36 horas. Resumo de mudanças contém produto, preço anterior/novo e totais, sem segredos. Deduplicar alerta por execução/evento e limitar repetição. Um monitor independente deve verificar o prazo de 36 horas; `OnFailure` sozinho não detecta timer que deixou de executar. Configurar destino e confirmar entrega com teste operacional, sem enviar mensagens nesta etapa de planejamento.

## 8. Deploy no VPS e no Netlify

### 8.1 Preparação e segurança — prioridade P0

1. Rotacionar credenciais expostas de SouEnergy, API e acesso SSH; invalidar valores antigos e atualizar consumidores autorizados. A exposição está documentada no estudo §1/§7, em `.env.example` e `deploy_update.py`. Não registrar valores em issue, log ou plano. Remover segredos dos arquivos correntes; revisar histórico Git e coordenar eventual saneamento sem tratá-lo como substituto da rotação.
2. `.env.example` somente com placeholders. Segredos reais ficam em arquivo fora do checkout, modo 0600, carregado por `EnvironmentFile`, ou gerenciador de segredos disponível. Variáveis: `USUARIO`, `SENHA`, `API_KEY`, caminho do DB, timeout, entradas do catálogo, configuração de publicação e destino de alertas; nenhum valor real versionado.
3. API falha na inicialização quando `API_KEY` está ausente/vazia e usa comparação constante. Hoje ausência de chave configurada e header ausente passam pela comparação de `api.py:50-53`.
4. SSH por chave de usuário dedicado, privilégios mínimos, `known_hosts` previamente validado, sem `AutoAddPolicy` e sem senha embutida. Identificar o serviço e diretório reais antes da instalação; não assumir que os caminhos propostos já existem.
5. Fixar dependências testadas e versão compatível do Chromium. `requirements.txt` hoje lista só FastAPI, uvicorn, Playwright e dotenv, sem versões; `deploy_update.py` importa Paramiko fora dessa lista. Se Paramiko continuar, declarar dependência de deploy separada; caso contrário usar SSH do sistema.

### 8.2 Instalação e promoção

1. Inspecionar serviço `souenergy`, usuário, Python, arquitetura, disco/RAM, timezone, proxy e mecanismo de deploy atuais; registrar baseline de healthcheck e release. Backup consistente do banco se existente e snapshot da configuração sem segredos no relatório.
2. Preparar release imutável em diretório versionado, venv isolado, dependências e Chromium com bibliotecas de sistema compatíveis. Usuário de coleta sem privilégios de superusuário; DB persistente proposto em `/var/lib/souenergy/catalogo.sqlite3`, segredos em `/etc/souenergy/`, lock em diretório de runtime apropriado.
3. Rodar migrações e testes com cópia/restauração validada. Fazer coleta manual de homologação sem push, revisar cobertura e amostras, gerar JSON candidato e validar schema/adaptador.
4. Instalar serviço de coleta `Type=oneshot`, `User` dedicado, `WorkingDirectory`, `EnvironmentFile`, `ExecStart` da CLI, encerramento de processos filhos e logs no journal. Limite inicial de execução de 60 minutos, ajustado por coleta medida; ultrapassá-lo falha, não aprova parcialmente. Prever cache do browser e diretórios graváveis necessários ao Playwright ao aplicar isolamento do serviço.
5. Timer com `OnCalendar` diário no timezone declarado, `Persistent=true` para recuperar disparo perdido e pequena variação opcional. Verificar interpretação com ferramentas do systemd instaladas. `Persistent` não deve executar todo o histórico de dias perdidos. Não instalar crontab e timer simultaneamente. Crontab seria alternativa mais simples, mas exigiria implementar à parte recuperação, ambiente, logs e observabilidade.
6. Trocar ponteiro da release somente após checks; reiniciar API e verificar autenticação e consultas. O deploy atual envia só três arquivos e não verifica o código de saída remoto: substituir por pacote completo com manifesto/checksum, checagem de cada comando e falha imediata. Configuração/DB não devem ser sobrescritos pelo deploy.
7. Habilitar timer somente após coleta manual e integração aprovadas; verificar próxima execução, journal e, depois, uma execução efetivamente disparada pelo timer. Coleta não deve interromper consultas da API.

### 8.3 Publicação em propostas-soloenergia

1. Inspecionar branch de produção, regras de proteção, diretório publicado e ligação Git–Netlify. Implantar primeiro adaptador compatível com schema v1 e snapshot inicial revisado, usando preview para validação.
2. Publicador usa checkout isolado e limpo, credencial de máquina restrita ao repositório de propostas e somente arquivos de dados esperados. Validar arquivo temporário, substituir atomicamente e criar commit apenas quando mudar o hash comercial. Nunca sobrescrever alterações humanas, fazer force-push ou incluir outros arquivos.
3. Respeitar branch protection: branch/PR de dados com checks e integração automática se permitida; caso haja push direto já permitido, aplicar os mesmos checks antes do push. Confirmar política real durante preparação e entregar caminho periódico sem depender de aprovação manual por atualização normal. Anomalias continuam exigindo revisão.
4. Se a branch avançar, atualizar a base e reaplicar apenas o artefato validado; conflito não resolvido vira erro de publicação. Guardar hash/commit/deploy em `publicacoes`. Falha de rede retoma o envio do mesmo artefato, sem repetir scraping nem duplicar commit já enviado.
5. Se Git–Netlify já dispara deploy, usar esse mecanismo. Build hook só se necessário e guardado como segredo; evitar disparos duplicados. Confirmar deploy concluído, HTTP 200 do JSON e página, schema e `dataset_id` esperados em `https://propostas.soloenergia.com.br/solo-prices/`. Push bem-sucedido não equivale a publicação concluída.
6. Separar estados: DB aprovada, Git enviado e Netlify confirmado. Se deploy falhar, o site mantém release anterior, sinalizar atraso e tentar novamente com limite. Publicação revertida exige bloqueio do hash rejeitado até correção, para o timer não republicá-lo.

## 9. Sequência de execução e validação pelo PM

| Etapa | Entrega de Verboo | Critério de passagem |
| --- | --- | --- |
| A — inventário e P0 | Evidência sanitizada de catálogo, leitura de `PRICE_DATA`, rotação e configuração segura. | Escopo completo definido; nenhum segredo novo; contratos comerciais identificados. |
| B — coleta e DB | Descoberta, normalização, staging, migração, lock e testes. | Coleta completa manual, promoção atômica e dados duráveis. |
| C — contrato e UI | JSON candidato, adaptador e relatório de mapeamento. | Quatro tabelas reconciliadas; composição e cálculos verificados no preview. |
| D — produção | Release, timer, publicador, backup e monitor. | Execução manual e agendada, deploy confirmado e rollback ensaiado. |
| E — aceite | Relatório com commits/PRs, métricas e URLs de evidência. | PM revisa critérios abaixo; só então declarar objetivo concluído. |

Testes exigidos para a implementação, priorizados pelo risco:

- Fixtures de catálogo: raiz com produtos e subcategorias, dois irmãos produtivos, três níveis, mais de cinco páginas, paginação própria de subcategoria, produto sem kWp, equipamento avulso, marcas adicionais, duplicata entre categorias, variante por URL, página repetida, listagem vazia real e timeout. Conferir conjunto esperado de identidades, não só contagem.
- Login falho e sessão expirada: confirmar ausência de promoção/publicação; 429 com espera, 403/CAPTCHA, falha de seletor e erro parcial têm estados e alertas esperados.
- Normalização: milhares/centavos, espaços especiais, preço parcelado rejeitado, PIX ausente, vírgula/ponto decimal em potência, kW versus kWp, fase desconhecida/conflitante, micro versus fase, módulos 620/710 e outras potências. Verificar que fallback genérico não vira PIX.
- DB temporária: primeiro ciclo, preço igual, mudança de um centavo, nulo comprovado, restauração de disponibilidade, mesmo scrape promovido duas vezes, interrupção antes da promoção, duas ausências completas e falha parcial entre elas. Reinício preserva catálogo e jobs; duas execuções concorrentes abrem apenas um browser. Exercitar leitura da API durante promoção.
- Exportação: saída determinística, timestamps não causam mudança comercial, quatro chaves, identidade estável, rejeição de ambiguidade e unidade monetária consistente; repetir publicação depois de erro de rede sem duplicar commit.
- Integração: comparar casos de cada tabela com produto autenticado e com regras originais da UI; testar potência mínima, intermediária e acima do máximo, variantes e tabela vazia. Reconciliar as 83 configurações citadas: cada linha antiga fica mapeada, substituída com justificativa ou explicitamente indisponível. Não exigir que o catálogo atual tenha exatamente 83 itens.
- Inspeção manual de pelo menos três produtos por tabela quando existirem, mais produtos fora das quatro tabelas e casos sem preço/sem potência. Comparar nome, URL, componentes, fase, W do módulo, quantidade, kWp e PIX com sessão autenticada; ampliar amostra em divergência. Relatório não deve conter dados da conta.
- Produção: serviço saudável; API sem chave e com chave inválida retorna 403, configuração sem chave impede startup; consulta válida responde a partir do DB; DB íntegra; job sobrevive reinício; timer tem próxima execução e uma execução observada; monitor detecta ausência simulada; alerta de teste chega ao destino configurado.
- Netlify: preview e produção carregam as quatro tabelas, não expõem segredo, exibem data correta, não confundem custo com preço final, tratam erro de fetch e entregam hash esperado. Simular falha de publicação mantendo versão anterior.
- Recuperação: restaurar backup em ambiente isolado e ensaiar retorno de release/JSON. Registrar duração real, memória, produtos/páginas por categoria e comparação de totais; a estimativa de 5–15 minutos do estudo não é critério de sucesso.

Evidência final requerida: versão do coletor, ID e relatório da coleta aprovada, totais de cobertura e mapeamento, queries resumidas de DB/histórico, resultado de testes, timer/journal sanitizados, hash JSON, SHA do commit de dados, ID do deploy, URL pública verificada e resultado de restauração. Abertura de PRs e reporte em canais pertencem à execução posterior conforme contrato; não foram feitos nesta etapa.

## 10. Rollback e recuperação

1. Diante de dados incorretos, pausar timer/publicador, preservar observações e bloquear o hash rejeitado. Não apagar evidência nem sobrescrever snapshot bom com coleta parcial.
2. No Netlify, restaurar última release aprovada e reverter o commit de dados por commit normal no Git, mantendo código e schema compatíveis; confirmar hash e cálculo da página. Não depender apenas do rollback visual do provedor, pois novo deploy poderia reintroduzir dados ruins.
3. Na API/coletor, retornar ao ponteiro da release anterior segura e executar healthcheck autenticado. Nunca retornar a versão com credenciais expostas ou autenticação aberta: manter correções P0 ou aplicar correção mínima segura.
4. Migrações iniciais devem ser aditivas. Antes de mudança incompatível, parar escritores e obter backup consistente; restaurar backup somente com processos desconectados e compatibilidade da release verificada. Não executar downgrade destrutivo improvisado nem perder histórico sem registrar o impacto.
5. Se somente a publicação falhou, manter DB aprovada e reenviar artefato conhecido; se a coleta falhou, manter snapshot anterior e indicar idade. Se a origem bloquear acesso, suspender tentativas repetidas e resolver autenticação/acesso antes de retomar.
6. Retomar após correção, nova coleta manual completa, validação de dados e deploy confirmado. Reativar timer e observar próximo disparo. Registrar causa, versões restauradas e janela de desatualização.

O aceite de produção depende de execução comprovada desse fluxo. A existência deste plano conclui apenas a atividade de planejamento do PM.
