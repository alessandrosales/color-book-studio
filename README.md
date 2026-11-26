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
uv sync --extra web --extra openai   # extra web uma vez
uv run cbg web                       # http://127.0.0.1:8000
```

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
- **Legenda / balão**: ative nos padrões, escolha estilo (`balão de fala` ou
  `legenda`), posição (`topo`/`base`), **fonte** (Sans/Serif/Mono), **peso**
  (normal/negrito) e **tamanho**. Cada página tem seu próprio campo de texto; o
  texto é desenhado no PNG final (faixa reservada), não gerado pelo modelo.
- **Estilo (referência)**: defina uma imagem ou use uma página já gerada com o
  botão *Usar como estilo*. Ela é enviada em toda geração como fonte visual para
  repetir traço, nível de detalhe e composição (sem copiar o assunto).
- Cada imagem gerada entra na lista com preview, status e download do PNG em 300 dpi.
  Clicar na miniatura abre um **lightbox** (navegação com ←/→, fecha com Esc/clique
  fora) sem baixar o arquivo.
- **Gerar pendentes** roda a fila; **Gerar PDF** compõe o interior no tamanho trim.
- **Backup**: o painel *Backup* exporta um `.zip` com sessões, imagens geradas,
  personagens, estilo e `settings.json`, e importa de volta com validação de
  caminho/versão. Sessões do arquivo substituem as de mesmo id; personagens são
  mesclados por id. Credenciais não são exportadas.
- Sessões são persistidas em `output/web/<id>/` (`session.json`, `pages/`, `raw/`,
  `previews/`, `book.pdf`), então recarregar a página retoma a mesma sessão.

## Usar seu plano ChatGPT Plus / Codex

O provider `codex` reutiliza o login por device code do Codex (mesma implementação
do projeto `videoai`): autentica no ChatGPT Plus e gera as imagens pela ferramenta
`image_generation`, sem chave de API por imagem.

```bash
uv run cbg auth codex     # abre o navegador e mostra um código
uv run cbg auth status    # conectado | desconectado
uv run cbg auth logout

uv run cbg generate --provider codex --pages 5
```

Na interface web há o bloco **Conta ChatGPT**: clique em *Conectar ChatGPT*, abra
a página exibida, informe o código e aguarde. Quando conectado e sem
`OPENAI_API_KEY`, o app troca o provedor para `codex` automaticamente (inclusive
em sessões antigas que estavam em `openai`). O provedor exibido no seletor é o
mesmo enviado em cada geração.

- Credenciais ficam em `~/.config/color-book-generator/openai_codex_auth.json`
  (modo `0600`), com refresh automático do token.
- Para reaproveitar um login já existente: `export CBG_CODEX_AUTH=/caminho/para/openai_codex_auth.json`.
- `cbg auth models` lista os slugs disponíveis na sua conta; o app consulta
  `/backend-api/codex/models`, escolhe o modelo de maior prioridade do seu plano
  e tenta os próximos modelos/parâmetros se um for recusado.
- Modelos ajustáveis: `CBG_CODEX_MODEL` (força um slug), `CBG_CODEX_IMAGE_MODEL`
  (ferramenta de imagem, padrão `gpt-image-2`), `CBG_CODEX_IMAGE_QUALITY`
  (padrão `medium`) e `CBG_CODEX_CLIENT_VERSION` (força a versão do catálogo).
- Se o backend recusar o tamanho pedido, a geração repete automaticamente em
  `1024x1024`/`medium`; erros do ChatGPT trazem o corpo da resposta na mensagem.

## Uso (CLI)

```bash
uv run cbg init               # cria config/book.yaml (se ainda não existir)
uv run cbg show               # mostra geometria da página (trim, bleed, px)
uv run cbg providers          # lista provedores disponíveis

uv run cbg generate --dry-run # só gera os prompts, sem custo
uv run cbg generate           # gera páginas + book.pdf em output/<run>/
uv run cbg generate --resume  # retoma um run interrompido
uv run cbg compose output/<run>/pages -o book.pdf
uv run cbg web --port 8000     # interface web
```

## Estrutura

```
config/book.yaml                      # definição do livro
src/color_book_generator/
  config.py                           # modelos de config + geometria de página
  prompts.py                          # construção dos prompts
  pipeline.py                         # orquestração ponta a ponta
  auth/codex.py                       # device-code OAuth ChatGPT Plus/Codex
  providers/                          # codex | openai | gemini | replicate | local
  processing/line_art.py              # binarização, limpeza, enquadramento
  layout/pdf.py                       # montagem do PDF (reportlab)
  web/                                # FastAPI + UI (prompts por página)
output/<run>/{raw,pages,book.pdf,manifest.json}
output/web/<session>/{pages,raw,previews,book.pdf,session.json}
```

## Como o pipeline funciona

1. `prompts.py` monta um prompt por página a partir do `theme` (ou de um arquivo
   com `--subjects`, um assunto por linha).
2. O provedor gera a imagem; com `candidates > 1` a página com melhor
   legibilidade (cobertura de tinta) é escolhida.
3. `processing/line_art.py` converte para P&B puro: cinza → limpeza → threshold
   (adaptive/otsu/fixed) → remoção de componentes pequenos → corte no conteúdo →
   centralização com margem de segurança na resolução final (`dpi`).
4. `layout/pdf.py` compõe um PDF com o tamanho de página do preset, página de
   título opcional e verso em branco opcional (`blank_verso`, útil para
   impressão frente única com canetinhas).

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
