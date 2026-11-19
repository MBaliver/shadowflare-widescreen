#!/usr/bin/env python3
"""
ShadowFlare Episode One — Widescreen / Ultrawide Binary Patcher

Patches `ShadowFlare.exe` and `RKC_UPDIB.dll` to render the isometric world at
a custom widescreen/ultrawide internal resolution (default: 1280x540, exact 2x
integer scale for 2560x1080 monitors) while keeping the original 640x480 HUD
centered horizontally and anchored at the bottom.

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
ORIG_DLL_SHA256 = "710f6cfbe190f74f76dc48ef299ca0f139d886e5631f18370001f7e4e279284c"

PRESETS = {
    "21:9-2x": (1280, 540),    # Exact 2x integer scale for 2560x1080
    "16:9-2x": (960, 540),     # Exact 2x integer scale for 1920x1080
    "16:9-720p": (1280, 720),  # Exact 2x integer scale for 2560x1440 / native 720p
    "16:9-1080p": (1920, 1080),
    "21:9-native": (2560, 1080),
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


def patch_rkc_updib(data: bytearray, width: int, height: int) -> None:
    """
    Patches RKC_UPDIB.dll to shift all rendered VSPackets by (+ox, +oy)
    whenever the destination DIB width is >= `width` (i.e. the main screen
    backbuffer), leaving offscreen helper DIBs untouched.
    """
    ox = (width - 640) // 2
    oy = height - 480

    # Expand .text VirtualSize so code cave at 0x100080f0..0x100082ff is mapped
    # PE section header .text Misc_VirtualSize is at file offset 0x1e0
    struct.pack_into("<I", data, 0x1e0, 0x7300)

    cave_va = 0x100080f0
    orig_render_va = 0x10003550

    # Redirect the 3 calls to RKC_UPDIB_VSPACKET::Render inside RKC_UPDIB_VS::Render
    for call_va in (0x100030ab, 0x100030d2, 0x100030ff):
        off = call_va - 0x10000000
        assert data[off] == 0xe8, f"Expected CALL opcode at 0x{call_va:x}"
        data[off : off + 5] = call_rel32(call_va, cave_va)

    # Build the wrapper at 0x100080f0 using imm32 for ox and oy so any resolution works
    code = bytearray()

    def cur_va() -> int:
        return cave_va + len(code)

    # mov eax, [esp+4] ; RKC_DIB* dst
    # test eax, eax ; je fallback
    # mov eax, [eax]  ; BITMAPINFOHEADER*
    # test eax, eax ; je fallback
    # cmp dword ptr [eax+4], width ; jge do_shift
    # fallback: jmp orig_render_va
    code += b"\x8b\x44\x24\x04"          # mov eax, [esp+4]
    code += b"\x85\xc0"                  # test eax, eax
    je1_pos = len(code)
    code += b"\x74\x00"                  # je fallback (patched below)
    code += b"\x8b\x00"                  # mov eax, [eax]
    code += b"\x85\xc0"                  # test eax, eax
    je2_pos = len(code)
    code += b"\x74\x00"                  # je fallback (patched below)
    code += b"\x81\x78\x04" + i32(width) # cmp dword ptr [eax+4], width
    code += b"\x7d\x05"                  # jge do_shift (+5 skips jmp rel32)
    fallback_pos = len(code)
    code += jmp_rel32(cur_va(), orig_render_va)

    code[je1_pos + 1] = fallback_pos - (je1_pos + 2)
    code[je2_pos + 1] = fallback_pos - (je2_pos + 2)

    # do_shift:
    code += b"\x56\x57"                  # push esi; push edi
    code += b"\x83\xec\x10"              # sub esp, 0x10 (local tagRECT copy)
    code += b"\x89\xce"                  # mov esi, ecx (this = RKC_UPDIB_VSPACKET*)

    # Shift packet (x, y) at [esi+0x0c], [esi+0x10]
    code += b"\x81\x46\x0c" + i32(ox)
    code += b"\x81\x46\x10" + i32(oy)

    # If primitive flag 0xe00 (Line/Box/Fill) is set, also shift endpoint [esi+0x14], [esi+0x18]
    code += b"\xf7\x46\x04\x00\x0e\x00\x00"  # test dword ptr [esi+4], 0xe00
    code += b"\x74\x0e"                      # je +14 (2 * 7B)
    code += b"\x81\x46\x14" + i32(ox)
    code += b"\x81\x46\x18" + i32(oy)

    # If clip flag 0x20 is set, shift packet internal clip rect [esi+0x40..0x4c]
    code += b"\xf6\x46\x04\x20"              # test byte ptr [esi+4], 0x20
    code += b"\x74\x1c"                      # je +28 (4 * 7B)
    code += b"\x81\x46\x40" + i32(ox)
    code += b"\x81\x46\x44" + i32(oy)
    code += b"\x81\x46\x48" + i32(ox)
    code += b"\x81\x46\x4c" + i32(oy)

    # If param_2 (tagRECT* clipRect at [esp+0x20]) is non-null, copy & shift into [esp..esp+0x0c]
    code += b"\x8b\x44\x24\x20"              # mov eax, [esp+0x20]
    code += b"\x85\xc0"                      # test eax, eax
    je_clip_pos = len(code)
    code += b"\x74\x00"                      # je call_orig
    code += b"\x8b\x10"                      # mov edx, [eax]
    code += b"\x81\xc2" + i32(ox)            # add edx, ox
    code += b"\x89\x14\x24"                  # mov [esp], edx
    code += b"\x8b\x50\x04"                  # mov edx, [eax+4]
    code += b"\x81\xc2" + i32(oy)            # add edx, oy
    code += b"\x89\x54\x24\x04"              # mov [esp+4], edx
    code += b"\x8b\x50\x08"                  # mov edx, [eax+8]
    code += b"\x81\xc2" + i32(ox)            # add edx, ox
    code += b"\x89\x54\x24\x08"              # mov [esp+8], edx
    code += b"\x8b\x50\x0c"                  # mov edx, [eax+0xc]
    code += b"\x81\xc2" + i32(oy)            # add edx, oy
    code += b"\x89\x54\x24\x0c"              # mov [esp+0xc], edx
    code += b"\x89\xe0"                      # mov eax, esp
    call_orig_pos = len(code)
    code[je_clip_pos + 1] = call_orig_pos - (je_clip_pos + 2)

    # Call original RKC_UPDIB_VSPACKET::Render(dst_dib, shifted_clip)
    code += b"\x50"                          # push eax (clipRect)
    code += b"\xff\x74\x24\x20"              # push dword ptr [esp+0x20] (dst_dib)
    code += b"\x89\xf1"                      # mov ecx, esi
    code += call_rel32(cur_va(), orig_render_va)
    code += b"\x89\xc7"                      # mov edi, eax (save return value)

    # Restore packet fields (-ox, -oy) in case packet is inspected again
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

    code += b"\x89\xf8"                      # mov eax, edi
    code += b"\x83\xc4\x10"                  # add esp, 0x10
    code += b"\x5f\x5e"                      # pop edi; pop esi
    code += b"\xc2\x08\x00"                  # ret 8

    cave_off = cave_va - 0x10000000
    assert len(code) <= 0x200, f"RKC_UPDIB cave overflow: {len(code)}"
    data[cave_off : cave_off + len(code)] = code


def patch_shadowflare_exe(data: bytearray, width: int, height: int) -> None:
    """
    Patches ShadowFlare.exe for custom internal resolution (width x height):
    - Backbuffer & window creation dimensions
    - ScreenToClient coordinate shift (-ox, -oy) so HUD stays in 0..639, 0..479
    - Full-screen clip rect (-ox, -oy, width-1-ox, height-1-oy) for world rendering
    - Camera centering & side-panel camera offsets (left/right panels keep full screen live)
    - Cursor bounds & WM_SETCURSOR bounds
    - UI left/right click dispatchers & FUN_00445750 world hit-test for ultrawide side bands
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

    # 1. Expand .text VirtualSize from 0x71574 to 0x71a00 (RawSize is 0x72000)
    struct.pack_into("<I", data, 0x1e8, 0x71a00)

    # 2. Direct resolution constants (width x height)
    # RKC_DBFCONTROL::Initialize(height, width) at 0x401afc / 0x401b01
    write_va(0x401afd, i32(height))
    write_va(0x401b02, i32(width))

    # Loading screen DIB creation at 0x40295e / 0x402963
    write_va(0x40295f, i32(height))
    write_va(0x402964, i32(width))

    # Loading screen click box (shifted by +ox, +oy) in FUN_004028a0
    write_va(0x4028c9, i32(0x250 + ox))
    write_va(0x4028d0, i32(0x274 + ox))
    # Original uses cmp eax, imm32 or imm8; let's check original opcodes
    # At 0x4028da: 3d c2 01 00 00 (cmp eax, 0x1c2) and 0x4028e1: 3d d8 01 00 00 (cmp eax, 0x1d8)
    write_va(0x4028db, i32(0x1c2 + oy))
    write_va(0x4028e2, i32(0x1d8 + oy))

    # WM_SETCURSOR bounds (0..width-1, 0..height-1) at 0x402be0 / 0x402be8
    write_va(0x402be2, i32(width - 1))
    write_va(0x402be9, i32(height - 1))

    # 3. Code cave 0x472580: SetRect replacement for initial clip rect (0x401ad9)
    cave_setrect = 0x472580
    code_setrect = (
        b"\x8b\x44\x24\x04"                # mov eax, [esp+4] (LPRECT)
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
    # Used by cursor render (0x4087ce) and input polling (0x456311)
    cave_s2c = 0x4725b0
    code_s2c = (
        b"\xff\x74\x24\x08"                # push dword ptr [esp+8] (lpPoint)
        b"\xff\x74\x24\x08"                # push dword ptr [esp+8] (hWnd)
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
    # Checks (unsigned)(x + ox) <= width - 1 && (unsigned)(y + oy) <= height - 1
    # Note: original at 0x45636e is `mov eax, [esi+0x10]`
    # We have 24 bytes from 0x456371 to 0x456389 (target 0x45638a = clear key, 0x45638d = keep)
    if oy <= 127:
        bounds_code = (
            b"\x05" + i32(ox) +            # add eax, ox          (5B @ 456371)
            b"\x3d" + i32(width - 1) +     # cmp eax, width-1     (5B @ 456376)
            b"\x77\x0d"                    # ja 0x45638a          (2B @ 45637b)
            b"\x8b\x46\x14"                # mov eax, [esi+0x14]  (3B @ 45637d)
            b"\x83\xc0" + bytes([oy]) +    # add eax, oy          (3B @ 456380)
            b"\x3d" + i32(height - 1) +    # cmp eax, height-1    (5B @ 456383)
            b"\x76\x03"                    # jbe 0x45638d         (2B @ 456388)
        )
        assert len(bounds_code) == 25
        write_va(0x456371, bounds_code)
    else:
        # Use a small cave at 0x472980 when oy > 127 (e.g. 1280x720, 1920x1080)
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
        b"\xc7\x44\x24\x54" + i32(min_x) +     # mov [esp+0x54], min_x (caller [esp+0x50])
        b"\xc7\x44\x24\x58" + i32(min_y) +     # mov [esp+0x58], min_y
        b"\xc7\x44\x24\x5c" + i32(max_x) +     # mov [esp+0x5c], max_x
        b"\xc7\x44\x24\x60" + i32(max_y) +     # mov [esp+0x60], max_y
        b"\x8b\x0d\x18\x2d\x48\x00"            # mov ecx, [0x482d18] (restores original instr)
        b"\xc3"                                # ret
    )
    assert len(code_clip) <= 0x30
    write_va(cave_clip, code_clip)
    write_va(0x41ea11, call_rel32(0x41ea11, cave_clip) + b"\x90")

    # 7. Gameplay camera centering & side-panel offsets (0x41ea62..0x41eac0)
    # Normal / both panels: player centered horizontally (camera_x = player_x - width//2)
    # Right panel (eax==1): player shifted left by 320px (camera_x = player_x - (width//2 - 320))
    # Left panel (eax==0x10): player shifted right by 320px (camera_x = player_x - (width//2 + 320))
    # viewport_offset_x = min_x (-ox) in ALL cases so the full width renders every frame!
    # Vertical center: camera_y = player_y - (height//2 + 30) so player is centered above HUD bar
    cam_normal_x = -(width // 2)
    cam_rpanel_x = -(width // 2 - 320)
    cam_lpanel_x = -(width // 2 + 320)
    cam_y = -(height // 2 + 30)  # -300 (0xfffffed4) at height=540

    cam_code = bytearray()
    cam_code += b"\x83\xf8\x01"                  # cmp eax, 1 (right panel)
    cam_code += b"\x74\x11"                      # je rpanel
    cam_code += b"\x83\xf8\x10"                  # cmp eax, 0x10 (left panel)
    cam_code += b"\x74\x18"                      # je lpanel
    # normal / both:
    cam_code += b"\xb9" + i32(cam_normal_x)      # mov ecx, cam_normal_x
    cam_code += b"\xba" + i32(min_x)             # mov edx, min_x
    cam_code += b"\xeb\x16"                      # jmp apply
    # rpanel:
    cam_code += b"\xb9" + i32(cam_rpanel_x)      # mov ecx, cam_rpanel_x
    cam_code += b"\xba" + i32(min_x)             # mov edx, min_x
    cam_code += b"\xeb\x0a"                      # jmp apply
    # lpanel:
    cam_code += b"\xb9" + i32(cam_lpanel_x)      # mov ecx, cam_lpanel_x
    cam_code += b"\xba" + i32(min_x)             # mov edx, min_x
    # apply:
    cam_code += b"\x01\x0f"                      # add [edi], ecx
    cam_code += b"\x81\x06" + i32(cam_y)         # add [esi], cam_y
    cam_code += b"\x89\x95\x08\xa0\x00\x00"      # mov [ebp+0xa008], edx (viewport_offset_x)
    cam_code += b"\xc7\x85\x0c\xa0\x00\x00" + i32(min_y)  # mov [ebp+0xa00c], min_y
    cam_code += b"\xeb\x19"                      # jmp 0x41eac1
    pad_len = (0x41eac1 - 0x41ea62) - len(cam_code)
    assert pad_len >= 0, f"cam_code too big: {len(cam_code)}"
    cam_code += b"\x90" * pad_len
    write_va(0x41ea62, bytes(cam_code))

    # 8. Code cave 0x472600: FUN_00445750 (world click allowed hit-test)
    # Inside 0..639, y>=0 -> original FUN_00445750.
    # Outside 0..639 or y<0 -> allow world click unless a full-screen modal is open.
    cave_hit = 0x472600
    ch = bytearray()
    ch += b"\x8b\x44\x24\x04"                # mov eax, [esp+4] (x)
    ch += b"\x8b\x54\x24\x08"                # mov edx, [esp+8] (y)
    ch += b"\x83\xf8\x00"                    # cmp eax, 0
    ch += b"\x7c\x16"                        # jl outside
    ch += b"\x3d\x7f\x02\x00\x00"            # cmp eax, 639
    ch += b"\x7f\x0f"                        # jg outside
    ch += b"\x83\xfa\x00"                    # cmp edx, 0
    ch += b"\x7c\x0a"                        # jl outside
    ch += b"\x8b\x54\x24\x08"                # mov edx, [esp+8] (orig prologue)
    ch += b"\x56"                            # push esi
    ch += jmp_rel32(cave_hit + len(ch), 0x445755)
    # outside:
    ch += b"\x31\xc0"                        # xor eax, eax
    for modal_va, disp in [
        (0x48ce54, 0x25),
        (0x48ce6c, 0x1c),
        (0x48ce58, 0x13),
        (0x48ce5c, 0x0a),
        (0x48ce60, 0x01),
    ]:
        ch += b"\x83\x3d" + u32(modal_va) + b"\x01"  # cmp dword ptr [modal_va], 1
        ch += b"\x74" + bytes([disp])                # je ret0
    ch += b"\x40"                            # inc eax (return 1)
    ch += b"\xc2\x08\x00"                    # ret 8
    write_va(cave_hit, bytes(ch))
    write_va(0x445750, jmp_rel32(0x445750, cave_hit))

    # 9. Code caves 0x472900 & 0x472940: Guard UI left-click (FUN_004458c0)
    # and right-click (FUN_00445a40) dispatchers so side panels (Status,
    # Equipment, Skills, Shop, Storage) and bottom HUD never swallow clicks
    # in the ultrawide side/top bands outside 0..639, y>=0.
    cave_left = 0x472900
    cl = bytearray()
    cl += b"\x56"                            # push esi
    cl += b"\x89\xce"                        # mov esi, ecx
    cl += b"\x8b\x86\xfc\x03\x00\x00"        # mov eax, [esi+0x3fc]
    cl += b"\x83\x38\x00"                    # cmp dword ptr [eax], 0
    cl += b"\x7c\x18"                        # jl outside
    cl += b"\x81\x38\x7f\x02\x00\x00"        # cmp dword ptr [eax], 639
    cl += b"\x7f\x10"                        # jg outside
    cl += b"\x83\x78\x04\x00"                # cmp dword ptr [eax+4], 0
    cl += b"\x7c\x0a"                        # jl outside
    cl += call_rel32(cave_left + len(cl), 0x451cb0)
    cl += jmp_rel32(cave_left + len(cl), 0x4458c8)
    # outside:
    cl += b"\x31\xc0"                        # xor eax, eax
    cl += b"\x83\xbe\x24\x16\x00\x00\x01"    # cmp dword ptr [esi+0x1624], 1
    cl += b"\x0f\x94\xc0"                    # sete al
    cl += b"\x5e\xc3"                        # pop esi; ret
    write_va(cave_left, bytes(cl))
    write_va(0x4458c0, jmp_rel32(0x4458c0, cave_left) + b"\x90\x90\x90")

    cave_right = 0x472940
    cr = bytearray()
    cr += b"\x8b\x86\xfc\x03\x00\x00"        # mov eax, [esi+0x3fc]
    cr += b"\x83\x38\x00"                    # cmp dword ptr [eax], 0
    cr += b"\x7c\x1a"                        # jl outside
    cr += b"\x81\x38\x7f\x02\x00\x00"        # cmp dword ptr [eax], 639
    cr += b"\x7f\x12"                        # jg outside
    cr += b"\x83\x78\x04\x00"                # cmp dword ptr [eax+4], 0
    cr += b"\x7c\x0c"                        # jl outside
    cr += b"\x89\xf1"                        # mov ecx, esi
    cr += call_rel32(cave_right + len(cr), 0x451cb0)
    cr += jmp_rel32(cave_right + len(cr), 0x445a64)
    # outside:
    cr += b"\x31\xc0"                        # xor eax, eax
    cr += b"\x5e\xc3"                        # pop esi; ret
    write_va(cave_right, bytes(cr))
    write_va(0x445a5d, jmp_rel32(0x445a5d, cave_right) + b"\x90\x90")


def restore_backups(game_dir: Path) -> None:
    for fname in ("ShadowFlare.exe", "RKC_UPDIB.dll"):
        bak = game_dir / f"{fname}.orig.bak"
        dst = game_dir / fname
        if bak.exists():
            shutil.copy2(bak, dst)
            print(f"Restored {dst} from {bak.name}")
        else:
            print(f"No backup found for {fname} ({bak.name})")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Patch ShadowFlare Episode One for widescreen/ultrawide resolutions."
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
        "--restore",
        action="store_true",
        help="Restore original ShadowFlare.exe and RKC_UPDIB.dll from .orig.bak backups.",
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

    exe_path = game_dir / "ShadowFlare.exe"
    dll_path = game_dir / "RKC_UPDIB.dll"
    for p in (exe_path, dll_path):
        if not p.exists():
            print(f"Error: {p} not found.", file=sys.stderr)
            return 1

    # Always patch from clean original backup if one already exists
    exe_bak = game_dir / "ShadowFlare.exe.orig.bak"
    dll_bak = game_dir / "RKC_UPDIB.dll.orig.bak"

    if not exe_bak.exists():
        if not args.force and sha256_file(exe_path) != ORIG_EXE_SHA256:
            print(
                f"Error: ShadowFlare.exe SHA-256 mismatch.\n"
                f"  Expected: {ORIG_EXE_SHA256}\n"
                f"  Found:    {sha256_file(exe_path)}\n"
                f"Use --force to override or restore the original binary first.",
                file=sys.stderr,
            )
            return 1
        shutil.copy2(exe_path, exe_bak)
        print(f"Created backup: {exe_bak.name}")

    if not dll_bak.exists():
        if not args.force and sha256_file(dll_path) != ORIG_DLL_SHA256:
            print(
                f"Error: RKC_UPDIB.dll SHA-256 mismatch.\n"
                f"  Expected: {ORIG_DLL_SHA256}\n"
                f"  Found:    {sha256_file(dll_path)}\n"
                f"Use --force to override or restore the original binary first.",
                file=sys.stderr,
            )
            return 1
        shutil.copy2(dll_path, dll_bak)
        print(f"Created backup: {dll_bak.name}")

    exe_data = bytearray(exe_bak.read_bytes())
    dll_data = bytearray(dll_bak.read_bytes())

    patch_shadowflare_exe(exe_data, width, height)
    patch_rkc_updib(dll_data, width, height)

    exe_path.write_bytes(exe_data)
    dll_path.write_bytes(dll_data)

    print(f"Patched ShadowFlare.exe and RKC_UPDIB.dll for {width}x{height}!")
    print(f"  HUD offset: +{(width - 640) // 2}px X, +{height - 480}px Y")
    print("  Tip: Pair with cnc-ddraw (ddraw.dll + ddraw.ini) for crisp fullscreen scaling.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
