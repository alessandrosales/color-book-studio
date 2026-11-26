# Discovery — padrões editoriais, de impressão e de narrativa por categoria

> Data: 2026-11-26 · Escopo: Color Book Studio (`src/color_book_generator`)
> Objetivo: fazer cada livro sair seguindo o padrão real da categoria (gráfica, formato, traço, narrativa) e criar uma etapa inicial em que o autor define o contexto da história antes de a IA gerar qualquer coisa.

Convenção usada no documento:
- **[fonte]**: dado tirado de pesquisa (links no fim).
- **[prática]**: convenção de mercado sem fonte única, ou conhecimento geral de pré-impressão. Precisa de validação com a gráfica ou com uma prova impressa.

---

## 1. Como o projeto está hoje (resumo do que importa)

| Área | Situação atual | Onde |
|---|---|---|
| Categorias | 6: `cozy`, `kids`, `manga`, `manhwa`, `kawaii`, `adult`, ~35 estilos | `presets.py` |
| Formatos | `letter_8.5x11`, `a4`, `square_8x8`, `square_8.5x8.5`, `6x9`, `8x10` | `config.py:34` |
| Margens | `margin_inner/outer/top/bottom_in` existem no config, **mas nenhum código de render usa esses campos**. A arte é centralizada com uma margem simétrica de 6% (`target_inner_margin_ratio`) | `config.py`, `processing/line_art.py:78` |
| Sangria | `bleed_in` padrão 0 no `book.yaml` (0.125 no modelo). Quando > 0, a página do PDF cresce, mas a arte continua centralizada | `layout/pdf.py:14` |
| Capa | Desenhada como **página 1 do miolo** no mesmo PDF | `layout/pdf.py:245` |
| Paginação | Sem validação de mínimo, de número par ou de múltiplo de 4/8/16. Só `blank_verso` força a ilustração a cair em página ímpar | `layout/pdf.py:259` |
| Cor/arquivo | PNG `L` (cinza 8 bits) binarizado, PDF via reportlab, sem PDF/X | `processing/line_art.py` |
| Traço | Controlado só por prompt ("thick outlines"…), sem medir a espessura real da linha | `prompts.py` |
| Narrativa | Dois modos: `narrative` e `collection`. A estrutura vem de `concept_prompt` + `_layout_script_guidance` (limite de palavras da legenda por categoria) | `story.py` |
| Contexto da história | Um textarea livre (`story-premise`) no passo 3, usado como `premise`/`premise_hint`. Nada estruturado: público, moral, tom e limites não existem como campos | `web/static/index.html:361`, `story.py` |

**Conclusão:** o pipeline de imagem está bem resolvido. O que falta é (a) uma camada de **perfil de impressão** que realmente aplique margens, gutter, sangria, paginação e capa separada, (b) **padrões por categoria** que sejam dados verificáveis (espessura de linha, densidade, quantidade de páginas, estrutura de história) em vez de só texto de prompt, e (c) um **briefing estruturado** antes do roteiro.

---

## 2. Diretrizes de impressão (o que a gráfica e a KDP pedem de fato)

### 2.1 Regras gerais de pré-impressão

| Item | Padrão | Status no projeto |
|---|---|---|
| Resolução | 300 dpi no tamanho final [fonte: KDP] | ✅ `dpi: 300` |
| Sangria | **3 mm** nas gráficas BR [fonte: Printi, Unicamp]; **0,125" (3,2 mm)** na KDP [fonte: KDP] | ⚠️ existe, mas a arte não chega até a sangria (o que só importa se algo encostar na borda) |
| Margem de segurança | 3 mm (Printi) a 4–5 mm (gráfica rápida); KDP: borda externa ≥ 0,25" sem sangria e ≥ 0,375" com sangria [fonte] | ⚠️ 6% simétrico, sem distinguir lado externo e lado da lombada |
| Gutter (margem interna) | KDP, 24–150 páginas: **0,375"**. Cresce com a espessura do livro: 151–300 páginas, provavelmente 0,5" (confirmar na calculadora); 501–700 páginas, 0,75" [fonte: KDP] | ❌ não aplicado. Em 6x9, 6% = 0,36" < 0,375" |
| Páginas | KDP: mínimo **24**, arredondado para número par [fonte]. Lombada canoa (grampo): múltiplo de **4**. Brochura offset: cadernos de 8/16 páginas, o que explica os picture books de 32 páginas [fonte + prática] | ❌ sem validação |
| Formato do PDF | PDF/X-1a é o preferido da KDP e da Printi [fonte]. Para miolo P&B, 1 bit ou escala de cinza basta, mas sem RGB nem "preto rico" | ⚠️ PDF comum. O PNG `L` já é seguro, falta exportar em PDF/X |
| Capa | **Arquivo separado** com contracapa + lombada + capa + sangria. Lombada KDP: páginas × 0,002252" (papel branco) ou × 0,002347" (miolo colorido). Texto na lombada só a partir de **79 páginas** [fonte: KDP] | ❌ a capa vai dentro do miolo |
| Fontes | Embutidas no PDF; texto ≥ 3 mm da linha de corte | ✅ reportlab embute TTF (confirmar quais fontes) |

