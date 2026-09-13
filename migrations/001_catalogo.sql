-- 001_catalogo.sql — Esquema inicial (plano SE-PRICES-002 §5)
-- Aplicado por db.migrar() de forma versionada. Migraciones aditivas.

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