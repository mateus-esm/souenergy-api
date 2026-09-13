# SE-PRICES-003 — Correção da descoberta de produtos

## Causa raiz

A inspeção autenticada no VPS em 13/09/2026 confirmou que os oito primeiros
elementos `.product-item` pertenciam ao minicarrinho oculto, fora de `main`.
Depois deles, as primeiras páginas de Solplanet e Hoymiles tinham 12 cards
visíveis, dentro de `.products`. A espera padrão do Playwright exige
visibilidade do primeiro elemento, portanto expirava apesar do catálogo presente.

Em Hoymiles, o contador visível era “Itens 1-12 de 20”. O seletor anterior
`.toolbar .toolbar-amount span:has-text("0")` também casa com “20”.
Após o timeout causado pelo minicarrinho, esse contador fazia a página ser
classificada incorretamente como vazia. A busca genérica de texto no corpo
e os marcadores ocultos também permitiam falsos positivos.

## Correção

- Seletor compartilhado `main .products .product-item` na espera, extração,
  estabilização do scroll e reconhecimento de categorias durante os detalhes.
- Espera com `state="attached"`: não depende da visibilidade do primeiro card.
- Cards do catálogo têm precedência sobre qualquer marcador de vazio.
- Marcadores de vazio precisam estar visíveis dentro de `main`; o contador
  exige texto completo com quantidade exatamente zero. Removida a busca no corpo.
- Log por listagem com URL, quantidade de produtos e subcategorias.

## Testes

Comando: `python3 -m pytest tests/ -q --no-header`.

Resultado: **104 passed**, sem testes ignorados, com um aviso preexistente de
depreciação de Starlette/httpx. Os 94 testes anteriores foram preservados.
Nove casos novos usam HTML em Chromium real e cobrem minicarrinho oculto,
primeiro card do grid oculto, contadores 10/20/0, mensagem oculta, vazio real,
texto incidental, contradição entre cards e mensagem de vazio e chegada
tardia do marcador autenticado. Um teste adicional rejeita links institucionais
sem excluir categorias Magento ou categorias novas.
Para executar esses casos, instalar o navegador com
`python3 -m playwright install chromium`.

## Validação real no VPS

Foram copiados `scraper.py` e `catalogo.py` do worktree para
`/opt/souenergy-api/current/`, conforme autorização de validação.
A execução utiliza o usuário de serviço `souenergy`, o ambiente de
`/etc/souenergy/souenergy.env` e `venv/bin/python -m coleta --no-promote`.
Os canais de alerta foram desabilitados somente no ambiente desse processo
para não enviar notificações externas durante a homologação.
Nenhuma credencial foi registrada no relatório.

A primeira execução, `a7afa7a200974da8a43d0f61a33986a4`, terminou com código 2
por login não confirmado, antes de visitar listagens. A repetição confirmou
login sem alteração no código de autenticação.

## Ajustes necessários durante a coleta completa

A execução `ae31949a15e84a4ba99b96d4bb970a91` confirmou login e coletou
as primeiras páginas das duas entradas, mas abortou antes dos detalhes.
A descoberta genérica do menu incluía páginas institucionais: o endereço
`/catalog/category/view/s/quem-somos/id/549/` redireciona para
`https://somos.souenergy.com.br/`, cuja página não contém a sessão da loja.
Isso foi reproduzido em inspeção autenticada, inclusive após aguardar o marcador.

A classificação de categorias agora exclui os segmentos institucionais
observados (quem-somos, calculadora, formas-de-pagamento, políticas e sobre).
A descoberta das demais categorias, marcas e paginações foi preservada.
Também foi adicionada espera limitada de 15 segundos pelo marcador dinâmico
após login/navegação; sua ausência continua abortando a execução, como
verificado pelo teste existente de perda de sessão.

O vazio em `hibridos/solplanet-hibrido.html` foi conferido separadamente:
zero cards no catálogo e mensagem visível
“Não encontramos produtos correspondentes a seleção.”
Esse caso não é a entrada `solplanet.html`, que tem produtos.