### 2.2 Papel e acabamento por categoria

| Categoria | Miolo | Impressão | Capa | Observação |
|---|---|---|---|---|
| Colorir infantil | Offset 120 g a 180 g; 90–120 g é o mínimo para não atravessar [fonte] | **frente só** (verso branco) se o público usa canetinha | Cartão 250–300 g + laminação [prática] | KDP não deixa escolher gramatura (~55–60 lb), por isso o verso em branco vira obrigatório |
| Colorir adulto | Idem; marcador a álcool só presta em ≥ 170 g ou em folha avulsa [fonte] | frente só (consenso do nicho) [fonte] | Idem | Página destacável (picote) é um diferencial em gráfica própria |
| Picture book (historinha) | Couché 150 g colorido, ou offset 120 g se for para colorir | frente e verso, texto + ilustração em **spread** | Capa dura ou cartão | O texto conversa com a ilustração da mesma abertura |
| Mangá | Offset/pólen 56–90 g | frente e verso, brochura | Cartão + sobrecapa | Leitura R→L no original; no ocidente, opção de L→R [prática] |
| Gibi / revista | Offset 56–75 g ou couché 90 g | frente e verso, **canoa (grampo)** → múltiplo de 4 | Couché 150 g | Formatinho 13×21 cm ou 13,5×19; americano 17×26 [fonte] |
| Manhwa | Print adaptado do webtoon: painéis verticais remontados em página [fonte] | frente e verso | Cartão | Não existe tamanho padrão coreano; usar A5/B6 |

### 2.3 Formatos que faltam no catálogo

