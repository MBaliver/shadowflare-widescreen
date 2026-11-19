# ShadowFlare Widescreen / Ultrawide Patch

> 🇺🇸 [Read in English (README.md)](README.md)

Patcher binário em Python (sem dependências externas) para **ShadowFlare: Episode One** (2001, Denyusha / Emurasoft) que expande o viewport isométrico nativo de `640×480` para resoluções **16:9** e **21:9 ultrawide** — aumentando o campo de visão ("zoom out") enquanto mantém a HUD original de `640×480` centralizada embaixo e totalmente funcional.

> **Aviso:** Este repositório **não contém arquivos proprietários do jogo nem binários de terceiros**. Você precisa da sua própria instalação do ShadowFlare Episode One.

---

## O que o patch faz

- **Viewport Widescreen / Ultrawide real:** Renderiza o mapa isométrico e personagens em toda a tela (ex.: `1280×540` para escala inteira exata de `2×` em monitores `2560×1080`, ou `960×540` para `1920×1080`).
- **HUD centralizada ancorada embaixo:** Todos os menus, painéis e a barra inferior permanecem centralizados (`+ox` na horizontal, `+oy` na vertical), sem esticar ou distorcer os sprites.
- **Cliques laterais funcionando com menus abertos:** Clicar nas faixas extras à esquerda do menu de Status/Magia/Loja ou à direita do menu de Equipamentos/Inventário vai direto para o mapa, sem ser engolido pelas checagens antigas de `640×480`.
- **Tela inteira redesenhada com painéis abertos:** Abrir painéis à esquerda (Status, Magia, Loja, Baú) ou à direita (Equipamentos) desloca a câmera para fora do painel mantendo a largura inteira renderizada todo frame (sem faixas congeladas nem rastro do cursor).
- **Hover em toda a tela ultrawide:** Nomes/HP de inimigos, NPCs e itens no chão (`FUN_00416bb0`) funcionam em toda a tela, inclusive ao lado dos menus abertos e nas faixas inferiores laterais.
- **Screenshots (`PrintScreen`) em ultrawide:** Os arquivos `ScreenShot_00000000.Bmp` passam a ser salvos na resolução completa em vez de `640×480`.
- **Rodinha do mouse para trocar magias (`WM_MOUSEWHEEL`):** Gire o scroll para cima/baixo para alternar rapidamente entre os atalhos de magia configurados em `F1–F8` (pode ser desativado com `--no-wheel`).
- **Toggle de itens no chão estilo Diablo 2 (`ALT`):** Aperte `ALT` uma vez para exibir o nome de todos os itens visíveis no chão com cores de raridade (branco = normal, azul = mágico, dourado = raro, laranja = único); aperte `ALT` novamente para ocultar (desative com `--no-item-labels`).
- **Remapeamento de teclas (`--remap`):** Troque qualquer atalho da tabela de teclas do jogo (`I`, `S`, `M`, `R`, `X`, `H`, `TAB`, `SPACE`, `1–8`, etc.) sem afetar a digitação de texto/nomes (ex.: `--remap I=E S=C 1=Q 2=W`).
- **Ajuste opcional de FPS / Tick (`--fps`):** Altera o limite de `33 ms` (`~30 FPS`) em `RKC_DBFCONTROL.dll`. *(Obs.: o jogo não usa delta-time, então valores acima de 30 FPS aceleram a velocidade do jogo proporcionalmente — ex.: `--fps 40` funciona como um turbo leve.)*
- **Seguro e reversível:** Valida o SHA-256 dos arquivos originais, cria backups `.orig.bak` automaticamente e suporta `--restore`.

---

## Presets de Resolução Recomendados

