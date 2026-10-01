"""Embed the modem_crypto.py template into GRC blocks after changing its key/code.

Run with GNU Radio's Python (PyYAML required), then regenerate all three graphs.
The key is deliberately never printed.
"""
from pathlib import Path
import re
import yaml


def main():
    root = Path(__file__).resolve().parent
    template = (root / "modem_crypto.py").read_text(encoding="utf-8")
    marker = r"(?s)# BEGIN embedded modem_crypto\.py\n.*?# END embedded modem_crypto\.py"
    replacement = "# BEGIN embedded modem_crypto.py\n" + template + "\n# END embedded modem_crypto.py"
    for filename in ("fpv_tx.grc", "fpv_rx.grc", "fpv_tx_headless.grc"):
        path = root / filename
        text = path.read_text(encoding="utf-8")
        doc = yaml.safe_load(text)
        name = "depacketizer" if filename == "fpv_rx.grc" else "packetizer"
        block = next(b for b in doc["blocks"] if b["name"] == name)
        code, count = re.subn(marker, lambda m: replacement,
                             block["parameters"]["_source_code"], count=1)
        if count != 1:
            raise ValueError("Missing embedded cipher marker in " + filename)
        compile(code, filename, "exec")
        start = text.index("- name: " + name + "\n")
        end = text.find("\n- name:", start + 1)
        if end < 0:
            end = text.index("\nconnections:", start)
        serialized = yaml.safe_dump({"_source_code": code}, sort_keys=False, width=95)
        serialized = "".join("    " + line + "\n" for line in serialized.rstrip().splitlines())
        segment, count = re.subn(
            r"(?ms)^    _source_code:.*?(?=^    [A-Za-z_][A-Za-z_0-9]*:)",
            lambda m: serialized, text[start:end], count=1)
        if count != 1:
            raise ValueError("Missing source field in " + filename)
        path.write_text(text[:start] + segment + text[end:], encoding="utf-8")
    print("Cipher synchronized into TX/RX/headless GRC. Regenerate all graphs before running.")


if __name__ == "__main__":
    main()
