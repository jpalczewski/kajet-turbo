"""Fake OpenAI-compatible embeddings endpoint for local runtime verification.

Serves POST /v1/embeddings with deterministic vectors derived from a hash of
each input, after an artificial delay so deferred embedding jobs are
observable. Binds an ephemeral port by default and prints the chosen port as
the first stdout line, so parallel sessions never collide on a fixed port.

Usage: python3 fake_embed.py [--port N] [--dim N] [--delay SECONDS]
"""

import argparse
import hashlib
import json
import struct
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def embed(text: str, dim: int) -> list[float]:
    # Stretch a SHA-256 digest into `dim` floats in [-1, 1]; identical input
    # always yields an identical vector.
    raw = b""
    counter = 0
    while len(raw) < dim * 4:
        raw += hashlib.sha256(f"{counter}:{text}".encode()).digest()
        counter += 1
    ints = struct.unpack(f"<{dim}I", raw[: dim * 4])
    return [i / 0xFFFFFFFF * 2 - 1 for i in ints]


def make_handler(dim: int, delay: float) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path.rstrip("/") != "/v1/embeddings":
                self.send_error(404)
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            inputs = body["input"]
            if isinstance(inputs, str):
                inputs = [inputs]
            time.sleep(delay)
            payload = {
                "object": "list",
                "model": body.get("model", "fake"),
                "data": [
                    {"object": "embedding", "index": i, "embedding": embed(text, dim)}
                    for i, text in enumerate(inputs)
                ],
            }
            out = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, format: str, *args: object) -> None:
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0, help="0 picks a free port")
    parser.add_argument("--dim", type=int, default=8)
    parser.add_argument("--delay", type=float, default=0.15)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.dim, args.delay))
    print(server.server_address[1], flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
