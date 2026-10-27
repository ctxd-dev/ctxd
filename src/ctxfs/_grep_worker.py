from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path


def main() -> None:
    pattern = base64.urlsafe_b64decode(sys.argv[1]).decode("utf-8")
    compiled = re.compile(pattern)
    path = Path(sys.argv[2])
    with path.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            line = raw_line.rstrip(b"\r\n").decode("utf-8", "replace")
            match = compiled.search(line)
            if match is None:
                continue
            event = {
                "type": "match",
                "data": {
                    "line_number": line_number,
                    "lines": {"text": line},
                    "submatches": [
                        {
                            "start": len(line[: match.start()].encode("utf-8")),
                            "end": len(line[: match.end()].encode("utf-8")),
                        }
                    ],
                },
            }
            print(json.dumps(event), flush=True)


if __name__ == "__main__":
    main()
