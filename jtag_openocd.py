import os
import re
import socket
import subprocess
import time
from typing import List, Optional

class OpenOcdProcess:
    def __init__(self, cfg_path: str, openocd_bin: str = "openocd"):
        self.cfg_path = cfg_path
        self.openocd_bin = openocd_bin
        self.proc: Optional[subprocess.Popen] = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            [self.openocd_bin, "-f", self.cfg_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        time.sleep(1.5)
        if self.proc.poll() is not None:
            out = self.proc.stdout.read() if self.proc.stdout else ""
            raise RuntimeError("OpenOCD no arrancó:\n" + out[-2000:])
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()

class OpenOcdTelnet:
    def __init__(self, host="127.0.0.1", port=4444, timeout=4):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = None

    def __enter__(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        time.sleep(0.2)
        self._read_available()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.cmd("exit")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass

    def _read_available(self) -> str:
        chunks = []
        end = time.time() + 0.35
        while time.time() < end:
            try:
                data = self.sock.recv(8192)
                if not data:
                    break
                chunks.append(data.decode(errors="ignore"))
                if b">" in data:
                    break
            except socket.timeout:
                break
        return "".join(chunks)

    def cmd(self, command: str, delay: float = 0.08) -> str:
        if not self.sock:
            raise RuntimeError("No conectado a OpenOCD")
        self.sock.sendall((command + "\n").encode())
        time.sleep(delay)
        out = self._read_available()
        if "invalid command" in out.lower() or "error:" in out.lower():
            # Do not raise for drscan values that contain words; caller may want output.
            pass
        return out

    def irscan(self, tap: str, opcode_bin: str) -> str:
        val = int(opcode_bin, 2)
        return self.cmd(f"irscan {tap} 0x{val:x}")

    def drscan(self, tap: str, bits: int, value: int = 0) -> str:
        return self.cmd(f"drscan {tap} {bits} 0x{value:x}", delay=0.12)

def parse_hex_from_output(out: str) -> int:
    xs = re.findall(r"0x[0-9a-fA-F]+|\b[0-9a-fA-F]{4,}\b", out)
    if not xs:
        return 0
    return int(xs[-1], 16)

def int_to_bits_lsb(value: int, length: int) -> List[int]:
    return [(value >> i) & 1 for i in range(length)]

def bits_lsb_to_int(bits: List[int]) -> int:
    v = 0
    for i, b in enumerate(bits):
        if b:
            v |= 1 << i
    return v
