"""
sample_write_app.py

Interactive console app that uses nfc_writer_portal.py to write NDEF records
defined in a JSON "records file" to a Type 2 NFC tag.

Run:
  python sample_write_app.py

Example records files are shown at the bottom of this script.
"""

from __future__ import annotations

import json
import time
import os
import duck
from typing import List, Dict, Any

from smartcard.System import readers
from smartcard.Exceptions import CardConnectionException, NoCardException

from nfc_writer_portal import (
    read_uid_hex,
    get_type2_data_area_capacity_bytes,
    build_records_from_spec,
    build_ndef_message,
    write_ndef_message_to_type2_tag,
)


def list_readers() -> List[str]:
    return [str(r) for r in readers()]


def pick_reader_index(reader_names: List[str]) -> int:
    print("\nConnected readers:")
    for i, name in enumerate(reader_names):
        print(f"  [{i}] {name}")

    while True:
        s = input("Select reader index: ").strip()
        if s.isdigit():
            idx = int(s)
            if 0 <= idx < len(reader_names):
                return idx
        print("Invalid selection. Try again.")


def load_records_file(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Records file must be a JSON array (list).")
    return data


def choose_duck_record() -> dict | Any:
    manager = duck.DuckManager()
    for i, d in enumerate(manager.data):
        print(i, json.dumps(d, indent=2))
    idx = int(input("Enter the number for the duck you want to work with -> "))
    return manager.data[idx]


def pick_records_file() -> str:
    """
    Simple UX: user types a path.
    Tip: keep a folder like ./records/ with a few presets.
    """
    while True:
        path = input(
            "Path to records JSON file (e.g., records/duck_url_text.json): ").strip().strip('"')
        if not path:
            print("Please enter a path.")
            continue
        if not os.path.exists(path):
            print("File not found. Try again.")
            continue
        return path


def wait_for_tag_on_reader(reader_obj, poll_seconds: float = 0.20):
    """
    Polls until a stable tag UID can be read.
    Returns (connection, uid_hex).
    """
    print("\nTap/hold a tag on the reader... (Ctrl+C to quit)")
    while True:
        try:
            conn = reader_obj.createConnection()
            conn.connect()
            uid_hex = read_uid_hex(conn)
            if uid_hex:
                return conn, uid_hex
        except (CardConnectionException, NoCardException):
            pass

        time.sleep(poll_seconds)


def format_duck_record(duck_record) -> List[Dict[Any, Any]]:
    """
    [
    { "type": "url", "value": "https://ects.example/d/PIXEL" },
    { "type": "text", "lang": "en", "value": "PIXEL" },
    { "type": "json", "value": { "duckId": "PIXEL", "v": 1 } }
    ]

    """
    data = []
    data.append(
        {"type": "url", "value": f"https://api.ducks.ects-cmp.com/ducks/{duck_record['_id']}"})
    data.append({"type": "text", "lang": "en", "value": duck_record["_id"]})
    data.append({"type": "json", "value": {
                "_id": duck_record["_id"], "assembler": duck_record["assembler"], "name": duck_record["name"]}})

    return data


def main():
    rlist = readers()
    if not rlist:
        print("No PC/SC readers found.")
        return

    reader_names = [str(r) for r in rlist]
    ridx = pick_reader_index(reader_names)
    reader_obj = rlist[ridx]

    duck_record = choose_duck_record()
    formatted = format_duck_record(duck_record)

    # records_path = pick_records_file()
    # spec = load_records_file(records_path)
    spec = formatted
    # Build records + message
    record_bytes_list = build_records_from_spec(spec)
    ndef_message = build_ndef_message(record_bytes_list)

    # Wait for a tag
    conn, uid_hex = wait_for_tag_on_reader(reader_obj)
    print(f"\nDetected tag UID: {uid_hex}")

    # Show capacity
    try:
        cap = get_type2_data_area_capacity_bytes(conn)
        print(f"Tag data area capacity (from CC): {cap} bytes")
    except Exception as e:
        print(f"WARNING: Could not read capacity from CC: {e}")
        print("Attempting to write anyway (may fail).")

    # Write
    try:
        write_ndef_message_to_type2_tag(
            conn, ndef_message, data_area_start_page=4, pad_with_zeros=False)
        print("✅ Write successful.")
        print(f"Wrote records {json.dumps(spec)}")
    except Exception as e:
        print(f"❌ Write failed: {e}")

    print("\nDone.")


if __name__ == "__main__":
    main()


"""
-------------------------
Example records files
-------------------------

Save these as JSON files and point the app at them.

1) records/duck_url_text.json
[
  { "type": "url", "value": "https://ects.example/ducks/PIXEL" },
  { "type": "text", "lang": "en", "value": "Duck: Pixel" }
]

2) records/duck_json_only.json
[
  { "type": "json", "pretty": true, "value": { "duckId": "D-001", "name": "Pixel", "strength": 8 } }
]

3) records/mixed_custom.json
[
  { "type": "text", "lang": "en", "value": "Hello NFC" },
  { "type": "mime", "mime": "text/plain", "value": "Plain-text payload" },
  { "type": "external", "external_type": "ects.edu:duck", "value": { "uuid": "abc-123", "team": "blue" } }
]

4) records/json_plaintext_url.json
[
  { "type": "url",  "value": "https://ects.example/d/PIXEL" },
  { "type": "text", "lang": "en", "value": "PIXEL" },
  { "type": "json", "value": { "duckId": "PIXEL", "v": 1 } }
]
"""
