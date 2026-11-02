"""
qrgen.py - tiny, dependency-free QR Code encoder (ISO/IEC 18004, byte mode).

Supports versions 1-40 and all four error-correction levels, with automatic
mask selection. Outputs a boolean matrix, an SVG string, or terminal text.
Algorithm follows the public reference design by Project Nayuki (MIT).
"""

from __future__ import annotations

__all__ = ["QrCode", "encode", "to_svg", "to_terminal"]

# ECC level indices: L=0, M=1, Q=2, H=3
_ECL = {"L": 0, "M": 1, "Q": 2, "H": 3}
_FORMAT_BITS = (1, 0, 3, 2)

_ECC_PER_BLOCK = (
    (-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),
    (-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
)
_NUM_BLOCKS = (
    (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),
    (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),
    (-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),
    (-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81),
)


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z & 0xFF


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _raw_modules(ver: int) -> int:
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        n = ver // 7 + 2
        result -= (25 * n - 10) * n - 55
        if ver >= 7:
            result -= 36
    return result


def _data_codewords(ver: int, ecl: int) -> int:
    return _raw_modules(ver) // 8 - _ECC_PER_BLOCK[ecl][ver] * _NUM_BLOCKS[ecl][ver]


class QrCode:
    def __init__(self, ver: int, ecl: int, codewords: list[int], mask: int = -1):
        self.version = ver
        self.ecl = ecl
        self.size = ver * 4 + 17
        n = self.size
        self.modules = [[False] * n for _ in range(n)]
        self._func = [[False] * n for _ in range(n)]
        self._draw_function_patterns()
        self._draw_codewords(self._add_ecc_and_interleave(codewords))
        if mask == -1:
            best, best_penalty = 0, None
            for m in range(8):
                self._apply_mask(m)
                self._draw_format(m)
                p = self._penalty()
                if best_penalty is None or p < best_penalty:
                    best, best_penalty = m, p
                self._apply_mask(m)  # undo (XOR)
            mask = best
        self.mask = mask
        self._apply_mask(mask)
        self._draw_format(mask)
        del self._func

    # -- function patterns -------------------------------------------------
    def _set(self, x: int, y: int, dark: bool) -> None:
        self.modules[y][x] = dark
        self._func[y][x] = True

    def _draw_function_patterns(self) -> None:
        n = self.size
        for i in range(n):
            self._set(6, i, i % 2 == 0)
            self._set(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        self._set(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = self._alignment_positions()
        last = len(pos) - 1
        for i, a in enumerate(pos):
            for j, b in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self._set(a + dx, b + dy, max(abs(dx), abs(dy)) != 1)
        self._draw_format(0)  # reserve area
        self._draw_version()

    def _alignment_positions(self) -> list[int]:
        ver = self.version
        if ver == 1:
            return []
        n = ver // 7 + 2
        step = 26 if ver == 32 else (ver * 4 + n * 2 + 1) // (n * 2 - 2) * 2
        result = [6]
        p = self.size - 7
        while len(result) < n:
            result.insert(1, p)
            p -= step
        return result

    def _draw_format(self, mask: int) -> None:
        data = _FORMAT_BITS[self.ecl] << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1 != 0
        n = self.size
        for i in range(6):
            self._set(8, i, bit(i))
        self._set(8, 7, bit(6))
        self._set(8, 8, bit(7))
        self._set(7, 8, bit(8))
        for i in range(9, 15):
            self._set(14 - i, 8, bit(i))
        for i in range(8):
            self._set(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self._set(8, n - 15 + i, bit(i))
        self._set(8, n - 8, True)

    def _draw_version(self) -> None:
        if self.version < 7:
            return
        rem = self.version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = self.version << 12 | rem
        for i in range(18):
            dark = (bits >> i) & 1 != 0
            a, b = self.size - 11 + i % 3, i // 3
            self._set(a, b, dark)
            self._set(b, a, dark)

    # -- data --------------------------------------------------------------
    def _add_ecc_and_interleave(self, data: list[int]) -> list[int]:
        ver, ecl = self.version, self.ecl
        num_blocks = _NUM_BLOCKS[ecl][ver]
        ecc_len = _ECC_PER_BLOCK[ecl][ver]
        raw = _raw_modules(ver) // 8
        num_short = num_blocks - raw % num_blocks
        short_len = raw // num_blocks
        div = _rs_divisor(ecc_len)
        blocks, k = [], 0
        for i in range(num_blocks):
            take = short_len - ecc_len + (0 if i < num_short else 1)
            dat = data[k:k + take]
            k += take
            ecc = _rs_remainder(dat, div)
            if i < num_short:
                dat.append(0)
            blocks.append(dat + ecc)
        out = []
        for i in range(len(blocks[0])):
            for j, blk in enumerate(blocks):
                if i != short_len - ecc_len or j >= num_short:
                    out.append(blk[i])
        return out

    def _draw_codewords(self, data: list[int]) -> None:
        n, i, total = self.size, 0, len(data) * 8
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = n - 1 - vert if upward else vert
                    if not self._func[y][x] and i < total:
                        self.modules[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 != 0
                        i += 1
            right -= 2

    def _apply_mask(self, mask: int) -> None:
        fns = (
            lambda x, y: (x + y) % 2 == 0,
            lambda x, y: y % 2 == 0,
            lambda x, y: x % 3 == 0,
            lambda x, y: (x + y) % 3 == 0,
            lambda x, y: (x // 3 + y // 2) % 2 == 0,
            lambda x, y: x * y % 2 + x * y % 3 == 0,
            lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
            lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
        )
        f = fns[mask]
        for y in range(self.size):
            row, frow = self.modules[y], self._func[y]
            for x in range(self.size):
                if not frow[x] and f(x, y):
                    row[x] = not row[x]

    def _penalty(self) -> int:
        n, m = self.size, self.modules
        score = 0
        lines = [m[y] for y in range(n)] + [[m[y][x] for y in range(n)] for x in range(n)]
        pat1 = [True, False, True, True, True, False, True, False, False, False, False]
        pat2 = pat1[::-1]
        for line in lines:
            run, prev = 0, None
            for v in line:
                if v == prev:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + (run - 5)
                    run, prev = 1, v
            if run >= 5:
                score += 3 + (run - 5)
            for i in range(n - 10):
                seg = line[i:i + 11]
                if seg == pat1 or seg == pat2:
                    score += 40
        for y in range(n - 1):
            for x in range(n - 1):
                c = m[y][x]
                if c == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    score += 3
        dark = sum(sum(r) for r in m)
        total = n * n
        k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
        score += max(k, 0) * 10
        return score


def encode(text: str, ecc: str = "M", boost: bool = True) -> QrCode:
    data = text.encode("utf-8")
    ecl = _ECL[ecc.upper()]
    for ver in range(1, 41):
        cc_bits = 8 if ver <= 9 else 16
        used = 4 + cc_bits + len(data) * 8
        if used <= _data_codewords(ver, ecl) * 8:
            break
    else:
        raise ValueError("Data too long for a QR code")
    if boost:
        for e in range(ecl + 1, 4):
            if used <= _data_codewords(ver, e) * 8:
                ecl = e
    bits: list[int] = []
    def put(val: int, length: int) -> None:
        bits.extend((val >> i) & 1 for i in range(length - 1, -1, -1))
    put(0b0100, 4)
    put(len(data), cc_bits)
    for b in data:
        put(b, 8)
    cap = _data_codewords(ver, ecl) * 8
    put(0, min(4, cap - len(bits)))
    put(0, (-len(bits)) % 8)
    pad = 0xEC
    while len(bits) < cap:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    return QrCode(ver, ecl, words)


def to_svg(qr: QrCode, border: int = 4, dark: str = "#0b1f3a", light: str = "#ffffff") -> str:
    n = qr.size + border * 2
    parts = []
    for y in range(qr.size):
        for x in range(qr.size):
            if qr.modules[y][x]:
                parts.append(f"M{x + border},{y + border}h1v1h-1z")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" '
        f'shape-rendering="crispEdges" role="img" aria-label="QR code">'
        f'<rect width="100%" height="100%" fill="{light}"/>'
        f'<path d="{"".join(parts)}" fill="{dark}"/></svg>'
    )


def to_terminal(qr: QrCode, border: int = 2) -> str:
    """Render using half-block characters (2 modules per text row).
    Light-on-dark friendly: dark modules are printed as spaces on a white field."""
    n = qr.size
    get = lambda x, y: 0 <= x < n and 0 <= y < n and qr.modules[y][x]
    rows = []
    for y in range(-border, n + border, 2):
        line = []
        for x in range(-border, n + border):
            top, bot = get(x, y), get(x, y + 1)
            # inverted: light module -> block, dark module -> space
            line.append({(False, False): "█", (True, False): "▄",
                         (False, True): "▀", (True, True): " "}[(top, bot)])
        rows.append("".join(line))
    return "\n".join(rows)