| Formato | Medida | Uso | Fonte |
|---|---|---|---|
| A5 | 148 × 210 mm (5,83 × 8,27") | mangá ISO, livro infantil BR, gráfica rápida | [fonte: manga tankobon] |
| B6 JP | 128 × 182 mm (5,04 × 7,17") | tankōbon japonês | [fonte] |
| Tokyopop | 5 × 7,5" (≈ 13 × 19 cm) | mangá nos EUA | [fonte] |
| Formatinho | 13,5 × 19 / 13 × 21 cm | gibi brasileiro | [fonte] |
| Americano | 17 × 26 cm | revista em quadrinhos, HQ | [fonte] |
| 21 × 28 cm | quase Letter, padrão BR de colorir/didático | gráficas BR | [prática] |
| 8,25 × 8,25" | quadrado KDP com sangria | picture book | [prática] |
| 10 × 8" paisagem | picture book horizontal | spreads largos | [prática] |

KDP define o 8,5 × 11 como o formato mais vendido de colorir, com o quadrado 8,5 × 8,5 em alta para o infantil [fonte].

---

## 3. Padrões visuais por categoria (traço, densidade, composição)

Números de espessura são **[prática]**: as fontes discordam entre si (3–5 pt num lugar, 1,5–2 pt contorno e 0,5–1 pt detalhe em outro) [fonte]. O caminho certo é medir a linha no PNG final (seção 6.3) e calibrar com uma prova impressa.

| Categoria / estilo | Contorno principal | Detalhe interno | Menor área fechada | Elementos por página | Fundo |
|---|---|---|---|---|---|
| Toddler (1–3 anos) | 2,5–4 mm | nenhum | ≥ 15 mm | 1–5 formas [fonte] | nenhum |
| Infantil 3–5 | 1,5–2,5 mm (1–2 mm aos 4 anos) [fonte] | 1 mm | ≥ 8 mm | 1 sujeito + 2–4 objetos | simples |
| Infantil 6–9 | 1–1,5 mm | 0,5–0,8 mm | ≥ 5 mm | cena completa | moderado |
| Bold & Easy / Cozy | 1,5–2,5 mm, monolinha | 1 mm | ≥ 6 mm, **sem ilhas minúsculas** [fonte] | 1 cena enquadrada | moderado |
| Kawaii | 1–1,5 mm, cantos redondos | 0,6 mm | ≥ 5 mm | grade 2×2 ou cluster | estrelas/corações |
| Mangá / Manhwa | 0,6–1 mm (variação de pena) | 0,3–0,5 mm | ≥ 3 mm | 3–6 quadros, 1 foco por quadro | mínimo; **sem retícula** |
| Adulto intricado | 0,5–0,8 mm | 0,25–0,4 mm | ≥ 2 mm | simetria/padrão denso | denso |

Linha abaixo de **0,25 mm (~0,7 pt)** some ou falha em impressão digital [prática]. A 300 dpi, 1 mm ≈ 11,8 px.

**Regras de composição que valem para todas as categorias:**
- Contornos fechados (sem vazamento ao colorir, já é o que `_RULES` pede).
- Nada essencial nos 3–6 mm da borda nem no gutter.
- Uma página por desenho no colorir (frente só). Exceção: picture book, que trabalha em spread.

---

## 4. Padrões de narrativa por categoria

### 4.1 Picture book / "Historinha para colorir" (`kids_storybook`)

| Item | Padrão |
|---|---|
| Extensão | **32 páginas** (24 e 48 também existem), em múltiplos de 8 por causa dos cadernos [fonte] |
| Espaço de história | ~28 páginas = **14 spreads** depois da página de rosto e do copyright [fonte] |
| Palavras | 500–700 é o alvo, ≤ 1000 no máximo; para 3–5 anos, **200–400** [fonte] |
| Por página | 15–30 palavras [fonte] |
| Estrutura | Apresentação (protagonista + desejo) → problema → 3 tentativas crescentes (regra de três) → clímax → resolução → fechamento/moral [prática] |
| Virada de página | Usar a virada para surpresa e suspense, principalmente perto do clímax [fonte] |
| Ilustração | Mostra o que o texto não diz (texto e imagem se complementam) [prática] |

**Proposta de mapeamento para 24 ilustrações:** 1 apresentação, 2–3 mundo/desejo, 4 incidente, 5–16 tentativas (3 blocos de 4), 17–19 crise, 20–21 clímax, 22–23 resolução, 24 "Fim" + moral.

### 4.2 Tipografia infantil

| Idade | Tamanho do corpo | Observação |
|---|---|---|
| 3–5 (pré-leitor) | ≥ 24 pt [fonte] | lido por um adulto |
| Leitor inicial | 14–18 pt, entrelinha 18–36 pt [fonte] | espaçamento generoso |
| Picture book | 14–24 pt [fonte] | depende da fonte |

Sem serifa, com **"a" e "g" de um andar** (como a criança aprende a escrever) [fonte]. Hoje `font_size_ratio=0.032` dá ≈ 25 pt em Letter e ≈ 21 pt em 6x9. Bom para o infantil, mas a escolha deveria vir da faixa etária, não do formato.

### 4.3 Mangá

- **Kishōtenketsu** (introdução → desenvolvimento → virada → conclusão) [fonte]. Na prática, os capítulos costumam ser introdução + dois desenvolvimentos [fonte].
- Shōnen: complicações e tensão crescem no desenvolvimento [fonte]. Shōjo: foco em emoção interna e relação, com quadros de close e fundo floral [prática, sem fonte].
- Capítulo semanal ≈ 18–20 páginas, ou seja, um livro de 24 desenhos cabe em um capítulo [prática].
- Ritmo: quadro grande = tempo desacelerado; a calha (gutter) é tempo narrativo [fonte].
- Gancho na última página. Balões curtos (o limite de 8 palavras já existe).

### 4.4 Manhwa

- Herdado do webtoon: ritmo vertical, revelação por rolagem. Na versão impressa, os painéis verticais são remontados em página [fonte].
- Por subgênero: romance fantasy (reencarnação, intriga palaciana), murim (treino → duelo), caçadores (sistema e níveis) [prática].
- Uma "batida" por página com cliffhanger no último quadro.

### 4.5 Coleções (Cozy, Kawaii, Adulto)

Sem arco narrativo, mas com **unidade**: mesmo universo, mesmo personagem-âncora (cozy), progressão de dificuldade (adulto: do simples ao intricado) ou variação de tema (estações, cômodos, comidas). A recomendação é 40–60 desenhos para adulto e 30–50 para infantil [fonte].

---

## 5. Gaps priorizados

| # | Gap | Impacto | Esforço |
|---|---|---|---|
| G1 | Capa dentro do miolo; falta o arquivo de capa com lombada e contracapa | Bloqueia KDP e gráfica | M |
| G2 | Margens inner/outer e gutter não aplicados (sem espelhamento par/ímpar) | Arte "comida" pela lombada | M |
| G3 | Sem validação de paginação (mín. 24, par, múltiplo de 4/8/16) | Rejeição no upload ou páginas brancas inesperadas | P |
| G4 | Formatos faltando (A5, B6, 5×7,5, formatinho, americano, 21×28) | Mangá e gibi fora do padrão | P |
| G5 | Espessura de linha e menor área não são medidas | Desenho adulto ruim para criança e vice-versa | M |
| G6 | Estrutura narrativa só existe como texto livre no `concept_prompt` | Histórias sem arco consistente | M |
| G7 | Contexto da história é um textarea livre e aparece tarde (passo 3) | A IA inventa público, tom e moral | M |
| G8 | Sem exportação PDF/X-1a | Algumas gráficas recusam | P–M |
| G9 | Tamanho da fonte não leva em conta a faixa etária | Legibilidade | P |

---

## 6. Proposta técnica

### 6.1 `PrintProfile` (novo, em `config.py` ou `print_profile.py`)

```python
class PrintProfile(BaseModel):
    id: str                    # "kdp_paperback" | "grafica_brochura" | "grafica_canoa" | "home_print"
    bleed_mm: float            # 3.2 KDP, 3.0 gráfica BR
    safe_mm: float             # 3–6
    gutter_rule: list[tuple[int, int, float]]   # (min_pag, max_pag, gutter_in) — tabela KDP
    page_multiple: int         # 2 KDP, 4 canoa, 8/16 offset
    min_pages: int             # 24 KDP
    single_sided_default: bool
    pdf_standard: Literal["none", "pdfx1a"]
    spine_per_page_in: float   # 0.002252 branco / 0.002347 cor
    spine_text_min_pages: int  # 79
```

- O render passa a **posicionar a arte dentro da área segura espelhada**: página ímpar (direita) com gutter à esquerda, página par com gutter à direita. `line_art.fit` recebe uma caixa (x, y, w, h) em vez de uma margem simétrica.
- `compose_pdf` calcula páginas e completa com brancas até o múltiplo. Avisa se o total ficou abaixo do mínimo.
- Novo `compose_cover_pdf()` gera contracapa + lombada + capa + sangria (sinopse e logo na contracapa, título na lombada se ≥ 79 páginas).

### 6.2 `CategoryStandard` (estende o `StylePreset`)

```python
class CategoryStandard(BaseModel):
    age_band: Literal["0-3", "3-5", "6-9", "10-14", "teen", "adult"] | None
    line_main_mm: tuple[float, float]       # faixa aceitável do contorno
    line_detail_mm: tuple[float, float]
    min_region_mm: float                    # menor área fechada colorível
    max_elements: int | None
    recommended_sizes: list[PageSizeName]
    recommended_pages: tuple[int, int]
    story_structure: Literal["picture_book", "kishotenketsu", "webtoon_beats", "collection", "progressive_collection"]
    words_per_page: tuple[int, int]
    font_pt: tuple[float, float]
    reading_direction: Literal["ltr", "rtl"] = "ltr"
```

Os valores das seções 3 e 4 entram aqui. O prompt continua existindo, mas passa a ser **gerado a partir desses números** (ex.: "contorno de ~2 mm"), e o pós-processamento **confere** o resultado.

### 6.3 Controle de qualidade da linha (`processing/qa.py`)

- Espessura do traço: `distance_transform` sobre o esqueleto → mediana × 2 = largura em px → mm (`px / dpi × 25,4`).
- Menor região: `connectedComponents` no fundo branco → menor área em mm².
- Cobertura de tinta (já existe para escolher candidatos).
- Resultado: um selo por página ("ok", "linha fina demais para 3–5 anos") e regeneração ou dilatação automática quando estiver dentro de uma tolerância (`cv2.dilate` 1–2 px).

### 6.4 Estruturas de história como templates de batidas

`story.py` recebe um `beat_template(structure, pages)` que distribui as batidas pelas N páginas (o mapa da seção 4.1 para picture book, ki/shō/ten/ketsu em proporção 25/35/25/15 para mangá, etc.). Cada `ScriptedPage` ganha `beat: str`, e o prompt pede que a página cumpra aquela batida. Depois da geração, o modelo valida: número de palavras por página, presença do clímax e fechamento da moral.

---

## 7. Etapa inicial: definição do contexto da história ("Briefing")

### 7.1 Onde entra

Novo **Passo 0 — Briefing**, antes de "Estilo e formato". Hoje o contexto é um campo livre no passo 3, quando estilo e personagens já foram decididos. O briefing vem primeiro porque **ele decide a categoria, o formato e a estrutura**, e não o contrário. O preset passa a ser sugerido a partir do briefing (o usuário ainda pode trocar).

### 7.2 Campos (salvos em `session.story_brief`)

**Comuns a todas as categorias**

| Campo | Tipo | Exemplo |
|---|---|---|
| Para quem é | faixa etária + quem lê (criança sozinha, adulto lendo junto, adulto) | 3–5, lido por adulto |
| Objetivo do livro | entreter / ensinar valor / educativo (letras, números) / relaxar / presente personalizado | ensinar valor |
| Ideia central (logline) | 1–2 frases | "Uma raposinha com medo do escuro descobre que as estrelas também são luz" |
| Mensagem / moral | texto curto | coragem é agir mesmo com medo |
| Tom | doce, engraçado, aventureiro, épico, melancólico, calmo | doce e engraçado |
| Mundo / cenário | lugar + época + regras | floresta encantada, sem humanos |
| Protagonista | nome, o que quer, do que tem medo, traço marcante | Lia, quer ver o céu à noite, medo do escuro |
| Elenco de apoio | 0–3 | coruja sábia |
| Conflito / obstáculo | texto | o escuro da floresta |
| Final desejado | feliz / aberto / surpresa / circular | feliz, volta para casa |
| Limites | evitar: medo intenso, violência, morte, temas religiosos, marcas… | sem vilões assustadores |
| Idioma e regionalismo | pt-BR, nomes brasileiros, referências culturais | pt-BR, fauna brasileira |
| Referências | livros/estilos que o autor gosta (texto livre, nunca para copiar) | — |

**Perguntas extras por categoria**

- **Infantil/historinha**: nível de leitura (só ilustração / frases curtas / parágrafo), repetição/refrão desejado, lição explícita ou implícita.
- **Mangá**: demografia (shōnen, shōjo, seinen, josei), gancho final, direção de leitura (L→R ou R→L).
- **Manhwa**: subgênero (romance fantasy, murim, caçador, histórico), "sistema" do mundo.
- **Cozy/Kawaii/Adulto (coleção)**: fio condutor (estações, cômodos, receitas), personagem-âncora (sim/não), progressão de dificuldade.

### 7.3 Fluxo

1. O usuário preenche o mínimo (público + ideia central). O resto é opcional e tem um botão **"Sugerir com IA"** por campo.
2. A IA devolve **3 propostas de premissa** (logline + tom + moral), e o usuário escolhe ou mistura.
3. Com a premissa escolhida, a IA gera a **Bíblia da história**: resumo, arco em batidas (com base no template da categoria, seção 6.4), ficha dos personagens e regras do mundo.
4. O usuário edita e **trava** a bíblia. A partir daí ela entra como contexto fixo em tudo: roteiro de páginas, "Nova página" no meio, capa, sinopse da contracapa e folha de rosto.
5. Se o usuário mudar a bíblia depois, o app mostra quais páginas ficaram desatualizadas, sem regenerar nada sozinho.

### 7.4 Mudanças no código

- `web/store.py`: `StoryBrief` + `StoryBible` no estado da sessão, com migração de sessões antigas (`story_premise` vira `brief.logline`).
- `story.py`: `propose_premises(brief)`, `build_story_bible(brief, preset)`. `generate_story_plan` e `generate_full_book_blueprint` passam a receber a bíblia em vez de `premise` solta.
- `web/routers/studio.py`: endpoints `/brief`, `/brief/suggest-field`, `/brief/premises`, `/bible`.
- UI: novo passo 0 no stepper (`index.html:44`), formulário em blocos com campos condicionais por categoria.
- `archive.py`: incluir brief e bíblia no backup e na validação de import.

---

## 8. Roadmap sugerido

| Fase | Entrega | Gaps |
|---|---|---|
| 1 | Briefing + Bíblia da história (passo 0) | G7 |
| 2 | Templates de batidas por categoria + validação de palavras e fonte por idade | G6, G9 |
| 3 | `PrintProfile`: margens espelhadas, gutter por página, paginação, novos formatos | G2, G3, G4 |
| 4 | Capa separada com lombada + PDF/X-1a | G1, G8 |
| 5 | QA de linha (espessura e menor região) com correção automática | G5 |

A fase 1 vem primeiro porque é o que você pediu e porque melhora tudo o que vem depois. A fase 3/4 é o que destrava enviar o arquivo para a KDP ou para uma gráfica sem retrabalho.

### A validar antes de implementar
- Tabela completa de gutter da KDP (151–300 e 301–500 páginas) na calculadora oficial.
- Especificação da gráfica que você pretende usar (sangria 3 ou 5 mm, PDF/X-1a ou X-4, gramaturas).
- Espessuras de linha: imprimir uma página de teste por categoria e calibrar as faixas da seção 3.

---

## Fontes

- KDP — margens, sangria, páginas mínimas, lombada: https://kdp.amazon.com/en_US/help/topic/GVBQ3CMEQW3W2VL6 · https://kdp.amazon.com/help/topic/G201857950 · https://kdp.amazon.com/pt_BR/help/topic/G201953020
- Vappingo — formatação KDP (gutter 151–300): https://www.vappingo.com/word-blog/kdp-paperback-formatting/
- Printi — guia de impressão (sangria/segurança 3 mm, PDF/X-1a): https://www.printi.com.br/assets/final/pdf/Guia-de-impressao.pdf
- Unicamp DGA — procedimento para impressão: https://www.dga.unicamp.br/Conteudos/Documentos/Procedimento_para_Impressao.pdf
- Cursa — sangria e margens em gráfica rápida: https://cursa.app/pt/pagina/dimensoes-sangria-e-margens-de-seguranca-em-grafica-rapida
- Picture books (32 páginas, spreads, palavras, virada de página): https://debbieohi.substack.com/p/picture-books-101-part-3-understanding · https://www.darcypattison.com/?p=459 · https://taralazar.com/2009/02/22/picture-book-construction-know-your-layout/ · https://madegooddesigns.com/picture-book-design/ · https://medium.com/@michaelhingston/picture-books-101-from-alison-hughes-9b9eec235b19
- Mangá — formatos e produção: https://llamagen.ai/guides/manga-page-size · https://tips.clip-studio.com/en-us/articles/8264 · https://en.wikipedia.org/wiki/Aizoban · https://printninja.com/printing-products/manga-printing/
- Kishōtenketsu: https://en.wikipedia.org/wiki/Kish%C5%8Dtenketsu · https://robynpaterson.com/kishotenketsu-in-manga-revisited
- Manhwa/webtoon em print: https://www.schoollibraryjournal.com/story/Designed-for-Phones-Webtoons-Find-a-Home-in-Print-comics-manga-graphic-novels · https://www.publishersweekly.com/pw/print/20241111/96454-webcomics-fans-turn-book-buyers.html
- Gibi BR (formatinho/americano): https://www.omelete.com.br/quadrinhos/o-formatinho-esta-morto-longa-vida-ao-formatinho
- Papel e marcadores: https://www.ggimage.com/zh-en/Blog/What-Is-the-Best-Paper-Weight-For-Printing-Adult-Coloring-Pages.html · https://www.crayola.com/faqs/what-paper-works-best-with-alcohol-markers-faq
- Colorir por idade: https://www.kdpeasy.com/blog/coloring-book-age-groups · https://hsnsudbury.ca/Portals/1/Services/Newborn_and_Childrens_Care/CTC%20Documents/PRESCHOOL/Early%20Colouring%20Skills-NEO%20Kids_Daycare%20Information%20Handout.pdf · https://www.scholastic.com/parents/books-and-reading/raise-a-reader-blog/developmental-milestones-coloring-in-the-lines.print.html
- KDP colorir (frente só, quantidade, bold & easy): https://kindlepreneur.com/how-to-publish-a-coloring-book/ · https://www.kdpeasy.com/guides/coloring-book-trends-2026 · https://kdpbuilder.com/blog/coloring-book-publishing-line-art-print-quality
- Tipografia infantil: https://type.today/en/journal/childrens_book_typography · https://booksforkeeps.co.uk/article/typography-in-childrens-books/ · https://www.whatfontis.com/blog/childrens-book-typography-guide/
