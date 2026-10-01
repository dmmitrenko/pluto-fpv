"""Makes fpv_tx_headless.grc (no window, for a Raspberry Pi) from fpv_tx.grc.

The Qt blocks are removed: the spectrum and the modulation label go away, the
attenuation slider becomes a plain variable with the value from tx.ini.
Everything else, including the logs, stays the same. Run it again after every
change of fpv_tx.grc:

    python make_tx_headless.py
"""
import os
import re

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(SRC_DIR, "fpv_tx.grc")
TARGET = os.path.join(SRC_DIR, "fpv_tx_headless.grc")

REMOVED_BLOCKS = ("mode_label", "tx_spectrum")
SLIDER = "tx_att_slider"
SLIDER_AS_VARIABLE = """- name: tx_att_slider
  id: variable
  parameters:
    comment: 'Headless: fixed value from tx.ini'
    value: tx_attenuation_cfg
  states:
    bus_sink: false
    bus_source: false
    bus_structure: null
    coordinate: [1752, 8.0]
    rotation: 0
    state: enabled
"""


def split_blocks(text):
    """Returns (header, blocks, footer); blocks are the '- name:' entries."""
    head, rest = text.split("\nblocks:\n", 1)
    body, tail = rest.split("\nconnections:\n", 1)
    blocks = re.split(r"(?m)^(?=- name: )", body)
    return head + "\nblocks:\n", [b for b in blocks if b], "\nconnections:\n" + tail


def block_name(block):
    return block.split("\n", 1)[0][len("- name: "):].strip()


def main():
    with open(SOURCE, encoding="utf-8") as f:
        text = f.read()
    head, blocks, tail = split_blocks(text)

    options = {"generate_options: qt_gui": "generate_options: no_gui",
               "id: fpv_tx\n": "id: fpv_tx_headless\n",
               "title: fpv_tx\n": "title: fpv_tx_headless\n",
               "run_options: prompt": "run_options: run"}
    for old, new in options.items():
        if old not in head:
            raise SystemExit("ERROR: not found in the options of fpv_tx.grc: " + old.strip())
        head = head.replace(old, new, 1)

    result = []
    for block in blocks:
        name = block_name(block)
        if name in REMOVED_BLOCKS:
            continue
        if name == SLIDER:
            block = SLIDER_AS_VARIABLE
        result.append(block)
    missing = set(REMOVED_BLOCKS + (SLIDER,)) - {block_name(b) for b in blocks}
    if missing:
        raise SystemExit("ERROR: blocks not found in fpv_tx.grc: " + ", ".join(sorted(missing)))

    connections = [line for line in tail.split("\n")
                   if not any("%s," % name in line for name in REMOVED_BLOCKS)]
    with open(TARGET, "w", encoding="utf-8", newline="\n") as f:
        f.write(head + "".join(result).rstrip("\n") + "\n".join(connections))
    print("written:", TARGET)


if __name__ == "__main__":
    main()
