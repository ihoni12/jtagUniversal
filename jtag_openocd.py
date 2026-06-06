import socket
import time
from typing import List

class OpenOcdTelnet:
    def __init__(self, host="127.0.0.1", port=4444, timeout=3):
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
        end = time.time() + 0.25
        while time.time() < end:
            try:
                data = self.sock.recv(4096)
                if not data:
                    break
                chunks.append(data.decode(errors="ignore"))
                if b">" in data:
                    break
            except socket.timeout:
                break
        return "".join(chunks)

    def cmd(self, command: str) -> str:
        if not self.sock:
            raise RuntimeError("Not connected to OpenOCD")
        self.sock.sendall((command + "\n").encode())
        time.sleep(0.1)
        return self._read_available()

    def irscan(self, tap: str, opcode_bin: str) -> str:
        val = int(opcode_bin, 2)
        return self.cmd(f"irscan {tap} 0x{val:x}")

    def drscan(self, tap: str, bits: int, value: int = 0) -> str:
        return self.cmd(f"drscan {tap} {bits} 0x{value:x}")


def parse_hex_from_output(out: str) -> int:
    import re
    xs = re.findall(r"0x[0-9a-fA-F]+|\b[0-9a-fA-F]{4,}\b", out)
    if not xs:
        return 0
    return int(xs[-1], 16)


def int_to_bits_lsb(value: int, length: int) -> List[int]:
    return [(value >> i) & 1 for i in range(length)]
