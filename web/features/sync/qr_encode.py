"""Tiny pure-Python QR encoder (byte mode, versions 1–10, ECC M).

No third-party QR dependency — renders a PNG via Pillow (already required).
"""

from __future__ import annotations

import io

from PIL import Image

# GF(256) for Reed-Solomon
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for i in range(255):
    _EXP[i] = _x
    _LOG[_x] = i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for i in range(255, 512):
    _EXP[i] = _EXP[i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(nsym: int) -> list[int]:
    g = [1]
    for i in range(nsym):
        g = [0] + g
        for j in range(len(g) - 1):
            g[j] ^= _gf_mul(g[j + 1], _EXP[i])
    return g


def _rs_encode(data: list[int], nsym: int) -> list[int]:
    gen = _rs_generator(nsym)
    out = data + [0] * nsym
    for i in range(len(data)):
        coef = out[i]
        if coef:
            for j in range(len(gen)):
                out[i + j] ^= _gf_mul(gen[j], coef)
    return out[-nsym:]


# ECC codewords per block for ECC-M, versions 1-10
_ECC_CW = [0, 10, 16, 26, 18, 24, 18, 20, 24, 30, 18]
# (num blocks group1, data cw/block g1, num blocks g2, data cw/block g2)
_BLOCK_INFO = {
    1: (1, 16, 0, 0),
    2: (1, 28, 0, 0),
    3: (1, 44, 0, 0),
    4: (2, 32, 0, 0),
    5: (2, 43, 0, 0),
    6: (4, 27, 0, 0),
    7: (4, 31, 0, 0),
    8: (2, 38, 2, 39),
    9: (3, 36, 2, 37),
    10: (4, 36, 1, 37),
}

_CAPACITY = {
    1: 14, 2: 26, 3: 42, 4: 62, 5: 84,
    6: 106, 7: 122, 8: 152, 9: 180, 10: 213,
}


def _bits(value: int, length: int) -> list[int]:
    return [(value >> i) & 1 for i in range(length - 1, -1, -1)]


def _choose_version(nbytes: int) -> int:
    for version, capacity in _CAPACITY.items():
        if nbytes <= capacity:
            return version
    raise ValueError("payload too large for QR versions 1-10")


def _encode_data(payload: bytes, version: int) -> list[int]:
    nsym = _ECC_CW[version]
    b1, d1, b2, d2 = _BLOCK_INFO[version]
    total_data = b1 * d1 + b2 * d2
    bits: list[int] = []
    bits += [0, 1, 0, 0]  # byte mode
    bits += _bits(len(payload), 16 if version >= 10 else 8)
    for byte in payload:
        bits += _bits(byte, 8)
    # terminator
    bits += [0] * min(4, total_data * 8 - len(bits))
    while len(bits) % 8:
        bits.append(0)
    data = []
    for i in range(0, len(bits), 8):
        byte = 0
        for bit in bits[i : i + 8]:
            byte = (byte << 1) | bit
        data.append(byte)
    pad = 0
    while len(data) < total_data:
        data.append(0xEC if pad % 2 == 0 else 0x11)
        pad += 1
    data = data[:total_data]

    blocks: list[list[int]] = []
    ecc_blocks: list[list[int]] = []
    offset = 0
    for count, size in ((b1, d1), (b2, d2)):
        for _ in range(count):
            block = data[offset : offset + size]
            offset += size
            blocks.append(block)
            ecc_blocks.append(_rs_encode(block, nsym))

    interleaved: list[int] = []
    for i in range(max(len(b) for b in blocks)):
        for block in blocks:
            if i < len(block):
                interleaved.append(block[i])
    for i in range(nsym):
        for block in ecc_blocks:
            interleaved.append(block[i])
    return interleaved


def _module_size(version: int) -> int:
    return 21 + 4 * (version - 1)


def _place_finders(matrix: list[list[int]], reserved: list[list[int]]) -> None:
    size = len(matrix)

    def place(r0: int, c0: int) -> None:
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = r0 + r, c0 + c
                if not (0 <= rr < size and 0 <= cc < size):
                    continue
                reserved[rr][cc] = 1
                if 0 <= r <= 6 and 0 <= c <= 6:
                    dark = (
                        r in (0, 6)
                        or c in (0, 6)
                        or (2 <= r <= 4 and 2 <= c <= 4)
                    )
                    matrix[rr][cc] = 1 if dark else 0
                else:
                    matrix[rr][cc] = 0

    place(0, 0)
    place(0, size - 7)
    place(size - 7, 0)


def _place_timing(matrix: list[list[int]], reserved: list[list[int]]) -> None:
    size = len(matrix)
    for i in range(size):
        for r, c in ((6, i), (i, 6)):
            if reserved[r][c]:
                continue
            reserved[r][c] = 1
            matrix[r][c] = 1 if i % 2 == 0 else 0


def _place_dark_module(matrix: list[list[int]], reserved: list[list[int]], version: int) -> None:
    r, c = 4 * version + 9, 8
    reserved[r][c] = 1
    matrix[r][c] = 1


_ALIGNMENT = {
    2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}


def _place_alignments(matrix: list[list[int]], reserved: list[list[int]], version: int) -> None:
    positions = _ALIGNMENT.get(version)
    if not positions:
        return
    for r0 in positions:
        for c0 in positions:
            if reserved[r0][c0]:
                continue
            for r in range(-2, 3):
                for c in range(-2, 3):
                    rr, cc = r0 + r, c0 + c
                    reserved[rr][cc] = 1
                    dark = abs(r) == 2 or abs(c) == 2 or (r == 0 and c == 0)
                    matrix[rr][cc] = 1 if dark else 0


def _reserve_format(reserved: list[list[int]]) -> None:
    size = len(reserved)
    for i in range(9):
        reserved[8][i] = 1
        reserved[i][8] = 1
    for i in range(8):
        reserved[8][size - 1 - i] = 1
        reserved[size - 1 - i][8] = 1


def _place_data(matrix: list[list[int]], reserved: list[list[int]], data: list[int]) -> None:
    size = len(matrix)
    bits: list[int] = []
    for byte in data:
        bits.extend(_bits(byte, 8))
    bit_i = 0
    upward = True
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if reserved[row][c]:
                    continue
                matrix[row][c] = bits[bit_i] if bit_i < len(bits) else 0
                bit_i += 1
        upward = not upward
        col -= 2


def _mask_fn(mask: int, r: int, c: int) -> bool:
    if mask == 0:
        return (r + c) % 2 == 0
    if mask == 1:
        return r % 2 == 0
    if mask == 2:
        return c % 3 == 0
    if mask == 3:
        return (r + c) % 3 == 0
    if mask == 4:
        return (r // 2 + c // 3) % 2 == 0
    if mask == 5:
        return (r * c) % 2 + (r * c) % 3 == 0
    if mask == 6:
        return ((r * c) % 2 + (r * c) % 3) % 2 == 0
    return ((r + c) % 2 + (r * c) % 3) % 2 == 0


def _apply_mask(matrix: list[list[int]], reserved: list[list[int]], mask: int) -> list[list[int]]:
    size = len(matrix)
    out = [row[:] for row in matrix]
    for r in range(size):
        for c in range(size):
            if reserved[r][c]:
                continue
            if _mask_fn(mask, r, c):
                out[r][c] ^= 1
    return out


def _format_bits(mask: int) -> list[int]:
    # ECC-M = 00, combined with BCH
    data = (0b00 << 3) | mask
    rem = data << 10
    for i in range(4, -1, -1):
        if rem & (1 << (i + 10)):
            rem ^= 0b10100110111 << i
    bits = (data << 10 | rem) ^ 0b101010000010010
    return _bits(bits, 15)


def _place_format(matrix: list[list[int]], mask: int) -> None:
    bits = _format_bits(mask)
    size = len(matrix)
    coords_a = [
        (8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
        (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8),
    ]
    coords_b = [
        (size - 1, 8), (size - 2, 8), (size - 3, 8), (size - 4, 8),
        (size - 5, 8), (size - 6, 8), (size - 7, 8),
        (8, size - 8), (8, size - 7), (8, size - 6), (8, size - 5),
        (8, size - 4), (8, size - 3), (8, size - 2), (8, size - 1),
    ]
    for bit, (r, c) in zip(bits, coords_a):
        matrix[r][c] = bit
    for bit, (r, c) in zip(bits, coords_b):
        matrix[r][c] = bit


def _penalty(matrix: list[list[int]]) -> int:
    size = len(matrix)
    score = 0
    # N1: runs
    for rows in (matrix, list(zip(*matrix))):
        for row in rows:
            run = 1
            for i in range(1, size):
                if row[i] == row[i - 1]:
                    run += 1
                else:
                    if run >= 5:
                        score += run - 2
                    run = 1
            if run >= 5:
                score += run - 2
    # N2: 2x2 blocks
    for r in range(size - 1):
        for c in range(size - 1):
            v = matrix[r][c]
            if v == matrix[r][c + 1] == matrix[r + 1][c] == matrix[r + 1][c + 1]:
                score += 3
    # N3: finder-like
    pattern = (1, 0, 1, 1, 1, 0, 1)
    for rows in (matrix, [list(col) for col in zip(*matrix)]):
        for row in rows:
            for i in range(size - 6):
                if tuple(row[i : i + 7]) == pattern:
                    score += 40
    # N4: balance
    dark = sum(sum(row) for row in matrix)
    ratio = abs(100 * dark / (size * size) - 50) // 5
    score += int(ratio) * 10
    return score


def encode_matrix(payload: str | bytes) -> list[list[int]]:
    data = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    version = _choose_version(len(data))
    codewords = _encode_data(data, version)
    size = _module_size(version)
    matrix = [[0] * size for _ in range(size)]
    reserved = [[0] * size for _ in range(size)]
    _place_finders(matrix, reserved)
    _place_alignments(matrix, reserved, version)
    _place_timing(matrix, reserved)
    _place_dark_module(matrix, reserved, version)
    _reserve_format(reserved)
    _place_data(matrix, reserved, codewords)

    best = None
    best_score = None
    for mask in range(8):
        masked = _apply_mask(matrix, reserved, mask)
        _place_format(masked, mask)
        score = _penalty(masked)
        if best_score is None or score < best_score:
            best_score = score
            best = masked
    assert best is not None
    return best


def encode_png(payload: str | bytes, *, scale: int = 8, border: int = 4) -> bytes:
    matrix = encode_matrix(payload)
    size = len(matrix)
    dim = (size + border * 2) * scale
    image = Image.new("L", (dim, dim), 255)
    pixels = image.load()
    assert pixels is not None
    for r, row in enumerate(matrix):
        for c, dark in enumerate(row):
            if not dark:
                continue
            y0 = (r + border) * scale
            x0 = (c + border) * scale
            for y in range(y0, y0 + scale):
                for x in range(x0, x0 + scale):
                    pixels[x, y] = 0
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
