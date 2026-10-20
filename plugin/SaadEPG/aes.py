# -*- coding: utf-8 -*-
# Minimal AES-CBC (PKCS7) used to talk to the OSN schedule API.
# Uses pycryptodome / pycrypto when the image has it, otherwise a pure-Python fallback.
from __future__ import absolute_import
import base64

try:
    from Cryptodome.Cipher import AES as _AES  # pycryptodomex
except Exception:
    try:
        from Crypto.Cipher import AES as _AES  # pycryptodome / pycrypto
    except Exception:
        _AES = None


def _b(x):
    return bytearray(x)


# ---------------------------------------------------------------- pure python
_SBOX = None
_INV = None


def _init_tables():
    global _SBOX, _INV
    if _SBOX is not None:
        return
    sbox = [0] * 256
    inv = [0] * 256
    p = q = 1
    while True:
        # multiply p by 3
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        # divide q by 3
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ (((q << 1) | (q >> 7)) & 0xFF) ^ (((q << 2) | (q >> 6)) & 0xFF) \
            ^ (((q << 3) | (q >> 5)) & 0xFF) ^ (((q << 4) | (q >> 4)) & 0xFF)
        x = (x ^ 0x63) & 0xFF
        sbox[p] = x
        inv[x] = p
        if p == 1:
            break
    sbox[0] = 0x63
    inv[0x63] = 0
    _SBOX, _INV = sbox, inv


def _xt(a):
    a <<= 1
    if a & 0x100:
        a ^= 0x11B
    return a


def _mul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xt(a)
        b >>= 1
    return r


def _expand(key):
    _init_tables()
    nk = len(key) // 4
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    rcon = 1
    for i in range(nk, 4 * (nr + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= rcon
            rcon = _xt(rcon) & 0xFF
        elif nk > 6 and i % nk == 4:
            t = [_SBOX[b] for b in t]
        w.append([w[i - nk][j] ^ t[j] for j in range(4)])
    keys = []
    for r in range(nr + 1):
        k = []
        for c in range(4):
            k.extend(w[4 * r + c])
        keys.append(k)
    return keys


def _enc_block(s, keys):
    s = [s[i] ^ keys[0][i] for i in range(16)]
    nr = len(keys) - 1
    for r in range(1, nr + 1):
        s = [_SBOX[b] for b in s]
        s = [s[(i + 4 * (i % 4)) % 16] for i in range(16)]  # shift rows
        if r != nr:
            m = []
            for c in range(4):
                a = s[4 * c:4 * c + 4]
                m.extend([
                    _mul(a[0], 2) ^ _mul(a[1], 3) ^ a[2] ^ a[3],
                    a[0] ^ _mul(a[1], 2) ^ _mul(a[2], 3) ^ a[3],
                    a[0] ^ a[1] ^ _mul(a[2], 2) ^ _mul(a[3], 3),
                    _mul(a[0], 3) ^ a[1] ^ a[2] ^ _mul(a[3], 2)])
            s = m
        s = [s[i] ^ keys[r][i] for i in range(16)]
    return s


_M = None


def _dec_block(s, keys):
    global _M
    if _M is None:
        _M = [[_mul(x, k) for x in range(256)] for k in (9, 11, 13, 14)]
    m9, m11, m13, m14 = _M
    nr = len(keys) - 1
    s = [s[i] ^ keys[nr][i] for i in range(16)]
    for r in range(nr - 1, -1, -1):
        s = [s[(i - 4 * (i % 4)) % 16] for i in range(16)]  # inverse shift rows
        s = [_INV[b] for b in s]
        s = [s[i] ^ keys[r][i] for i in range(16)]
        if r != 0:
            m = []
            for c in range(4):
                a = s[4 * c:4 * c + 4]
                m.extend([
                    m14[a[0]] ^ m11[a[1]] ^ m13[a[2]] ^ m9[a[3]],
                    m9[a[0]] ^ m14[a[1]] ^ m11[a[2]] ^ m13[a[3]],
                    m13[a[0]] ^ m9[a[1]] ^ m14[a[2]] ^ m11[a[3]],
                    m11[a[0]] ^ m13[a[1]] ^ m9[a[2]] ^ m14[a[3]]])
            s = m
    return s


def _pad(data):
    n = 16 - len(data) % 16
    return data + bytearray([n] * n)


def _unpad(data):
    n = data[-1]
    if 1 <= n <= 16:
        return data[:-n]
    return data


def encrypt_b64(text, key, iv):
    data = text.encode('utf-8') if not isinstance(text, (bytes, bytearray)) else text
    if _AES is not None:
        c = _AES.new(bytes(key), _AES.MODE_CBC, bytes(iv))
        out = c.encrypt(bytes(_pad(bytearray(data))))
    else:
        keys = _expand(_b(key))
        prev = list(_b(iv))
        data = _pad(bytearray(data))
        res = bytearray()
        for i in range(0, len(data), 16):
            blk = [data[i + j] ^ prev[j] for j in range(16)]
            prev = _enc_block(blk, keys)
            res.extend(prev)
        out = bytes(res)
    return base64.b64encode(out).decode('ascii')


def _openssl_decrypt(raw, key, iv):
    """Fast path through the openssl binary that most images ship."""
    import subprocess
    import binascii
    p = subprocess.Popen(['openssl', 'enc', '-d', '-aes-256-cbc' if len(key) == 32 else '-aes-128-cbc',
                          '-K', binascii.hexlify(bytes(key)).decode('ascii'),
                          '-iv', binascii.hexlify(bytes(iv)).decode('ascii')],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, _err = p.communicate(bytes(raw))
    if p.returncode != 0:
        raise ValueError('openssl failed')
    return out


def decrypt_b64(b64, key, iv):
    raw = bytearray(base64.b64decode(b64))
    if _AES is None and len(raw) > 4096:
        try:
            return _openssl_decrypt(raw, key, iv).decode('utf-8', 'replace')
        except Exception:
            pass
    if _AES is not None:
        c = _AES.new(bytes(key), _AES.MODE_CBC, bytes(iv))
        out = bytearray(c.decrypt(bytes(raw)))
    else:
        keys = _expand(_b(key))
        prev = list(_b(iv))
        out = bytearray()
        for i in range(0, len(raw), 16):
            blk = list(raw[i:i + 16])
            d = _dec_block(blk, keys)
            out.extend([d[j] ^ prev[j] for j in range(16)])
            prev = blk
    return bytes(_unpad(out)).decode('utf-8', 'replace')
