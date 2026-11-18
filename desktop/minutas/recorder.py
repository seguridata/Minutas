"""Grabación del audio del sistema (lo que dicen los demás) y del micrófono (tu voz) en Windows.

Usa WASAPI loopback, así funciona igual con Meet, Teams o Zoom, en navegador o app de escritorio.
"""

import os
import shutil
import tempfile
import threading
import time

import pyaudiowpatch as pyaudio

from .secure_audio import EncryptedAudio, EncryptedWriter, new_key

TEMP_PREFIX = "minuta_"


class EncryptedSource:
    """Pista grabada: el archivo cifrado y la llave en memoria para leerlo."""

    def __init__(self, path: str, key: bytes):
        self.path, self._key = path, key

    def open(self) -> EncryptedAudio:
        return EncryptedAudio(self.path, self._key)


class _Track:
    """Escribe el audio cifrado y rellena con silencio los huecos.

    El loopback de WASAPI no entrega datos mientras nadie habla, así que sin relleno
    las pistas de sistema y micrófono quedarían desfasadas al mezclarlas.
    """

    def __init__(self, path: str, key: bytes, channels: int, rate: int, sampwidth: int):
        self.channels, self.rate, self.sampwidth = channels, rate, sampwidth
        self.frame_bytes = channels * sampwidth
        self.wav = EncryptedWriter(path, key, channels, sampwidth, rate)
        self.frames_written = 0
        self.t0: float | None = None
        self.lock = threading.Lock()

    def start(self, t0: float) -> None:
        self.t0 = t0

    def write(self, data: bytes) -> None:
        with self.lock:
            if self.t0 is None:
                return
            n = len(data) // self.frame_bytes
            expected = int((time.monotonic() - self.t0) * self.rate) - n
            gap = expected - self.frames_written
            if gap > self.rate // 10:  # más de 100 ms de hueco
                for start in range(0, gap, self.rate):  # en bloques de 1 s para no descifrar bloques enormes
                    self.wav.write(b"\x00" * min(self.rate, gap - start) * self.frame_bytes)
                self.frames_written += gap
            self.wav.write(data)
            self.frames_written += n

    def close(self) -> None:
        with self.lock:
            self.t0 = None
            self.wav.close()


def cleanup_orphans(older_than_hours: float = 6) -> int:
    """Borra audio de grabaciones que no terminaron (la app se cerró de golpe). Ya no se puede descifrar:
    la llave vivía solo en memoria. Se respetan las carpetas recientes por si otra ventana está grabando."""
    removed = 0
    limit = time.time() - older_than_hours * 3600
    root = tempfile.gettempdir()
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if not name.startswith(TEMP_PREFIX) or not os.path.isdir(path):
            continue
        try:
            newest = max([os.path.getmtime(path)] + [os.path.getmtime(os.path.join(path, f)) for f in os.listdir(path)])
            if newest < limit:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            pass
    return removed


class Recorder:
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix=TEMP_PREFIX)
        self._key = new_key()  # solo en memoria; no se guarda en ningún lado
        self.system_path = os.path.join(self.dir, "sistema.audio")
        self.mic_path = os.path.join(self.dir, "microfono.audio")
        self._pa = pyaudio.PyAudio()
        self._streams: list = []
        self._tracks: list[_Track] = []

    def _loopback_device(self) -> dict:
        wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        speakers = self._pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if speakers.get("isLoopbackDevice"):
            return speakers
        for dev in self._pa.get_loopback_device_info_generator():
            if speakers["name"] in dev["name"]:
                return dev
        raise RuntimeError("No se encontró el dispositivo de loopback de las bocinas/audífonos")

    def _mic_device(self) -> dict:
        wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        if wasapi["defaultInputDevice"] < 0:
            raise RuntimeError("No se encontró un micrófono")
        return self._pa.get_device_info_by_index(wasapi["defaultInputDevice"])

    def _open(self, device: dict, path: str) -> None:
        channels = min(int(device["maxInputChannels"]), 2) or 1
        rate = int(device["defaultSampleRate"])
        track = _Track(path, self._key, channels, rate, self._pa.get_sample_size(pyaudio.paInt16))

        def callback(in_data, frame_count, time_info, status):
            track.write(in_data)
            return (None, pyaudio.paContinue)

        stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=device["index"],
            frames_per_buffer=int(rate * 0.1),
            stream_callback=callback,
        )
        self._tracks.append(track)
        self._streams.append(stream)

    def start(self) -> None:
        self._open(self._loopback_device(), self.system_path)
        self._open(self._mic_device(), self.mic_path)
        t0 = time.monotonic()
        for track in self._tracks:
            track.start(t0)

    def stop(self) -> list[EncryptedSource]:
        for stream in self._streams:
            stream.stop_stream()
            stream.close()
        for track in self._tracks:
            track.close()
        self._pa.terminate()
        return [EncryptedSource(self.system_path, self._key), EncryptedSource(self.mic_path, self._key)]

    def discard(self) -> None:
        """Borra el audio grabado. Se llama siempre al terminar o cancelar."""
        for path in (self.system_path, self.mic_path):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        try:
            os.rmdir(self.dir)
        except OSError:
            pass
        self._key = b""  # se descarta la llave
