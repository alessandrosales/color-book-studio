# Color Book Generator

Pipeline em Python para gerar livros de colorir prontos para impressão:
prompts → imagens → line art P&B limpo → PDF com tamanho de página e margens corretas.

## Requisitos

- Python 3.12 (gerenciado por `uv`)
- Uma chave de API do provedor de imagens escolhido (OpenAI, Gemini ou Replicate)

## Setup

```bash
uv sync --extra openai        # ou --extra gemini / --extra replicate / --extra local
cp .env.example .env          # preencha a chave do provedor
```

## Interface web

```bash
uv run cbg              # http://127.0.0.1:8000 (ou --port 8080 --reload)
```

A raiz (`/`) é a **biblioteca de livros**: cada livro guarda contexto, páginas,
personagens, estilo e padrões próprios. Abrir um livro vai para `/books/<id>`.
O seletor **Sessão** no topo alterna entre livros; “Novo livro” cria outro sem
afetar o atual.

- **Padrões da página**: prompt de contexto aplicado a todas as imagens (estilo,
  traço, fundo, composição). Editável; salvo na sessão.
- **Prompt por página**: cada página tem seu próprio prompt (assunto/composição),
  editável antes de gerar ou regenerar.
- Título, quantidade de páginas, prompt de contexto, provedor e modelo são salvos
  (`output/web/settings.json` + `session.json`). Alterar **Páginas** ajusta a
  sessão atual: adiciona páginas pendentes ou remove apenas as que ainda não têm
  imagem (reduzir sobre páginas já geradas retorna erro).
- **Personagens**: envie nome + imagem de referência no painel *Personagens* e
  — ao adicionar, o personagem é marcado automaticamente em todas as páginas da
  sessão (desmarque o chip onde não quiser). As imagens são passadas ao modelo como
  referência para manter rosto, cabelo, proporções e roupa; o prompt pede o
  redesenho no estilo line-art. Suporte a referências: provedor `codex`
  (imagens `input_image`) e `openai` com `gpt-image-1` (images.edit); os demais
  provedores retornam erro se houver referência marcada.
- **Referência de texto (fonte)**: no passo 1, envie uma imagem com uma amostra do
  texto (ou use uma página já gerada com *Usar fonte desta página*). A fonte
  detectada — família (Sans/Serif/Mono) e peso (normal/negrito) — é aplicada nas
  legendas e balões das páginas geradas. O mesmo card concentra todas as definições
  de texto: ativar legenda/balão, estilo (`balão de fala` ou `legenda`), posição
  (`topo`/`base`), fonte, peso e tamanho. Cada página tem seu próprio campo de
  texto; o texto é desenhado no PNG final (faixa reservada), não gerado pelo modelo.
- Cada imagem gerada entra na lista com preview, status e download do PNG em 300 dpi.
  Clicar na miniatura abre um **lightbox** (navegação com ←/→, fecha com Esc/clique
  fora) sem baixar o arquivo.
- **Gerar pendentes** (passo 4) roda a fila das páginas ilustradas.
- **Capa e finalização (passo 5)**: a capa é fixa e tem direção de arte própria;
  o contexto do livro, o roteiro completo e as referências servem de memória
  conceitual. Pode ser gerada colorida (padrão) ou em line art, e o título é
  aplicado sobre uma faixa na base. Também permite adicionar páginas de texto
  iniciais (depois da capa) e finais (depois dos desenhos), cada uma com preview,
  edição, checkbox para entrar no PDF e um **prompt da página** que define como o
  texto e a(s) imagem(ns) são organizados (alinhamento, posição/tamanho da imagem,
  borda etc.) antes de gerar o preview. Os toggles de KDP (verso em branco, folha
  de rosto, “este livro pertence a”, teste de cores) e a composição final do PDF
  ficam nesse passo. **Autor e sinopse** também ficam aqui; quando a folha de rosto
  está ativada, ambos são impressos nela.
- **Gerar PDF** compõe capa + páginas iniciais + páginas ilustradas + páginas
  finais no tamanho trim.
- **Backup**: o painel *Backup* exporta um `.zip` com sessões (incluindo
  `raw/`, páginas, `previews/`, `cover/`, `extras/`, `style/`, `characters/`,
  `book.pdf`), personagens globais, estilo global, `settings.json`, presets
  personalizados e `config/book.yaml`. A importação valida sessões, capas,
  páginas extras (imagem final e fundo visual), personagens, estilos e
  configurações antes de escrever (sem estado parcial), aceita zips antigos/sem
  manifest e mostra uma tela de progresso.
  Se o mesmo livro (id) já existir, a importação cria uma **nova cópia** com outro
  id em vez de sobrescrever; personagens são mesclados por id; o
  `book.yaml` restaurado mantém geometria/margens/DPI. Credenciais não entram.
- Sessões são persistidas em `output/web/<id>/` (`session.json`, `pages/`, `raw/`,
  `previews/`, `book.pdf`), então recarregar a página retoma a mesma sessão.

