# ShadowFlare Widescreen / Ultrawide Patch

> 🇧🇷 [Leia em Português (README.pt-BR.md)](README.pt-BR.md)

A standalone, zero-dependency Python binary patcher for **ShadowFlare: Episode One** (2001, Denyusha / Emurasoft) that expands the native `640×480` isometric viewport to custom **16:9** and **21:9 ultrawide** resolutions — giving you a wider field of view ("zoom out") while keeping the original `640×480` HUD centered and fully clickable.

> **Disclaimer:** This repository contains **no copyrighted game files or third-party binaries**. You must supply your own installation of ShadowFlare Episode One.

---

## Features

- **True Widescreen / Ultrawide Viewport:** Renders the isometric map and entities across the entire custom resolution (e.g. `1280×540` for exact 2× integer scaling on `2560×1080` monitors, or `960×540` for `1920×1080`).
- **Centered Bottom-Anchored HUD:** All original UI panels, menus, and the bottom bar remain centered (`+ox` horizontally, `+oy` vertically) without stretching or distorting sprites.
- **Fixed Side-Band Clicking & Side Panels:** Clicking in the extra ultrawide bands to the left of Status/Skills/Shop or to the right of Equipment/Inventory passes cleanly to the world instead of being swallowed by UI bounds checks.
- **Full-Width Rendering With Panels Open:** Opening left-hand panels (Status, Magic, Shop, Storage) or right-hand panels (Equipment) shifts the camera away from the panel while continuing to render the entire screen width every frame (no frozen side strips or cursor trails).
- **Full-Screen Hover Labels:** Enemy names/HP, NPCs, and ground item tooltips (`FUN_00416bb0`) work across the entire ultrawide screen, including beside open panels and in the lower side bands.
- **Ultrawide `PrintScreen` Screenshots:** In-game screenshots (`ScreenShot_00000000.Bmp`) are saved at the full patched resolution instead of `640×480`.
- **Mouse Wheel Magic Cycling (`WM_MOUSEWHEEL`):** Scroll up/down in combat to cycle through assigned `F1–F8` magic hotkeys (can be disabled with `--no-wheel`).
- **Optional Frame Rate / Tick Speed (`--fps`):** Adjusts the `33 ms` (`~30 FPS`) loop cap in `RKC_DBFCONTROL.dll`. *(Note: ShadowFlare has no delta-time — game speed scales proportionally above 30 FPS, so values like `--fps 40` act as a mild turbo mode.)*
- **Safe & Reversible:** Automatically verifies original SHA-256 hashes, saves `.orig.bak` backups, and supports `--restore`.

---

## Recommended Resolution Presets

