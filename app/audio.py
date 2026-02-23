import base64
from typing import Iterator
import numpy as np


def twilio_ulaw_b64_to_pcm16(payload_b64: str) -> bytes:
    ulaw = base64.b64decode(payload_b64)
    if not ulaw:
        return b""

    u = np.frombuffer(ulaw, dtype=np.uint8)
    u = np.bitwise_xor(u, 0xFF)

    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F

    sample = ((mantissa.astype(np.int32) << 3) + 0x84) << exponent
    sample = sample - 0x84
    sample = np.where(sign != 0, -sample, sample)

    return sample.astype(np.int16).tobytes()


def rms_energy(pcm16: bytes) -> float:
    if not pcm16:
        return 0.0
    s = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)
    if s.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(s * s)))


def chunk_bytes(b: bytes, n: int) -> Iterator[bytes]:
    for i in range(0, len(b), n):
        yield b[i : i + n]