## Usar seu plano ChatGPT Plus / Codex

O provider `codex` reutiliza o login por device code do Codex (mesma implementação
do projeto `videoai`): autentica no ChatGPT Plus e gera as imagens pela ferramenta
`image_generation`, sem chave de API por imagem.

Na interface web há o bloco **Conta ChatGPT**: clique em *Conectar ChatGPT*, abra
a página exibida, informe o código e aguarde. Quando conectado e sem
`OPENAI_API_KEY`, o app troca o provedor para `codex` automaticamente (inclusive
em sessões antigas que estavam em `openai`). O provedor exibido no seletor é o
mesmo enviado em cada geração.

- Credenciais ficam em `~/.config/color-book-generator/openai_codex_auth.json`
  (modo `0600`), com refresh automático do token.
- Para reaproveitar um login já existente: `export CBG_CODEX_AUTH=/caminho/para/openai_codex_auth.json`.
- O app consulta `/backend-api/codex/models`, escolhe o modelo de maior prioridade
  do seu plano e tenta os próximos modelos/parâmetros se um for recusado.
- Modelos ajustáveis: `CBG_CODEX_MODEL` (força um slug), `CBG_CODEX_IMAGE_MODEL`
  (ferramenta de imagem, padrão `gpt-image-2`), `CBG_CODEX_IMAGE_QUALITY`
  (padrão `medium`) e `CBG_CODEX_CLIENT_VERSION` (força a versão do catálogo).
- Se o backend recusar o tamanho pedido, a geração repete automaticamente em
  `1024x1024`/`medium`; erros do ChatGPT trazem o corpo da resposta na mensagem.

## Estrutura

```
config/book.yaml                      # padrões do livro
src/color_book_generator/
  config.py                           # modelos de config + geometria de página
  prompts.py                          # construção dos prompts
  fsutil.py                           # gravação atômica em disco
  auth/codex.py                       # device-code OAuth ChatGPT Plus/Codex
  providers/                          # codex | openai | gemini | replicate | local
  processing/line_art.py              # binarização, limpeza, enquadramento
  layout/pdf.py                       # montagem do PDF (reportlab)
  web/                                # FastAPI + UI + persistência
output/web/<session>/{pages,raw,previews,book.pdf,session.json}
output/web/{settings.json,characters/,style/}
```

## Como o pipeline funciona

Cada livro tem uma **identidade própria** (`identity.py` + `web/book_pipeline.py`),
e nada vem de outros livros:

- **Padrão por categoria** (`static/page-layouts/<categoria>/`): cada categoria tem
  sua própria pasta de páginas-modelo, sem subpastas e sem empréstimo entre
  categorias. A primeira imagem da pasta é o padrão de formato da categoria.
- **Âncora de estilo** (`<livro>/style/anchor.png`): criada antes da primeira geração
  do livro (página, folha, capa ou piloto automático), sem personagens, a partir do
  padrão da categoria + prompt de estilo + mundo do livro. Só a âncora vê a página-modelo;
  folhas e páginas recebem apenas a âncora, então nada da página-modelo vaza.
  A amostra de fonte (`style/reference.png`) serve só para detectar a fonte.
- **Elenco fixo**: com a bíblia travada (ou personagens já criados), o roteiro usa
  exatamente esse elenco, com os mesmos ids; os roteiros citam personagens só pelo
  nome, sem redescrever a aparência, e não inventam personagens novos.
- **Design + character sheet**: cada personagem ganha um design detalhado
  (proporções, rosto, cabelo, roupa fixa, acessório, regras "sempre/nunca") e um
  sheet com turnaround de 4 vistas, 4 expressões e o acessório. O sheet só é refeito
  quando a descrição muda.
- **Páginas**: toda página (manual ou piloto automático) usa a mesma receita:
  âncora + sheets dos personagens da cena + design deles + regras do mundo.

1. `prompts.py` e `story.py` montam os prompts de cada página.
2. O provedor gera a imagem; com `candidates > 1` a página com melhor
   legibilidade (cobertura de tinta) é escolhida.
3. `processing/line_art.py` converte para P&B puro: cinza → limpeza → threshold
   (adaptive/otsu/fixed) → remoção de componentes pequenos → corte no conteúdo →
   centralização com margem de segurança na resolução final (`dpi`).
4. `layout/pdf.py` compõe um PDF com o tamanho de página do preset, capa,
   páginas de texto iniciais/finais, página de título opcional e verso em branco
   opcional (`blank_verso`, útil para impressão frente única com canetinhas).

Cada página final é um PNG em `300 dpi` (configurável) no tamanho trim do livro —
pronto para upload em KDP/IngramSpark como interior.

## Presets de página

`letter_8.5x11`, `a4`, `square_8x8`, `6x9`, `8x10`.

## Desenvolvimento

```bash
uv sync --extra dev --extra openai
uv run pytest
uv run ruff check .
uv run mypy src
```