Because ShadowFlare uses 2D pre-rendered sprites, choosing a higher internal resolution acts as a **zoom-out**. Integer-scaled resolutions keep pixels razor-sharp when paired with [cnc-ddraw](https://github.com/FunkyFr3sh/cnc-ddraw):

| Preset | Internal Resolution | Target Monitor | Scale Factor | Feel |
| :--- | :---: | :---: | :---: | :--- |
| `--preset 21:9-2x` *(default)* | `1280×540` | `2560×1080` | Exact `2×` integer | Crisp sprites, 2× wider FOV |
| `--preset 16:9-2x` | `960×540` | `1920×1080` | Exact `2×` integer | Crisp sprites, 1.5× wider FOV |
| `--preset 16:9-720p` | `1280×720` | `2560×1440` / `1920×1080` | `2×` (1440p) / `1.5×` (1080p) | More zoomed out |
| `--preset 16:9-1080p` | `1920×1080` | `1920×1080` | `1×` native | Very zoomed out |
| `--preset 21:9-native` | `2560×1080` | `2560×1080` | `1×` native | Maximum ultrawide FOV |

You can also pass any custom even dimensions (`width >= 640`, `height >= 480`) with `--width` and `--height`.

---

## Usage

### 1. Apply the Patch

Requires Python 3 (no `pip install` needed):

```bash
python3 patch_shadowflare.py "/path/to/ShadowFlare" --preset 21:9-2x
```

Or with a custom resolution:

```bash
python3 patch_shadowflare.py "/path/to/ShadowFlare" --width 1280 --height 540
```

To restore the original unmodified binaries at any time:

```bash
python3 patch_shadowflare.py "/path/to/ShadowFlare" --restore
```

### 2. Fullscreen Scaling with `cnc-ddraw`

1. Download the latest release of [cnc-ddraw](https://github.com/FunkyFr3sh/cnc-ddraw/releases).
2. Extract `ddraw.dll` into your `ShadowFlare` installation directory.
3. Copy the included [`ddraw.ini`](ddraw.ini) from this repository into the same directory.

### 3. Linux / Steam Proton Notes (XWayland)

If you run ShadowFlare via Steam + Proton (tested with `GE-Proton11-12`):

1. Add the following to the game's **Steam Launch Options** so Wine loads the local `ddraw.dll`:
   ```
   WINEDLLOVERRIDES="ddraw=n,b" %command%
   ```
2. Keep `devmode=true` in [`ddraw.ini`](ddraw.ini). Under **XWayland fullscreen**, `cnc-ddraw`'s default `ClipCursor` lock can freeze input on the first mouse click; `devmode=true` disables cursor locking while keeping borderless 2× integer fullscreen working smoothly.

---

## How It Works (Reverse Engineering Notes)

ShadowFlare's engine is split into C++ DLLs with intact exported symbol names (`RKC_DBFCONTROL.dll`, `RKC_UPDIB.dll`, `RKC_RPGSCRN.dll`, `RKC_DIB.dll`, `RKC_WINDOW.dll`). Instead of rewriting hundreds of hardcoded UI draw calls, the patch uses a **shifted coordinate space** (`ox = (W - 640) / 2`, `oy = H - 480`):

1. **Global Render Offset (`RKC_UPDIB.dll`):**
   - Redirects the 3 calls to `RKC_UPDIB_VSPACKET::Render` (`0x100030ab`, `0x100030d2`, `0x100030ff`) to a code cave at `0x100080f0`.
   - Whenever the destination DIB width is `>= W` (the main backbuffer), the wrapper temporarily shifts packet coordinates (`+ox, +oy`), primitive endpoints (flag `0xe00`), internal clip rects (flag `0x20`), and the passed `tagRECT*` clip rect, calls the original renderer, and restores the packet fields.
   - Offscreen helper DIBs remain untouched.

2. **Shifted Mouse Coordinate Space (`ShadowFlare.exe`):**
   - Hooks `ScreenToClient` at `0x4087ce` (cursor render) and `0x456311` (input polling) via a code cave at `0x4725b0` that subtracts `(ox, oy)`.
   - All original UI elements continue operating in `[0..639, 0..479]`, while the full screen spans `[-ox .. W-1-ox, -oy .. 479]`.
   - Updates `WM_SETCURSOR` bounds (`0x402be0`), input polling mouse clamp (`0x456371`), loading screen DIB/click area (`0x4028c9`, `0x40295e`), and `RKC_DBFCONTROL::Initialize` (`0x401afc`).

3. **Full-Screen World Viewport & Side-Panel Camera (`ShadowFlare.exe`):**
   - Hooks initial `SetRect` (`0x401ad9` → `0x472580`) and per-frame `RKC_DBF::SetClipRect` (`0x41ea11` → `0x472670`) to use the full shifted viewport `(-ox, -oy, W-1-ox, 479)`.
   - Rewrites the camera offset block (`0x41ea62..0x41eac0`) so `viewport_offset_x` (`[ebp+0xa008]`) is always `-ox`, ensuring the entire screen redraws even when a left-hand panel (Status/Magic/Skills/Shop/Storage) is open, while still shifting the character left/right away from open panels:
     - Normal / both panels: `camera_x = player_x - W/2`
     - Right panel open (`eax == 1`): `camera_x = player_x - (W/2 - 320)`
     - Left panel open (`eax == 0x10`): `camera_x = player_x - (W/2 + 320)`

4. **Ultrawide Click Hit-Testing (`ShadowFlare.exe`):**
   - Hooks `FUN_00445750` (`0x445750` → `0x472600`) so clicks outside `[0..639, y >= 0]` are allowed to reach the world unless a full-screen modal (`0x48ce54`, `0x48ce6c`, `0x48ce58`, `0x48ce5c`, `0x48ce60`) is open.
   - Hooks both UI click dispatchers — `FUN_004458c0` (left click, `0x4458c0` → `0x472900`) and `FUN_00445a40` (right click, `0x445a5d` → `0x472940`) — so open side panels (which originally only checked `x < 320` or `x > 319`) and the bottom HUD bar never swallow clicks in the ultrawide side/top bands.

---

## Verified Original Binary Hashes (Episode One)

| File | SHA-256 |
| :--- | :--- |
| `ShadowFlare.exe` | `db109b5a29a38053feb434cc7594fd49de6b64833ddf4326ac675ca32e71dc94` |
| `RKC_UPDIB.dll` | `710f6cfbe190f74f76dc48ef299ca0f139d886e5631f18370001f7e4e279284c` |

---

## License

MIT — see [LICENSE](LICENSE).