Como o ShadowFlare usa sprites 2D pré-renderizados, aumentar a resolução interna funciona como **afastar a câmera (zoom out)**. Resoluções com escala inteira mantêm os pixels super nítidos usando [cnc-ddraw](https://github.com/FunkyFr3sh/cnc-ddraw):

| Preset | Resolução Interna | Monitor Alvo | Escala | Visual |
| :--- | :---: | :---: | :---: | :--- |
| `--preset 21:9-2x` *(padrão)* | `1280×540` | `2560×1080` | `2×` inteiro exato | Pixels nítidos, 2× mais mapa na horizontal |
| `--preset 16:9-2x` | `960×540` | `1920×1080` | `2×` inteiro exato | Pixels nítidos, 1,5× mais mapa na horizontal |
| `--preset 16:9-720p` | `1280×720` | `2560×1440` / `1920×1080` | `2×` (1440p) / `1,5×` (1080p) | Câmera mais afastada |
| `--preset 16:9-1080p` | `1920×1080` | `1920×1080` | `1×` nativo | Câmera bem afastada |
| `--preset 21:9-native` | `2560×1080` | `2560×1080` | `1×` nativo | Campo de visão ultrawide máximo |

Você também pode informar qualquer resolução par (`width >= 640`, `height >= 480`) via `--width` e `--height`.

---

## Como Usar

### 1. Aplicar o Patch

Requer apenas Python 3 (não precisa instalar nenhum pacote):

```bash
python3 patch_shadowflare.py "/caminho/para/ShadowFlare" --preset 21:9-2x
```

Ou com resolução personalizada:

```bash
python3 patch_shadowflare.py "/caminho/para/ShadowFlare" --width 1280 --height 540
```

Para restaurar os arquivos originais a qualquer momento:

```bash
python3 patch_shadowflare.py "/caminho/para/ShadowFlare" --restore
```

### 2. Escala em Tela Cheia com `cnc-ddraw`

1. Baixe a versão mais recente do [cnc-ddraw](https://github.com/FunkyFr3sh/cnc-ddraw/releases).
2. Extraia o `ddraw.dll` na pasta de instalação do `ShadowFlare`.
3. Copie o arquivo [`ddraw.ini`](ddraw.ini) deste repositório para a mesma pasta.

### 3. Notas para Linux / Steam Proton (XWayland)

Testado na Steam com `GE-Proton11-12` em monitor `2560×1080`:

1. Nas **Opções de Inicialização** do jogo na Steam, adicione para que o Proton carregue o `ddraw.dll` local:
   ```
   WINEDLLOVERRIDES="ddraw=n,b" %command%
   ```
2. Mantenha `devmode=true` no [`ddraw.ini`](ddraw.ini). Em tela cheia no **XWayland**, a trava de cursor (`ClipCursor`) padrão do `cnc-ddraw` pode travar o jogo ao clicar; `devmode=true` desliga essa trava mantendo o modo tela cheia sem bordas com escala inteira `2×` funcionando normalmente.

---

## Como Funciona (Detalhes Técnicos)

A engine do ShadowFlare é dividida em DLLs C++ com os nomes dos símbolos intactos (`RKC_DBFCONTROL.dll`, `RKC_UPDIB.dll`, `RKC_RPGSCRN.dll`, `RKC_DIB.dll`, `RKC_WINDOW.dll`). Em vez de reescrever centenas de chamadas de desenho da HUD, o patch cria um **espaço de coordenadas deslocado** (`ox = (W - 640) / 2`, `oy = H - 480`):

1. **Deslocamento Global de Renderização (`RKC_UPDIB.dll`):**
   - Redireciona as 3 chamadas de `RKC_UPDIB_VSPACKET::Render` (`0x100030ab`, `0x100030d2`, `0x100030ff`) para um code cave em `0x100080f0`.
   - Sempre que o DIB de destino tem largura `>= W` (backbuffer principal), o wrapper desloca temporariamente as coordenadas do pacote (`+ox, +oy`), primitivas (flag `0xe00`), retângulo de recorte interno (flag `0x20`) e o `tagRECT*` passado, chama o renderizador original e restaura os valores.

2. **Espaço de Coordenadas do Mouse (`ShadowFlare.exe`):**
   - Intercepta `ScreenToClient` em `0x4087ce` (desenho do cursor) e `0x456311` (leitura de input) via code cave em `0x4725b0` subtraindo `(ox, oy)`.
   - Toda a UI continua trabalhando em `[0..639, 0..479]`, enquanto a tela inteira vai de `[-ox .. W-1-ox, -oy .. 479]`.
   - Atualiza os limites de `WM_SETCURSOR` (`0x402be0`), clamp do mouse (`0x456371`), tela de loading (`0x4028c9`, `0x40295e`) e `RKC_DBFCONTROL::Initialize` (`0x401afc`).

3. **Viewport do Mundo e Câmera com Painéis Laterais (`ShadowFlare.exe`):**
   - Intercepta o `SetRect` inicial (`0x401ad9` → `0x472580`) e o `RKC_DBF::SetClipRect` por frame (`0x41ea11` → `0x472670`) para usar o viewport inteiro `(-ox, -oy, W-1-ox, 479)`.
   - Reescreve o bloco de câmera (`0x41ea62..0x41eac0`) mantendo `viewport_offset_x` (`[ebp+0xa008]`) sempre em `-ox`, garantindo que o mapa inteiro seja redesenhado mesmo com um painel esquerdo (Status/Magia/Loja/Baú) aberto, enquanto desloca o personagem para o lado livre da tela.

4. **Hit-Test de Cliques nas Faixas Ultrawide (`ShadowFlare.exe`):**
   - Intercepta `FUN_00445750` (`0x445750` → `0x472600`) liberando cliques fora de `[0..639, y >= 0]` para o mundo (exceto quando um modal de tela cheia está aberto).
   - Intercepta os dois dispatchers de clique da UI — `FUN_004458c0` (botão esquerdo, `0x4458c0` → `0x472900`) e `FUN_00445a40` (botão direito, `0x445a5d` → `0x472940`) — impedindo que painéis laterais e a barra inferior engulam cliques fora da caixa central `640×480`.

---

## Hashes dos Binários Originais Validados (Episode One)

| Arquivo | SHA-256 |
| :--- | :--- |
| `ShadowFlare.exe` | `db109b5a29a38053feb434cc7594fd49de6b64833ddf4326ac675ca32e71dc94` |
| `RKC_UPDIB.dll` | `710f6cfbe190f74f76dc48ef299ca0f139d886e5631f18370001f7e4e279284c` |

---

## Licença

MIT — veja [LICENSE](LICENSE).
