"""Audio temporal cifrado.

Mientras dura la reunión, el audio se escribe en disco cifrado con AES-256-GCM por bloques. La llave se
genera al empezar cada grabación y solo vive en la memoria del proceso: si la app se cierra de golpe,
los archivos que queden en %TEMP% no se pueden leer con nada.

Para transcribir, EncryptedAudio presenta el archivo como un WAV normal que se descifra bloque por
bloque conforme se lee, así nunca hay una copia completa del audio descifrado ni en disco ni en memoria.
"""

import io
import os
import struct
from bisect import bisect_right

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"MINUTA-AUDIO-1\n"
_HEADER = struct.Struct("<HHI")  # canales, bytes por muestra, frecuencia
_RECORD = struct.Struct("<I")  # largo del bloque cifrado
NONCE_BYTES = 12
TAG_BYTES = 16


def new_key() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def _aad(index: int) -> bytes:
    # El número de bloque va autenticado: no se pueden reordenar ni quitar bloques sin que falle el descifrado
    return MAGIC + index.to_bytes(8, "little")


class EncryptedWriter:
    def __init__(self, path: str, key: bytes, channels: int, sampwidth: int, rate: int):
        self._aes = AESGCM(key)
        self._f = open(path, "wb")
        self._f.write(MAGIC + _HEADER.pack(channels, sampwidth, rate))
        self._index = 0

    def write(self, data: bytes) -> None:
        if not data:
            return
        nonce = os.urandom(NONCE_BYTES)
        ct = self._aes.encrypt(nonce, data, _aad(self._index))
        self._f.write(_RECORD.pack(len(ct)) + nonce + ct)
        self._index += 1

    def close(self) -> None:
        if not self._f.closed:
            self._f.close()


class EncryptedAudio(io.RawIOBase):
    """Archivo cifrado visto como un WAV de solo lectura (con seek), para pasarlo a faster-whisper."""

    def __init__(self, path: str, key: bytes):
        self.path = path
        self._aes = AESGCM(key)
        self._f = open(path, "rb")
        if self._f.read(len(MAGIC)) != MAGIC:
            raise ValueError("No es un audio cifrado de Minutas")
        self.channels, self.sampwidth, self.rate = _HEADER.unpack(self._f.read(_HEADER.size))

        # Índice de bloques: posición en el audio descifrado -> posición en el archivo
        self._starts: list[int] = []
        self._blocks: list[tuple[int, int]] = []  # (posición en archivo, largo cifrado)
        data_size = 0
        while head := self._f.read(_RECORD.size):
            (ct_len,) = _RECORD.unpack(head)
            offset = self._f.tell()
            self._f.seek(NONCE_BYTES + ct_len, io.SEEK_CUR)
            self._starts.append(data_size)
            self._blocks.append((offset, ct_len))
            data_size += ct_len - TAG_BYTES
        self.data_size = data_size

        block_align = self.channels * self.sampwidth
        self._wav_header = (
            b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, self.channels, self.rate, self.rate * block_align, block_align, self.sampwidth * 8)
            + b"data" + struct.pack("<I", data_size)
        )
        self._size = len(self._wav_header) + data_size
        self._pos = 0
        self._cache: tuple[int, bytes] | None = None

    @property
    def seconds(self) -> float:
        return self.data_size / (self.channels * self.sampwidth * self.rate)

    def _block(self, i: int) -> bytes:
        if self._cache and self._cache[0] == i:
            return self._cache[1]
        offset, ct_len = self._blocks[i]
        self._f.seek(offset)
        nonce = self._f.read(NONCE_BYTES)
        plain = self._aes.decrypt(nonce, self._f.read(ct_len), _aad(i))
        self._cache = (i, plain)
        return plain

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self._size}[whence]
        self._pos = max(0, base + offset)
        return self._pos

    def readinto(self, buffer) -> int:
        view = memoryview(buffer).cast("B")
        written = 0
        hl = len(self._wav_header)
        while written < len(view) and self._pos < self._size:
            if self._pos < hl:
                chunk = self._wav_header[self._pos : hl]
            else:
                rel = self._pos - hl
                i = bisect_right(self._starts, rel) - 1
                chunk = self._block(i)[rel - self._starts[i] :]
            n = min(len(chunk), len(view) - written)
            view[written : written + n] = chunk[:n]
            written += n
            self._pos += n
        return written

    def close(self) -> None:
        self._f.close()
        self._cache = None
        super().close()
