#!/usr/bin/env python3
"""
ShadowFlare Episode One — Widescreen / Ultrawide & QoL Binary Patcher

Patches `ShadowFlare.exe`, `RKC_UPDIB.dll`, and optionally `RKC_DBFCONTROL.dll`:
- Custom widescreen/ultrawide internal resolution (default: 1280x540, exact 2x
  integer scale for 2560x1080 monitors) with centered bottom-anchored 640x480 HUD
- Full-screen world rendering & clicking with left/right panels open
- Full-screen enemy/NPC/ground-item hover labels across ultrawide bands
- Full-resolution PrintScreen screenshots
- Mouse wheel (WM_MOUSEWHEEL) to cycle assigned F1–F8 magic hotkeys
- Optional FPS / tick-rate adjustment in RKC_DBFCONTROL.dll (note: game logic is
  coupled to frame ticks, so >30 FPS also speeds up game speed proportionally)

Zero external dependencies (Python 3 standard library only).
Always creates `.orig.bak` backups before modifying any file.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import struct
import sys
from pathlib import Path

ORIG_EXE_SHA256 = "db109b5a29a38053feb434cc7594fd49de6b64833ddf4326ac675ca32e71dc94"
ORIG_UPDIB_SHA256 = "710f6cfbe190f74f76dc48ef299ca0f139d886e5631f18370001f7e4e279284c"
ORIG_DBF_SHA256 = "edcfbbdd230d7dd0b82725006f6c88afe15028c41990290f02a6628c6e3f3871"

PRESETS = {
    "21:9-2x": (1280, 540),    # Exact 2x integer scale for 2560x1080
    "16:9-2x": (960, 540),     # Exact 2x integer scale for 1920x1080
    "16:9-720p": (1280, 720),  # Exact 2x integer scale for 2560x1440 / native 720p
    "16:9-1080p": (1920, 1080),
    "21:9-native": (2560, 1080),
}

# Key table at VA 0x47fac0 (file offset 0x7fac0): index -> (default_name, VK code)
KEY_TABLE_OFF = 0x7fac0
KEY_NAME_TO_INDEX: dict[str, int] = {
    "SHIFT": 2, "CTRL": 3, "ALT": 4,
    "UP": 6, "DOWN": 7, "LEFT": 8, "RIGHT": 9,
    "TAB": 12, "ESC": 13, "ENTER": 14, "SPACE": 15,
    **{chr(ord("A") + i): 16 + i for i in range(26)},
    **{str(i): 42 + i for i in range(10)},
}
KEY_NAME_TO_VK: dict[str, int] = {
    "SHIFT": 0x10, "CTRL": 0x11, "ALT": 0x12,
    "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
    "TAB": 0x09, "ESC": 0x1B, "ENTER": 0x0D, "SPACE": 0x20,
    "CAPSLOCK": 0x14, "BACKSPACE": 0x08, "DELETE": 0x2E, "INSERT": 0x2D,
    "HOME": 0x24, "END": 0x23, "PGUP": 0x21, "PGDN": 0x22,
    **{chr(c): c for c in range(ord("A"), ord("Z") + 1)},
    **{chr(c): c for c in range(ord("0"), ord("9") + 1)},
    **{f"F{i}": 0x6F + i for i in range(1, 13)},
    **{f"NUM{i}": 0x60 + i for i in range(10)},
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel32(src_va: int, dst_va: int, instr_len: int = 5) -> bytes:
    return struct.pack("<i", dst_va - (src_va + instr_len))


def jmp_rel32(src_va: int, dst_va: int) -> bytes:
    return b"\xe9" + rel32(src_va, dst_va, 5)


def call_rel32(src_va: int, dst_va: int) -> bytes:
    return b"\xe8" + rel32(src_va, dst_va, 5)


def i32(val: int) -> bytes:
    return struct.pack("<i", val)


def u32(val: int) -> bytes:
    return struct.pack("<I", val)


class AsmBuf:
    """Minimal pure-Python x86-32 label/jump resolver for code caves."""

    def __init__(self, base_va: int) -> None:
        self.base_va = base_va
        self.buf = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str]] = []

    @property
    def va(self) -> int:
        return self.base_va + len(self.buf)

    def emit(self, raw: bytes) -> None:
        self.buf += raw

    def label(self, name: str) -> None:
        self.labels[name] = len(self.buf)

    def jmp_label(self, name: str) -> None:
        self.buf += b"\xe9"
        self.fixups.append((len(self.buf), name))
        self.buf += b"\x00\x00\x00\x00"

    def jcc_label(self, cc: str, name: str) -> None:
        op = {
            "je": b"\x0f\x84",
            "jne": b"\x0f\x85",
            "jl": b"\x0f\x8c",
            "jge": b"\x0f\x8d",
            "jle": b"\x0f\x8e",
            "jg": b"\x0f\x8f",
        }[cc]
        self.buf += op
        self.fixups.append((len(self.buf), name))
        self.buf += b"\x00\x00\x00\x00"

    def call_va(self, target_va: int) -> None:
        self.buf += call_rel32(self.va, target_va)

    def jmp_va(self, target_va: int) -> None:
        self.buf += jmp_rel32(self.va, target_va)

    def finish(self) -> bytes:
        for pos, name in self.fixups:
            target = self.labels[name]
            struct.pack_into("<i", self.buf, pos, target - (pos + 4))
        return bytes(self.buf)


def apply_key_remaps(data: bytearray, remaps: list[str]) -> list[tuple[str, str]]:
    """
    Remaps gameplay hotkeys in ShadowFlare's key table at 0x47fac0.
    Format of each remap entry: ORIGINAL=NEW (e.g., I=E, S=C, 1=Q).
    """
    applied: list[tuple[str, str]] = []
    for spec in remaps:
        if "=" not in spec:
            raise ValueError(f"Invalid remap '{spec}': expected ORIGINAL=NEW (e.g. I=E)")
        orig_k, new_k = (s.strip().upper() for s in spec.split("=", 1))
        if orig_k not in KEY_NAME_TO_INDEX:
            raise ValueError(f"Unknown original key '{orig_k}'. Supported: {', '.join(sorted(KEY_NAME_TO_INDEX))}")
        if new_k not in KEY_NAME_TO_VK:
            raise ValueError(f"Unknown target key '{new_k}'. Supported: {', '.join(sorted(KEY_NAME_TO_VK))}")
        idx = KEY_NAME_TO_INDEX[orig_k]
        vk = KEY_NAME_TO_VK[new_k]
        struct.pack_into("<I", data, KEY_TABLE_OFF + idx * 4, vk)
        applied.append((orig_k, new_k))
    return applied


def patch_rkc_dbfcontrol(data: bytearray, fps: int) -> int:
    """
    Patches the frame interval cap (originally 0x21 = 33ms, ~30 FPS) in
    RKC_DBFCONTROL.dll (FUN_10001bd0 at 0x10001c2f and 0x10001c3c).
    Returns the applied frame interval in milliseconds.
    """
    ms = 33 if fps == 30 else max(8, min(127, round(1000 / fps)))
    assert data[0x1c2f : 0x1c32] == b"\x83\xfa\x21", "Unexpected opcode at RKC_DBFCONTROL 0x1c2f"
    assert data[0x1c3c : 0x1c3f] == b"\x83\xf8\x21", "Unexpected opcode at RKC_DBFCONTROL 0x1c3c"
    data[0x1c31] = ms
    data[0x1c3e] = ms
    return ms


def patch_rkc_updib(data: bytearray, width: int, height: int) -> None:
    """
    Patches RKC_UPDIB.dll to shift all rendered VSPackets by (+ox, +oy)
    whenever the destination DIB width is >= `width` (i.e. the main screen
    backbuffer), leaving offscreen helper DIBs untouched.
    """
    ox = (width - 640) // 2
    oy = height - 480

    # Expand .text VirtualSize so code cave at 0x100080f0..0x100082ff is mapped
    struct.pack_into("<I", data, 0x1e0, 0x7300)

    cave_va = 0x100080f0
    orig_render_va = 0x10003550

    for call_va in (0x100030ab, 0x100030d2, 0x100030ff):
        off = call_va - 0x10000000
        assert data[off] == 0xe8, f"Expected CALL opcode at 0x{call_va:x}"
        data[off : off + 5] = call_rel32(call_va, cave_va)

    code = bytearray()

    def cur_va() -> int:
        return cave_va + len(code)

    code += b"\x8b\x44\x24\x04"          # mov eax, [esp+4]
    code += b"\x85\xc0"                  # test eax, eax
    je1_pos = len(code)
    code += b"\x74\x00"                  # je fallback
    code += b"\x8b\x00"                  # mov eax, [eax]
    code += b"\x85\xc0"                  # test eax, eax
    je2_pos = len(code)
    code += b"\x74\x00"                  # je fallback
    code += b"\x81\x78\x04" + i32(width) # cmp dword ptr [eax+4], width
    code += b"\x7d\x05"                  # jge do_shift
    fallback_pos = len(code)
    code += jmp_rel32(cur_va(), orig_render_va)

    code[je1_pos + 1] = fallback_pos - (je1_pos + 2)
    code[je2_pos + 1] = fallback_pos - (je2_pos + 2)

    code += b"\x56\x57"                  # push esi; push edi
    code += b"\x83\xec\x10"              # sub esp, 0x10
    code += b"\x89\xce"                  # mov esi, ecx

    code += b"\x81\x46\x0c" + i32(ox)
    code += b"\x81\x46\x10" + i32(oy)

    code += b"\xf7\x46\x04\x00\x0e\x00\x00"  # test dword ptr [esi+4], 0xe00
    code += b"\x74\x0e"
    code += b"\x81\x46\x14" + i32(ox)
    code += b"\x81\x46\x18" + i32(oy)

    code += b"\xf6\x46\x04\x20"              # test byte ptr [esi+4], 0x20
    code += b"\x74\x1c"
    code += b"\x81\x46\x40" + i32(ox)
    code += b"\x81\x46\x44" + i32(oy)
    code += b"\x81\x46\x48" + i32(ox)
    code += b"\x81\x46\x4c" + i32(oy)

    code += b"\x8b\x44\x24\x20"              # mov eax, [esp+0x20]
    code += b"\x85\xc0"
    je_clip_pos = len(code)
    code += b"\x74\x00"
    code += b"\x8b\x10" + b"\x81\xc2" + i32(ox) + b"\x89\x14\x24"
    code += b"\x8b\x50\x04" + b"\x81\xc2" + i32(oy) + b"\x89\x54\x24\x04"
    code += b"\x8b\x50\x08" + b"\x81\xc2" + i32(ox) + b"\x89\x54\x24\x08"
    code += b"\x8b\x50\x0c" + b"\x81\xc2" + i32(oy) + b"\x89\x54\x24\x0c"
    code += b"\x89\xe0"
    call_orig_pos = len(code)
    code[je_clip_pos + 1] = call_orig_pos - (je_clip_pos + 2)

    code += b"\x50"
    code += b"\xff\x74\x24\x20"
    code += b"\x89\xf1"
    code += call_rel32(cur_va(), orig_render_va)
    code += b"\x89\xc7"

    code += b"\x81\x46\x0c" + i32(-ox)
    code += b"\x81\x46\x10" + i32(-oy)
    code += b"\xf7\x46\x04\x00\x0e\x00\x00"
    code += b"\x74\x0e"
    code += b"\x81\x46\x14" + i32(-ox)
    code += b"\x81\x46\x18" + i32(-oy)
    code += b"\xf6\x46\x04\x20"
    code += b"\x74\x1c"
    code += b"\x81\x46\x40" + i32(-ox)
    code += b"\x81\x46\x44" + i32(-oy)
    code += b"\x81\x46\x48" + i32(-ox)
    code += b"\x81\x46\x4c" + i32(-oy)

    code += b"\x89\xf8"
    code += b"\x83\xc4\x10"
    code += b"\x5f\x5e"
    code += b"\xc2\x08\x00"

    cave_off = cave_va - 0x10000000
    assert len(code) <= 0x200, f"RKC_UPDIB cave overflow: {len(code)}"
    data[cave_off : cave_off + len(code)] = code


def patch_shadowflare_exe(
    data: bytearray,
    width: int,
    height: int,
    enable_wheel: bool = True,
    enable_item_labels: bool = True,
) -> None:
    """
    Patches ShadowFlare.exe for custom internal resolution (width x height) and QoL:
    - Backbuffer, loading screen, and PrintScreen screenshot dimensions
    - ScreenToClient coordinate shift (-ox, -oy) so HUD stays in 0..639, 0..479
    - Full-screen clip rect (-ox, -oy, width-1-ox, height-1-oy) for world rendering
    - Camera centering & side-panel camera offsets (left/right panels keep full screen live)
    - Cursor bounds & WM_SETCURSOR bounds
    - UI left/right click dispatchers & FUN_00445750 world hit-test for ultrawide side bands
    - Full-screen hover labels (FUN_00416bb0) for enemies, NPCs, and ground items
    - Optional WM_MOUSEWHEEL magic cycling (F1–F8 hotkeys)
    - Optional Diablo 2-style ALT toggle for ground item labels with rarity colors
    """
    ox = (width - 640) // 2
    oy = height - 480
    min_x = -ox
    min_y = -oy
    max_x = width - 1 - ox
    max_y = height - 1 - oy  # always 479 (0x1df)

    def va_to_off(va: int) -> int:
        return va - 0x400000

    def write_va(va: int, blob: bytes) -> None:
        off = va_to_off(va)
        data[off : off + len(blob)] = blob

    # 1. Expand .text VirtualSize from 0x71574 to 0x71e00 (RawSize is 0x72000)
    struct.pack_into("<I", data, 0x1e8, 0x71e00)

    # 2. Direct resolution constants (width x height)
    # RKC_DBFCONTROL::Initialize(height, width) at 0x401afc / 0x401b01
    write_va(0x401afd, i32(height))
    write_va(0x401b02, i32(width))

    # Loading screen DIB creation at 0x40295e / 0x402963
    write_va(0x40295f, i32(height))
    write_va(0x402964, i32(width))

    # PrintScreen screenshot DIB creation at 0x41f2e3 / 0x41f2e8
    write_va(0x41f2e4, i32(height))
    write_va(0x41f2e9, i32(width))

    # Loading screen click box (shifted by +ox, +oy) in FUN_004028a0
    write_va(0x4028c9, i32(0x250 + ox))
    write_va(0x4028d0, i32(0x274 + ox))
    write_va(0x4028db, i32(0x1c2 + oy))
    write_va(0x4028e2, i32(0x1d8 + oy))

    # WM_SETCURSOR bounds (0..width-1, 0..height-1) at 0x402be0 / 0x402be8
    write_va(0x402be2, i32(width - 1))
    write_va(0x402be9, i32(height - 1))

    # 3. Code cave 0x472580: SetRect replacement for initial clip rect (0x401ad9)
    cave_setrect = 0x472580
    code_setrect = (
        b"\x8b\x44\x24\x04"                # mov eax, [esp+4]
        b"\xc7\x00" + i32(min_x) +         # mov [eax], min_x
        b"\xc7\x40\x04" + i32(min_y) +     # mov [eax+4], min_y
        b"\xc7\x40\x08" + i32(max_x) +     # mov [eax+8], max_x
        b"\xc7\x40\x0c" + i32(max_y) +     # mov [eax+0xc], max_y
        b"\xb8\x01\x00\x00\x00"            # mov eax, 1
        b"\xc2\x14\x00"                    # ret 0x14
    )
    write_va(cave_setrect, code_setrect)
    write_va(0x401ad9, call_rel32(0x401ad9, cave_setrect) + b"\x90")

    # 4. Code cave 0x4725b0: ScreenToClient wrapper subtracting (ox, oy)
    cave_s2c = 0x4725b0
    code_s2c = (
        b"\xff\x74\x24\x08"                # push dword ptr [esp+8]
        b"\xff\x74\x24\x08"                # push dword ptr [esp+8]
        b"\xff\x15\xd0\x35\x47\x00"        # call [ScreenToClient]
        b"\x8b\x4c\x24\x08"                # mov ecx, [esp+8]
        b"\x81\x29" + i32(ox) +            # sub dword ptr [ecx], ox
        b"\x81\x69\x04" + i32(oy) +        # sub dword ptr [ecx+4], oy
        b"\xc2\x08\x00"                    # ret 8
    )
    assert len(code_s2c) <= 0x30
    write_va(cave_s2c, code_s2c)
    write_va(0x4087ce, call_rel32(0x4087ce, cave_s2c) + b"\x90")
    write_va(0x456311, call_rel32(0x456311, cave_s2c) + b"\x90")

    # 5. Input polling mouse bounds in FUN_004562f0 (0x456371..0x456389)
    if oy <= 127:
        bounds_code = (
            b"\x05" + i32(ox) +
            b"\x3d" + i32(width - 1) +
            b"\x77\x0d"
            b"\x8b\x46\x14"
            b"\x83\xc0" + bytes([oy]) +
            b"\x3d" + i32(height - 1) +
            b"\x76\x03"
        )
        assert len(bounds_code) == 25
        write_va(0x456371, bounds_code)
    else:
        cave_bounds = 0x472980
        cb = bytearray()
        cb += b"\x05" + i32(ox)
        cb += b"\x3d" + i32(width - 1)
        cb += b"\x0f\x87" + rel32(cave_bounds + len(cb), 0x45638a, 6)
        cb += b"\x8b\x46\x14"
        cb += b"\x05" + i32(oy)
        cb += b"\x3d" + i32(height - 1)
        cb += b"\x0f\x86" + rel32(cave_bounds + len(cb), 0x45638d, 6)
        cb += jmp_rel32(cave_bounds + len(cb), 0x45638a)
        write_va(cave_bounds, bytes(cb))
        write_va(0x456371, jmp_rel32(0x456371, cave_bounds) + b"\x90" * 20)

    # 6. Code cave 0x472670: Full-screen gameplay clip rect before RKC_DBF::SetClipRect (0x41ea11)
    cave_clip = 0x472670
    code_clip = (
        b"\xc7\x44\x24\x54" + i32(min_x) +
        b"\xc7\x44\x24\x58" + i32(min_y) +
        b"\xc7\x44\x24\x5c" + i32(max_x) +
        b"\xc7\x44\x24\x60" + i32(max_y) +
        b"\x8b\x0d\x18\x2d\x48\x00"
        b"\xc3"
    )
    assert len(code_clip) <= 0x30
    write_va(cave_clip, code_clip)
    write_va(0x41ea11, call_rel32(0x41ea11, cave_clip) + b"\x90")

    # 7. Gameplay camera centering & side-panel offsets (0x41ea62..0x41eac0)
    cam_normal_x = -(width // 2)
    cam_rpanel_x = -(width // 2 - 320)
    cam_lpanel_x = -(width // 2 + 320)
    cam_y = -(height // 2 + 30)

    cam_code = bytearray()
    cam_code += b"\x83\xf8\x01"                  # cmp eax, 1 (right panel)
    cam_code += b"\x74\x11"                      # je rpanel
    cam_code += b"\x83\xf8\x10"                  # cmp eax, 0x10 (left panel)
    cam_code += b"\x74\x18"                      # je lpanel
    cam_code += b"\xb9" + i32(cam_normal_x)
    cam_code += b"\xba" + i32(min_x)
    cam_code += b"\xeb\x16"
    cam_code += b"\xb9" + i32(cam_rpanel_x)
    cam_code += b"\xba" + i32(min_x)
    cam_code += b"\xeb\x0a"
    cam_code += b"\xb9" + i32(cam_lpanel_x)
    cam_code += b"\xba" + i32(min_x)
    cam_code += b"\x01\x0f"
    cam_code += b"\x81\x06" + i32(cam_y)
    cam_code += b"\x89\x95\x08\xa0\x00\x00"
    cam_code += b"\xc7\x85\x0c\xa0\x00\x00" + i32(min_y)
    cam_code += b"\xeb\x19"
    pad_len = (0x41eac1 - 0x41ea62) - len(cam_code)
    assert pad_len >= 0
    cam_code += b"\x90" * pad_len
    write_va(0x41ea62, bytes(cam_code))

    # 8. Code cave 0x472600: FUN_00445750 (world click/hover allowed hit-test)
    cave_hit = 0x472600
    ch = bytearray()
    ch += b"\x8b\x44\x24\x04"
    ch += b"\x8b\x54\x24\x08"
    ch += b"\x83\xf8\x00"
    ch += b"\x7c\x16"
    ch += b"\x3d\x7f\x02\x00\x00"
    ch += b"\x7f\x0f"
    ch += b"\x83\xfa\x00"
    ch += b"\x7c\x0a"
    ch += b"\x8b\x54\x24\x08"
    ch += b"\x56"
    ch += jmp_rel32(cave_hit + len(ch), 0x445755)
    ch += b"\x31\xc0"
    for modal_va, disp in [
        (0x48ce54, 0x25),
        (0x48ce6c, 0x1c),
        (0x48ce58, 0x13),
        (0x48ce5c, 0x0a),
        (0x48ce60, 0x01),
    ]:
        ch += b"\x83\x3d" + u32(modal_va) + b"\x01"
        ch += b"\x74" + bytes([disp])
    ch += b"\x40"
    ch += b"\xc2\x08\x00"
    write_va(cave_hit, bytes(ch))
    write_va(0x445750, jmp_rel32(0x445750, cave_hit))

    # 9. Full-screen hover labels in FUN_00416bb0 (0x416cc5..0x416cfd)
    # Replaces hardcoded 640x408 / side-panel checks with FUN_00445750(mouse_x, mouse_y)
    hover = bytearray()
    hover += b"\xff\x74\x24\x1c"                            # push dword ptr [esp+0x1c] (mouse_y)
    hover += b"\x57"                                        # push edi (mouse_x)
    hover += call_rel32(0x416cc5 + len(hover), 0x445750)    # call FUN_00445750
    hover += b"\x83\xf8\x01"                                # cmp eax, 1
    hover += b"\x0f\x85" + rel32(0x416cc5 + len(hover), 0x416f76, 6)  # jne 0x416f76
    hover_pad = (0x416cfd - 0x416cc5) - len(hover)
    assert hover_pad >= 0
    hover += b"\x90" * hover_pad
    write_va(0x416cc5, bytes(hover))

    # 10. Guard UI left-click (FUN_004458c0) and right-click (FUN_00445a40) dispatchers
    cave_left = 0x472900
    cl = bytearray()
    cl += b"\x56"
    cl += b"\x89\xce"
    cl += b"\x8b\x86\xfc\x03\x00\x00"
    cl += b"\x83\x38\x00"
    cl += b"\x7c\x18"
    cl += b"\x81\x38\x7f\x02\x00\x00"
    cl += b"\x7f\x10"
    cl += b"\x83\x78\x04\x00"
    cl += b"\x7c\x0a"
    cl += call_rel32(cave_left + len(cl), 0x451cb0)
    cl += jmp_rel32(cave_left + len(cl), 0x4458c8)
    cl += b"\x31\xc0"
    cl += b"\x83\xbe\x24\x16\x00\x00\x01"
    cl += b"\x0f\x94\xc0"
    cl += b"\x5e\xc3"
    write_va(cave_left, bytes(cl))
    write_va(0x4458c0, jmp_rel32(0x4458c0, cave_left) + b"\x90\x90\x90")

    cave_right = 0x472940
    cr = bytearray()
    cr += b"\x8b\x86\xfc\x03\x00\x00"
    cr += b"\x83\x38\x00"
    cr += b"\x7c\x1a"
    cr += b"\x81\x38\x7f\x02\x00\x00"
    cr += b"\x7f\x12"
    cr += b"\x83\x78\x04\x00"
    cr += b"\x7c\x0c"
    cr += b"\x89\xf1"
    cr += call_rel32(cave_right + len(cr), 0x451cb0)
    cr += jmp_rel32(cave_right + len(cr), 0x445a64)
    cr += b"\x31\xc0"
    cr += b"\x5e\xc3"
    write_va(cave_right, bytes(cr))
    write_va(0x445a5d, jmp_rel32(0x445a5d, cave_right) + b"\x90\x90")

    # 11. Optional WM_MOUSEWHEEL (0x020A) magic cycling (F1–F8 hotkeys)
    # Hooked at 0x402ca5: `ja 0x402ecb` -> `ja 0x4729c0`
    if enable_wheel:
        cave_wheel = 0x4729c0
        cw = bytearray()

        def wva() -> int:
            return cave_wheel + len(cw)

        # if (uMsg != WM_MOUSEWHEEL) goto DefWindowProc (0x402ecb)
        cw += b"\x81\xfe\x0a\x02\x00\x00"            # cmp esi, 0x20a
        cw += b"\x0f\x85" + rel32(wva(), 0x402ecb, 6)  # jne 0x402ecb

        # ecx = DAT_0048ce0c (player manager); if (!ecx) return 0
        cw += b"\x8b\x0d" + u32(0x48ce0c)            # mov ecx, [0x48ce0c]
        cw += b"\x85\xc9"                            # test ecx, ecx
        jz1 = len(cw)
        cw += b"\x74\x00"                            # je ret_zero

        # eax = FUN_00434cb0(ecx) (active player); if (!eax) return 0
        cw += call_rel32(wva(), 0x434cb0)
        cw += b"\x85\xc0"                            # test eax, eax
        jz2 = len(cw)
        cw += b"\x74\x00"                            # je ret_zero

        # edx = cur_magic = [eax + 0x127c]
        cw += b"\x8b\x90\x7c\x12\x00\x00"            # mov edx, [eax+0x127c]

        # Find current index in DAT_0048d508[0..7] -> ecx (defaults to 0 if not found)
        cw += b"\x31\xc9"                            # xor ecx, ecx
        find_loop = len(cw)
        cw += b"\x39\x14\x8d" + u32(0x48d508)        # cmp [ecx*4 + 0x48d508], edx
        je_found = len(cw)
        cw += b"\x74\x00"                            # je found
        cw += b"\x41"                                # inc ecx
        cw += b"\x83\xf9\x08"                        # cmp ecx, 8
        cw += b"\x7c" + struct.pack("<b", find_loop - (len(cw) + 2))
        cw += b"\x31\xc9"                            # xor ecx, ecx (fallback index 0)

        found_pos = len(cw)
        cw[je_found + 1] = found_pos - (je_found + 2)

        # Read wheel delta = (short)[esp+0x3e]: <0 -> step=+1 (next), >0 -> step=-1 (prev)
        cw += b"\x0f\xbf\x44\x24\x3e"                # movsx eax, word ptr [esp+0x3e]
        cw += b"\x85\xc0"                            # test eax, eax
        jz3 = len(cw)
        cw += b"\x74\x00"                            # je ret_zero
        cw += b"\xba\x01\x00\x00\x00"                # mov edx, 1
        cw += b"\x78\x05"                            # js step_ready (delta < 0 => +1)
        cw += b"\xba\xff\xff\xff\xff"                # mov edx, -1   (delta > 0 => -1)

        # Search up to 8 slots for next assigned hotkey (!= -1)
        cw += b"\xb8\x08\x00\x00\x00"                # mov eax, 8 (loop counter)
        step_loop = len(cw)
        cw += b"\x01\xd1"                            # add ecx, edx
        cw += b"\x83\xe1\x07"                        # and ecx, 7
        cw += b"\x83\x3c\x8d" + u32(0x48d508) + b"\xff"  # cmp dword ptr [ecx*4+0x48d508], -1
        jne_hit = len(cw)
        cw += b"\x75\x00"                            # jne hit
        cw += b"\x48"                                # dec eax
        cw += b"\x75" + struct.pack("<b", step_loop - (len(cw) + 2))
        jmp_ret0 = len(cw)
        cw += b"\xeb\x00"                            # jmp ret_zero

        hit_pos = len(cw)
        cw[jne_hit + 1] = hit_pos - (jne_hit + 2)
        cw += b"\x89\x0d" + u32(0x48d52c)            # mov [0x48d52c], ecx

        ret0_pos = len(cw)
        cw[jz1 + 1] = ret0_pos - (jz1 + 2)
        cw[jz2 + 1] = ret0_pos - (jz2 + 2)
        cw[jz3 + 1] = ret0_pos - (jz3 + 2)
        cw[jmp_ret0 + 1] = ret0_pos - (jmp_ret0 + 2)

        # WndProc epilogue (matches 0x402c00): return 0
        cw += b"\x5f\x5e\x31\xc0\x5b\x83\xc4\x24\xc2\x10\x00"

        assert len(cw) <= 0x100, f"wheel cave overflow: {len(cw)}"
        write_va(cave_wheel, bytes(cw))
        # Patch 0x402ca5: 0f 87 <rel32> (ja cave_wheel)
        write_va(0x402ca5, b"\x0f\x87" + rel32(0x402ca5, cave_wheel, 6))

    # 12. Optional Diablo 2-style ALT toggle for ground item labels (0x41f37b -> 0x472b00)
    # Toggle flag lives in zero-initialized writable .data padding at VA 0x48ff00.
    if enable_item_labels:
        cave_items = 0x472b00
        toggle_va = 0x48ff00
        a = AsmBuf(cave_items)

        # Save callee-saved regs + ecx (this = game_state)
        a.emit(b"\x55\x53\x56\x57\x51")        # push ebp, ebx, esi, edi, ecx
        a.emit(b"\x83\xec\x20")                # sub esp, 0x20
        # Copy vsBlock from caller arg ([esp + 0x38]) into local [esp + 0x18]
        a.emit(b"\x8b\x44\x24\x38")            # mov eax, [esp+0x38]
        a.emit(b"\x89\x44\x24\x18")            # mov [esp+0x18], eax

        # Check ALT pressed edge: [[0x48d8b4] + 4] + 0x80 == 1 -> xor [toggle_va], 1
        a.emit(b"\xa1" + u32(0x48d8b4))        # mov eax, [0x48d8b4]
        a.emit(b"\x85\xc0")
        a.jcc_label("je", "check_toggle")
        a.emit(b"\x8b\x40\x04")                # mov eax, [eax+4]
        a.emit(b"\x85\xc0")
        a.jcc_label("je", "check_toggle")
        a.emit(b"\x83\xb8\x80\x00\x00\x00\x01")  # cmp dword ptr [eax+0x80], 1
        a.jcc_label("jne", "check_toggle")
        a.emit(b"\x83\x35" + u32(toggle_va) + b"\x01")  # xor dword ptr [toggle_va], 1

        a.label("check_toggle")
        a.emit(b"\x83\x3d" + u32(toggle_va) + b"\x00")  # cmp dword ptr [toggle_va], 0
        a.jcc_label("je", "done")

        # Get active player -> current map -> entity list
        a.emit(b"\x8b\x0d" + u32(0x48ce0c))    # mov ecx, [0x48ce0c]
        a.emit(b"\x85\xc9")
        a.jcc_label("je", "done")
        a.call_va(0x434cb0)                    # active player -> eax
        a.emit(b"\x85\xc0")
        a.jcc_label("je", "done")
        a.emit(b"\x8b\x80\x58\x04\x00\x00")    # mov eax, [eax+0x458] (current map)
        a.emit(b"\x85\xc0")
        a.jcc_label("je", "done")
        a.emit(b"\x8b\x40\x30")                # mov eax, [eax+0x30] (entity list)
        a.emit(b"\x85\xc0")
        a.jcc_label("je", "done")
        a.emit(b"\x8b\x08")                    # mov ecx, [eax] (count)
        a.emit(b"\x85\xc9")
        a.jcc_label("jle", "done")
        a.emit(b"\x89\x4c\x24\x14")            # mov [esp+0x14], ecx (count)
        a.emit(b"\x8b\x40\x04")                # mov eax, [eax+4] (arr)
        a.emit(b"\x85\xc0")
        a.jcc_label("je", "done")
        a.emit(b"\x89\x44\x24\x10")            # mov [esp+0x10], eax (arr)

        # origin_x = camera_x - viewport_offset_x; origin_y = camera_y - viewport_offset_y
        a.emit(b"\xa1" + u32(0x48cdf4))
        a.emit(b"\x2b\x05" + u32(0x48cdfc))
        a.emit(b"\x89\x44\x24\x08")            # [esp+0x08] = origin_x
        a.emit(b"\xa1" + u32(0x48cdf8))
        a.emit(b"\x2b\x05" + u32(0x48ce00))
        a.emit(b"\x89\x44\x24\x0c")            # [esp+0x0c] = origin_y

        # Loop edi = 0 .. count - 1
        a.emit(b"\x31\xff")                    # xor edi, edi
        a.label("loop")
        a.emit(b"\x3b\x7c\x24\x14")            # cmp edi, [esp+0x14]
        a.jcc_label("jge", "done")
        a.emit(b"\x8b\x44\x24\x10")            # mov eax, [esp+0x10] (arr)
        a.emit(b"\x8b\x04\xb8")                # mov eax, [eax + edi*4] (ent)
        a.emit(b"\x85\xc0")
        a.jcc_label("je", "next")
        a.emit(b"\x83\x38\x03")                # cmp dword ptr [eax], 3 (ground item)
        a.jcc_label("jne", "next")
        a.emit(b"\x8b\x70\x08")                # mov esi, [eax+8] (obj)
        a.emit(b"\x85\xf6")
        a.jcc_label("je", "next")
        a.emit(b"\x8b\x9e\xcc\x00\x00\x00")    # mov ebx, [esi+0xcc] (item)
        a.emit(b"\x85\xdb")
        a.jcc_label("je", "next")

        # RKC_RPGSCRN::CalcRealPos([0x482d14], [esi+0x18], [esi+0x1c], &rx, &ry)
        a.emit(b"\x8d\x44\x24\x04\x50")        # lea eax, [esp+4] (&ry); push eax
        a.emit(b"\x8d\x44\x24\x04\x50")        # lea eax, [esp+4] (&rx); push eax
        a.emit(b"\xff\x76\x1c")                # push [esi+0x1c]
        a.emit(b"\xff\x76\x18")                # push [esi+0x18]
        a.emit(b"\x8b\x0d" + u32(0x482d14))    # mov ecx, [0x482d14]
        a.emit(b"\xff\x15" + u32(0x473358))    # call [CalcRealPos]

        # sx = rx - origin_x (esi), sy = ry - origin_y - 24 (ebp)
        a.emit(b"\x8b\x34\x24")                # mov esi, [esp+0x00] (rx)
        a.emit(b"\x2b\x74\x24\x08")            # sub esi, [esp+0x08]
        a.emit(b"\x8b\x6c\x24\x04")            # mov ebp, [esp+0x04] (ry)
        a.emit(b"\x2b\x6c\x24\x0c")            # sub ebp, [esp+0x0c]
        a.emit(b"\x83\xed\x18")                # sub ebp, 24

        # Screen bounds check
        a.emit(b"\x81\xfe" + i32(min_x))       # cmp esi, min_x
        a.jcc_label("jl", "next")
        a.emit(b"\x81\xfe" + i32(max_x - 24))  # cmp esi, max_x - 24
        a.jcc_label("jg", "next")
        a.emit(b"\x81\xfd" + i32(min_y))       # cmp ebp, min_y
        a.jcc_label("jl", "next")
        a.emit(b"\x81\xfd" + i32(405))         # cmp ebp, 405
        a.jcc_label("jg", "next")

        # Determine item name: Gold ("Gold" @ 0x47f14b), identified ([ebx+4]), or unidentified ([ebx+8])
        a.emit(b"\x83\x7b\x0c\x04")            # cmp dword ptr [ebx+0x0c], 4
        a.jcc_label("jne", "not_gold")
        a.emit(b"\x83\x7b\x14\x00")            # cmp dword ptr [ebx+0x14], 0
        a.jcc_label("jne", "not_gold")
        a.emit(b"\xb8" + u32(0x47f14b))        # mov eax, "Gold"
        a.jmp_label("have_name")

        a.label("not_gold")
        a.emit(b"\x83\x7b\x1c\x01")            # cmp dword ptr [ebx+0x1c], 1 (identified?)
        a.jcc_label("jne", "unid")
        a.emit(b"\x8b\x43\x04")                # mov eax, [ebx+4]
        a.jmp_label("have_name")

        a.label("unid")
        a.emit(b"\x8b\x43\x08")                # mov eax, [ebx+8]

        a.label("have_name")
        a.emit(b"\x85\xc0")
        a.jcc_label("je", "next")
        a.emit(b"\x89\x44\x24\x1c")            # [esp+0x1c] = name_ptr

        # Center horizontally by subtracting strlen(name) * 3 (6px font -> 3px half-char)
        a.emit(b"\x89\xc2\x31\xc9")            # mov edx, eax; xor ecx, ecx
        a.label("strlen")
        a.emit(b"\x80\x3c\x0a\x00")            # cmp byte ptr [edx+ecx], 0
        a.jcc_label("je", "strlen_done")
        a.emit(b"\x41")                        # inc ecx
        a.jmp_label("strlen")
        a.label("strlen_done")
        a.emit(b"\x8d\x04\x49")                # lea eax, [ecx + ecx*2] (len * 3)
        a.emit(b"\x29\xc6")                    # sub esi, eax
        a.emit(b"\x81\xfe" + i32(min_x))       # cmp esi, min_x
        a.jcc_label("jge", "sx_ok")
        a.emit(b"\xbe" + i32(min_x))           # mov esi, min_x
        a.label("sx_ok")

        # Draw black shadow at (sx+1, sy+1) on VScreen 0x11
        a.emit(b"\x68\xe8\x03\x00\x00")        # push 1000
        a.emit(b"\x6a\x01\x6a\x00\x6a\x00\x6a\x04")  # push 1, 0, 0, 4
        a.emit(b"\x6a\x00\x6a\x00\x6a\x00")    # push B=0, G=0, R=0
        a.emit(b"\xff\x74\x24\x3c")            # push name ([esp+0x1c+0x20])
        a.emit(b"\x8d\x45\x01\x50")            # lea eax, [ebp+1]; push eax
        a.emit(b"\x8d\x46\x01\x50")            # lea eax, [esi+1]; push eax
        a.emit(b"\x6a\x00\x6a\x11")            # push font=0, VScreen=0x11
        a.emit(b"\xff\x74\x24\x4c")            # push vsBlock ([esp+0x18+0x34])
        a.emit(b"\x8b\x0d" + u32(0x482d10))    # mov ecx, [0x482d10]
        a.emit(b"\xff\x15" + u32(0x47356c))    # call [SetStringsPacket]

        # Select rarity color (eax=R, ecx=G, edx=B) from [ebx+0x18]
        a.emit(b"\x8b\x53\x18")                # mov edx, [ebx+0x18]
        a.emit(b"\x83\xfa\x01")
        a.jcc_label("je", "col1")
        a.emit(b"\x83\xfa\x02")
        a.jcc_label("je", "col2")
        a.emit(b"\x83\xfa\x03")
        a.jcc_label("je", "col3")
        # Normal (white)
        a.emit(b"\xb8\xe0\x00\x00\x00\xb9\xe0\x00\x00\x00\xba\xe0\x00\x00\x00")
        a.jmp_label("draw_fg")
        a.label("col1")  # Magic (blue)
        a.emit(b"\xb8\x80\x00\x00\x00\xb9\x90\x00\x00\x00\xba\xff\x00\x00\x00")
        a.jmp_label("draw_fg")
        a.label("col2")  # Rare (gold)
        a.emit(b"\xb8\xff\x00\x00\x00\xb9\xd8\x00\x00\x00\xba\x40\x00\x00\x00")
        a.jmp_label("draw_fg")
        a.label("col3")  # Unique (orange)
        a.emit(b"\xb8\xff\x00\x00\x00\xb9\x80\x00\x00\x00\xba\x30\x00\x00\x00")

        a.label("draw_fg")
        a.emit(b"\x68\xe8\x03\x00\x00")        # push 1000
        a.emit(b"\x6a\x01\x6a\x00\x6a\x00\x6a\x04")  # push 1, 0, 0, 4
        a.emit(b"\x52\x51\x50")                # push B(edx), G(ecx), R(eax)
        a.emit(b"\xff\x74\x24\x3c")            # push name ([esp+0x1c+0x20])
        a.emit(b"\x55\x56")                    # push sy(ebp), sx(esi)
        a.emit(b"\x6a\x00\x6a\x11")            # push font=0, VScreen=0x11
        a.emit(b"\xff\x74\x24\x4c")            # push vsBlock ([esp+0x18+0x34])
        a.emit(b"\x8b\x0d" + u32(0x482d10))    # mov ecx, [0x482d10]
        a.emit(b"\xff\x15" + u32(0x47356c))    # call [SetStringsPacket]

        a.label("next")
        a.emit(b"\x47")                        # inc edi
        a.jmp_label("loop")

        a.label("done")
        a.emit(b"\x83\xc4\x20")                # add esp, 0x20
        a.emit(b"\x59\x5f\x5e\x5b\x5d")        # pop ecx, edi, esi, ebx, ebp
        a.jmp_va(0x4087b0)                     # tail-call original DrawCursor

        item_blob = a.finish()
        assert len(item_blob) <= 0x300, f"item labels cave overflow: {len(item_blob)}"
        write_va(cave_items, item_blob)
        write_va(0x41f37b, call_rel32(0x41f37b, cave_items))


def restore_backups(game_dir: Path) -> None:
    for fname in ("ShadowFlare.exe", "RKC_UPDIB.dll", "RKC_DBFCONTROL.dll"):
        bak = game_dir / f"{fname}.orig.bak"
        dst = game_dir / fname
        if bak.exists():
            shutil.copy2(bak, dst)
            print(f"Restored {dst} from {bak.name}")
        else:
            print(f"No backup found for {fname} ({bak.name})")


def ensure_backup(path: Path, expected_sha256: str, force: bool) -> Path:
    bak = path.with_name(path.name + ".orig.bak")
    if not bak.exists():
        if not force and sha256_file(path) != expected_sha256:
            raise RuntimeError(
                f"{path.name} SHA-256 mismatch.\n"
                f"  Expected: {expected_sha256}\n"
                f"  Found:    {sha256_file(path)}\n"
                f"Use --force to override or restore the original binary first."
            )
        shutil.copy2(path, bak)
        print(f"Created backup: {bak.name}")
    return bak


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Patch ShadowFlare Episode One for widescreen/ultrawide resolutions and QoL."
    )
    parser.add_argument(
        "game_dir",
        type=Path,
        help="Path to the ShadowFlare installation directory (containing ShadowFlare.exe).",
    )
    parser.add_argument(
        "--preset",
        choices=sorted(PRESETS.keys()),
        help="Use a recommended resolution preset.",
    )
    parser.add_argument("--width", type=int, default=1280, help="Internal width (default: 1280).")
    parser.add_argument("--height", type=int, default=540, help="Internal height (default: 540).")
    parser.add_argument(
        "--fps",
        type=int,
        default=30,
        help="Target FPS / tick rate (default: 30). Note: game speed scales proportionally above 30.",
    )
    parser.add_argument(
        "--no-wheel",
        action="store_true",
        help="Disable mouse wheel cycling for F1–F8 magic hotkeys.",
    )
    parser.add_argument(
        "--no-item-labels",
        action="store_true",
        help="Disable Diablo 2-style ALT toggle for ground item labels.",
    )
    parser.add_argument(
        "--remap",
        nargs="+",
        metavar="ORIG=NEW",
        default=[],
        help="Remap gameplay hotkeys (e.g. --remap I=E S=C 1=Q 2=W). Does not affect text input.",
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        help="Restore original binaries from .orig.bak backups.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Apply patch even if original SHA-256 hashes do not match.",
    )
    args = parser.parse_args()

    game_dir: Path = args.game_dir.expanduser().resolve()
    if args.restore:
        restore_backups(game_dir)
        return 0

    width, height = PRESETS[args.preset] if args.preset else (args.width, args.height)
    if width < 640 or height < 480 or width % 2 != 0 or height % 2 != 0:
        print("Error: width must be >= 640, height >= 480, and both must be even.", file=sys.stderr)
        return 1
    if not (15 <= args.fps <= 120):
        print("Error: --fps must be between 15 and 120.", file=sys.stderr)
        return 1

    exe_path = game_dir / "ShadowFlare.exe"
    updib_path = game_dir / "RKC_UPDIB.dll"
    dbf_path = game_dir / "RKC_DBFCONTROL.dll"
    for p in (exe_path, updib_path, dbf_path):
        if not p.exists():
            print(f"Error: {p} not found.", file=sys.stderr)
            return 1

    try:
        exe_bak = ensure_backup(exe_path, ORIG_EXE_SHA256, args.force)
        updib_bak = ensure_backup(updib_path, ORIG_UPDIB_SHA256, args.force)
        dbf_bak = ensure_backup(dbf_path, ORIG_DBF_SHA256, args.force)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    exe_data = bytearray(exe_bak.read_bytes())
    updib_data = bytearray(updib_bak.read_bytes())
    dbf_data = bytearray(dbf_bak.read_bytes())

    patch_shadowflare_exe(
        exe_data,
        width,
        height,
        enable_wheel=not args.no_wheel,
        enable_item_labels=not args.no_item_labels,
    )
    try:
        remapped = apply_key_remaps(exe_data, args.remap)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    patch_rkc_updib(updib_data, width, height)
    frame_ms = patch_rkc_dbfcontrol(dbf_data, args.fps)

    exe_path.write_bytes(exe_data)
    updib_path.write_bytes(updib_data)
    dbf_path.write_bytes(dbf_data)

    print(f"Patched ShadowFlare for {width}x{height}!")
    print(f"  HUD offset:    +{(width - 640) // 2}px X, +{height - 480}px Y")
    print(f"  Hover labels:  full ultrawide enabled")
    print(f"  PrintScreen:   {width}x{height} BMP")
    print(f"  Mouse wheel:   {'F1-F8 magic cycling enabled' if not args.no_wheel else 'disabled'}")
    print(f"  Item labels:   {'ALT toggle enabled' if not args.no_item_labels else 'disabled'}")
    print(f"  Frame cap:     {frame_ms} ms (~{1000/frame_ms:.1f} FPS)")
    if remapped:
        print("  Key remaps:    " + ", ".join(f"{o}->{n}" for o, n in remapped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
