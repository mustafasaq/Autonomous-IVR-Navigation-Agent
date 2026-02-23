import base64
import audioop
from typing import Iterator


def twilio_ulaw_b64_to_pcm16(payload_b64: str) -> bytes:
    ulaw = base64.b64decode(payload_b64)
    pcm16 = audioop.ulaw2lin(ulaw, 2)
    return pcm16


def rms_energy(pcm16: bytes) -> float:
    if not pcm16:
        return 0.0
    return float(audioop.rms(pcm16, 2))


def chunk_bytes(b: bytes, n: int) -> Iterator[bytes]:
    for i in range(0, len(b), n):
        yield b[i : i + n]
