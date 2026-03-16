"""
nfc_writer_portal.py

Reusable NFC portal + writer module for PC/SC CCID readers (pyscard).

Includes:
- Reader portal (polling + parsing):
  - Polls connected readers
  - Reads UID (Get Data)
  - Reads Type 2 tag memory pages (NTAG21x / Ultralight style) via FF B0
  - Extracts NDEF message TLV
  - Parses NDEF records (URL, TEXT, MIME, EXTERNAL, etc.)
  - Detects tag present / removed / changed
  - Emits callbacks with per-reader PortalState

- Writer helpers:
  - Build NDEF messages with multiple record types (URL/TEXT/JSON/MIME/EXTERNAL)
  - Write NDEF TLV to Type 2 tags using FF D6 page writes
  - Read CC to estimate data area capacity

Install:
    pip install pyscard
"""

from __future__ import annotations

import json
import time
import threading
import hashlib
from dataclasses import dataclass
from typing import Callable, Optional, Tuple, Dict, Any, List

from smartcard.System import readers
from smartcard.Exceptions import CardConnectionException, NoCardException


# -----------------------------
# PC/SC constants
# -----------------------------

STATUS_SUCCESS_SW1 = 0x90
STATUS_SUCCESS_SW2 = 0x00

# common PC/SC “Get UID” for contactless readers
APDU_GET_CARD_UID = [0xFF, 0xCA, 0x00, 0x00, 0x00]

# Type 2 page reads/writes (4 bytes per page)
APDU_READ_PAGE_PREFIX = [0xFF, 0xB0, 0x00]   # FF B0 00 <page> 04
APDU_WRITE_PAGE_PREFIX = [0xFF, 0xD6, 0x00]  # FF D6 00 <page> 04 <d0 d1 d2 d3>

ERROR_CARD_UNRESPONSIVE_HEX = "80100066"  # SCARD_W_UNRESPONSIVE_CARD
ERROR_CARD_REMOVED_HEX = "80100069"       # SCARD_W_REMOVED_CARD


def is_transient_card_error(exception_object: Exception) -> bool:
    """
    True for “tag moved / flicker” errors. We treat these as “no stable tag”.
    """
    msg = str(exception_object).lower().replace("0x", "")
    return (
        "not responding to a reset" in msg
        or "has been removed" in msg
        or "further communication is not possible" in msg
        or ERROR_CARD_UNRESPONSIVE_HEX in msg
        or ERROR_CARD_REMOVED_HEX in msg
        or isinstance(exception_object, NoCardException)
    )


def _transmit_ok(sw1: int, sw2: int) -> bool:
    return (sw1, sw2) == (STATUS_SUCCESS_SW1, STATUS_SUCCESS_SW2)


# -----------------------------
# NDEF constants
# -----------------------------

TNF_EMPTY = 0x00
TNF_WELL_KNOWN = 0x01
TNF_MIME_MEDIA = 0x02
TNF_ABSOLUTE_URI = 0x03
TNF_EXTERNAL_TYPE = 0x04

NDEF_TYPE_URI = b"U"
NDEF_TYPE_TEXT = b"T"

URI_PREFIX_TABLE = [
    "", "http://www.", "https://www.", "http://", "https://",
    "tel:", "mailto:", "ftp://anonymous:anonymous@", "ftp://ftp.",
    "ftps://", "sftp://", "smb://", "nfs://", "ftp://", "dav://",
    "news:", "telnet://", "imap:", "rtsp://", "urn:", "pop:",
    "sip:", "sips:", "tftp:", "btspp://", "btl2cap://",
    "btgoep://", "tcpobex://", "irdaobex://", "file://",
    "urn:epc:id:", "urn:epc:tag:", "urn:epc:pat:", "urn:epc:raw:",
    "urn:epc:", "urn:nfc:"
]


# -----------------------------
# Public data types (Reader side)
# -----------------------------

@dataclass(frozen=True)
class NdefRecord:
    """
    One decoded NDEF record.

    payload_bytes: raw bytes exactly as stored in the tag record payload.
    text_value: friendly interpretation (best-effort) for display/logging.
    """
    kind: str  # "URL" | "TEXT" | "DATA(MIME)" | "DATA(EXTERNAL)" | "ABSOLUTE_URI" | "UNKNOWN"
    type_text: str
    payload_bytes: bytes
    text_value: str
    mime_type: Optional[str] = None
    external_type: Optional[str] = None

    def as_utf8(self, errors: str = "strict") -> str:
        return self.payload_bytes.decode("utf-8", errors=errors)

    def as_json(self) -> Any:
        """
        Parse payload as JSON.

        Some phone NFC apps write "smart quotes" (curly quotes) which are NOT valid JSON.
        We normalize common curly quotes to straight quotes before json.loads.
        """
        raw_text = self.payload_bytes.decode("utf-8", errors="strict")
        normalized_text = (
            raw_text.replace("\u201c", '"')
                    .replace("\u201d", '"')
                    .replace("\u2018", "'")
                    .replace("\u2019", "'")
                    .replace("\u00A0", " ")
        )
        return json.loads(normalized_text)

    def looks_like_json(self) -> bool:
        s = self.text_value.strip()
        return (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]"))


@dataclass(frozen=True)
class PortalState:
    """
    Current stable state for one reader.
    """
    reader_name: str
    uid_hex: Optional[str]                  # None when no tag present
    ndef_records: Tuple[NdefRecord, ...]    # empty when none/unknown

    def has_tag(self) -> bool:
        return self.uid_hex is not None

    def first_text(self) -> Optional[str]:
        for r in self.ndef_records:
            if r.kind == "TEXT" and r.text_value.strip():
                return r.text_value.strip()
        return None

    def first_url(self) -> Optional[str]:
        for r in self.ndef_records:
            if r.kind == "URL" and r.text_value.strip():
                return r.text_value.strip()
        return None

    def first_json(self) -> Optional[Any]:
        """
        Returns the first JSON object found.
        Prefers explicit application/json MIME records.
        Falls back to trying to parse JSON from raw payload bytes for other record types.
        """
        for r in self.ndef_records:
            if r.kind == "DATA(MIME)" and (r.mime_type or "").lower() == "application/json":
                try:
                    return r.as_json()
                except Exception:
                    pass

        for r in self.ndef_records:
            if r.kind in ("DATA(MIME)", "DATA(EXTERNAL)", "UNKNOWN", "ABSOLUTE_URI", "TEXT", "URL"):
                try:
                    return r.as_json()
                except Exception:
                    pass

        return None

    def get_name(self) -> str:
        obj = self.first_json()
        if isinstance(obj, dict) and isinstance(obj.get("name"), str) and obj["name"].strip():
            return obj["name"].strip()

        txt = self.first_text()
        if txt:
            return txt

        url = self.first_url()
        if url:
            parts = [p for p in url.split("/") if p]
            if parts:
                return parts[-1]

        return self.uid_hex or "Unknown Duck"


# -----------------------------
# Type 2: read memory + extract NDEF TLV (Reader side)
# -----------------------------

def read_type2_memory_pages(card_connection, start_page_inclusive: int, end_page_inclusive: int) -> Optional[bytes]:
    """
    Reads Type 2 tag pages (4 bytes each) via PC/SC READ BINARY:
      FF B0 00 <page> 04
    """
    dump = bytearray()
    for page in range(start_page_inclusive, end_page_inclusive + 1):
        apdu_read_page = APDU_READ_PAGE_PREFIX + [page & 0xFF, 0x04]
        page_bytes, sw1, sw2 = card_connection.transmit(apdu_read_page)
        if (sw1, sw2) != (STATUS_SUCCESS_SW1, STATUS_SUCCESS_SW2) or len(page_bytes) != 4:
            return None
        dump.extend(page_bytes)
    return bytes(dump)


def extract_ndef_from_type2_tlvs(type2_memory_bytes: bytes) -> Optional[bytes]:
    """
    Scans TLVs starting at byte offset 16 (page 4) and returns the NDEF Message TLV (0x03) payload.
    """
    if not type2_memory_bytes or len(type2_memory_bytes) < 16:
        return None

    idx = 16
    n = len(type2_memory_bytes)

    while idx < n:
        tlv_tag = type2_memory_bytes[idx]
        idx += 1

        if tlv_tag == 0x00:
            continue
        if tlv_tag == 0xFE:
            return None

        if idx >= n:
            return None

        tlv_length = type2_memory_bytes[idx]
        idx += 1

        if tlv_length == 0xFF:
            if idx + 1 >= n:
                return None
            tlv_length = (type2_memory_bytes[idx]
                          << 8) | type2_memory_bytes[idx + 1]
            idx += 2

        if idx + tlv_length > n:
            return None

        tlv_value = type2_memory_bytes[idx:idx + tlv_length]
        idx += tlv_length

        if tlv_tag == 0x03:
            return tlv_value

    return None


# -----------------------------
# NDEF parsing helpers (Reader side)
# -----------------------------

def safe_hex(payload_bytes: bytes, limit: int = 96) -> str:
    snippet = payload_bytes[:limit]
    hex_text = " ".join(f"{b:02X}" for b in snippet)
    return hex_text + (" …" if len(payload_bytes) > limit else "")


def payload_to_text(payload_bytes: bytes) -> str:
    """
    Prefer UTF-8 text, fall back to HEX preview.
    """
    if not payload_bytes:
        return ""
    try:
        return payload_bytes.decode("utf-8")
    except Exception:
        return f"HEX: {safe_hex(payload_bytes)}"


def parse_ndef_message(ndef_message_bytes: bytes) -> Tuple[NdefRecord, ...]:
    """
    Parses an NDEF message into records, with both raw bytes + friendly strings.
    """
    if not ndef_message_bytes:
        return tuple()

    records: List[NdefRecord] = []
    idx = 0

    while idx < len(ndef_message_bytes):
        header = ndef_message_bytes[idx]
        idx += 1

        message_end = (header & 0x40) != 0
        short_record = (header & 0x10) != 0
        id_length_present = (header & 0x08) != 0
        tnf = header & 0x07

        if idx >= len(ndef_message_bytes):
            break

        type_length = ndef_message_bytes[idx]
        idx += 1

        if short_record:
            if idx >= len(ndef_message_bytes):
                break
            payload_length = ndef_message_bytes[idx]
            idx += 1
        else:
            if idx + 3 >= len(ndef_message_bytes):
                break
            payload_length = (
                (ndef_message_bytes[idx] << 24)
                | (ndef_message_bytes[idx + 1] << 16)
                | (ndef_message_bytes[idx + 2] << 8)
                | (ndef_message_bytes[idx + 3])
            )
            idx += 4

        record_id_length = 0
        if id_length_present:
            if idx >= len(ndef_message_bytes):
                break
            record_id_length = ndef_message_bytes[idx]
            idx += 1

        if idx + type_length > len(ndef_message_bytes):
            break
        type_bytes = ndef_message_bytes[idx:idx + type_length]
        idx += type_length

        if idx + record_id_length > len(ndef_message_bytes):
            break
        idx += record_id_length

        if idx + payload_length > len(ndef_message_bytes):
            break
        payload_bytes = ndef_message_bytes[idx:idx + payload_length]
        idx += payload_length

        type_text = type_bytes.decode("utf-8", errors="replace")

        if tnf == TNF_WELL_KNOWN and type_bytes == NDEF_TYPE_URI:
            prefix_code = payload_bytes[0] if len(payload_bytes) > 0 else 0
            uri_rest = payload_bytes[1:].decode("utf-8", errors="replace")
            prefix = URI_PREFIX_TABLE[prefix_code] if prefix_code < len(
                URI_PREFIX_TABLE) else ""
            records.append(
                NdefRecord(
                    kind="URL",
                    type_text=type_text,
                    payload_bytes=payload_bytes,
                    text_value=prefix + uri_rest,
                )
            )

        elif tnf == TNF_WELL_KNOWN and type_bytes == NDEF_TYPE_TEXT:
            if len(payload_bytes) >= 1:
                status = payload_bytes[0]
                lang_len = status & 0x3F
                text_part = payload_bytes[1 + lang_len:]
                text_value = text_part.decode("utf-8", errors="replace")
            else:
                text_value = ""
            records.append(
                NdefRecord(
                    kind="TEXT",
                    type_text=type_text,
                    payload_bytes=payload_bytes,
                    text_value=text_value,
                )
            )

        elif tnf == TNF_MIME_MEDIA:
            mime_type = type_text
            records.append(
                NdefRecord(
                    kind="DATA(MIME)",
                    type_text=type_text,
                    payload_bytes=payload_bytes,
                    text_value=payload_to_text(payload_bytes),
                    mime_type=mime_type,
                )
            )

        elif tnf == TNF_EXTERNAL_TYPE:
            external_type = type_text
            records.append(
                NdefRecord(
                    kind="DATA(EXTERNAL)",
                    type_text=type_text,
                    payload_bytes=payload_bytes,
                    text_value=payload_to_text(payload_bytes),
                    external_type=external_type,
                )
            )

        elif tnf == TNF_ABSOLUTE_URI:
            records.append(
                NdefRecord(
                    kind="ABSOLUTE_URI",
                    type_text=type_text,
                    payload_bytes=payload_bytes,
                    text_value=payload_to_text(payload_bytes),
                )
            )

        else:
            records.append(
                NdefRecord(
                    kind="UNKNOWN",
                    type_text=type_text,
                    payload_bytes=payload_bytes,
                    text_value=payload_to_text(payload_bytes),
                )
            )

        if message_end:
            break

    return tuple(records)


def read_uid_hex(card_connection) -> Optional[str]:
    uid_bytes, sw1, sw2 = card_connection.transmit(APDU_GET_CARD_UID)
    if (sw1, sw2) != (STATUS_SUCCESS_SW1, STATUS_SUCCESS_SW2):
        return None
    return "".join(f"{b:02X}" for b in uid_bytes)


def read_portal_state_for_reader(reader_obj, memory_page_end_inclusive: int) -> PortalState:
    """
    Reads a stable snapshot: UID + NDEF records.
    """
    reader_name = str(reader_obj)
    try:
        connection = reader_obj.createConnection()
        connection.connect()

        uid_hex = read_uid_hex(connection)
        if uid_hex is None:
            return PortalState(reader_name=reader_name, uid_hex=None, ndef_records=tuple())

        type2_dump = read_type2_memory_pages(
            connection, 0x00, memory_page_end_inclusive)
        if type2_dump is None:
            return PortalState(reader_name=reader_name, uid_hex=uid_hex, ndef_records=tuple())

        ndef_message = extract_ndef_from_type2_tlvs(type2_dump)
        if ndef_message is None:
            return PortalState(reader_name=reader_name, uid_hex=uid_hex, ndef_records=tuple())

        records = parse_ndef_message(ndef_message)
        return PortalState(reader_name=reader_name, uid_hex=uid_hex, ndef_records=records)

    except (CardConnectionException, NoCardException) as e:
        if is_transient_card_error(e):
            return PortalState(reader_name=reader_name, uid_hex=None, ndef_records=tuple())
        return PortalState(reader_name=reader_name, uid_hex=None, ndef_records=tuple())


def fingerprint_state(state: PortalState) -> str:
    """
    Used to detect changes without “magic” comparisons.
    """
    h = hashlib.sha256()
    h.update((state.uid_hex or "").encode("utf-8"))
    for r in state.ndef_records:
        h.update(r.kind.encode("utf-8"))
        h.update((r.mime_type or "").encode("utf-8"))
        h.update((r.external_type or "").encode("utf-8"))
        h.update(r.type_text.encode("utf-8", errors="replace"))
        h.update(r.payload_bytes)
    return h.hexdigest()


# -----------------------------
# Writer: record builders + message builder
# -----------------------------

def _encode_ndef_record(
    tnf: int,
    type_bytes: bytes,
    payload_bytes: bytes,
    *,
    record_id: bytes = b"",
    mb: bool = False,
    me: bool = False
) -> bytes:
    """
    Encodes a single NDEF record.
    Uses Short Record when payload < 256.
    """
    if payload_bytes is None:
        payload_bytes = b""
    if type_bytes is None:
        type_bytes = b""
    if record_id is None:
        record_id = b""

    il = 1 if len(record_id) > 0 else 0
    sr = 1 if len(payload_bytes) < 256 else 0

    header = 0
    header |= 0x80 if mb else 0x00  # MB
    header |= 0x40 if me else 0x00  # ME
    header |= 0x10 if sr else 0x00  # SR
    header |= 0x08 if il else 0x00  # IL
    header |= (tnf & 0x07)

    out = bytearray()
    out.append(header)
    out.append(len(type_bytes))

    if sr:
        out.append(len(payload_bytes))
    else:
        plen = len(payload_bytes)
        out.extend([(plen >> 24) & 0xFF, (plen >> 16) &
                   0xFF, (plen >> 8) & 0xFF, plen & 0xFF])

    if il:
        out.append(len(record_id))

    out.extend(type_bytes)
    if il:
        out.extend(record_id)
    out.extend(payload_bytes)
    return bytes(out)


def make_text_record(text: str, lang: str = "en") -> bytes:
    if text is None:
        text = ""
    if lang is None:
        lang = "en"

    text_bytes = text.encode("utf-8")
    lang_bytes = lang.encode("ascii", errors="ignore")
    if len(lang_bytes) > 63:
        lang_bytes = lang_bytes[:63]

    status = len(lang_bytes) & 0x3F  # UTF-8 + lang length
    payload = bytes([status]) + lang_bytes + text_bytes
    return _encode_ndef_record(TNF_WELL_KNOWN, NDEF_TYPE_TEXT, payload)


def _best_uri_prefix(url: str) -> Tuple[int, str]:
    if url is None:
        return 0, ""
    best_code = 0
    best_len = 0
    for code, prefix in enumerate(URI_PREFIX_TABLE):
        if prefix and url.startswith(prefix) and len(prefix) > best_len:
            best_code = code
            best_len = len(prefix)
    remainder = url[best_len:] if best_len > 0 else url
    return best_code, remainder


def make_url_record(url: str) -> bytes:
    if url is None:
        url = ""
    code, rest = _best_uri_prefix(url)
    payload = bytes([code]) + rest.encode("utf-8")
    return _encode_ndef_record(TNF_WELL_KNOWN, NDEF_TYPE_URI, payload)


def make_mime_record(mime_type: str, data: bytes) -> bytes:
    if mime_type is None:
        mime_type = "application/octet-stream"
    if data is None:
        data = b""
    return _encode_ndef_record(TNF_MIME_MEDIA, mime_type.encode("utf-8"), data)


def make_json_record(obj: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        txt = json.dumps(obj, ensure_ascii=False, indent=2)
    else:
        txt = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return make_mime_record("application/json", txt.encode("utf-8"))


def make_external_record(external_type: str, data: bytes) -> bytes:
    if external_type is None:
        external_type = "example.com:unknown"
    if data is None:
        data = b""
    return _encode_ndef_record(TNF_EXTERNAL_TYPE, external_type.encode("utf-8"), data)


def build_ndef_message(record_bytes_list: List[bytes]) -> bytes:
    """
    Builds an NDEF message and patches MB/ME bits.
    """
    if not record_bytes_list:
        return b""

    out = bytearray()
    for i, rec in enumerate(record_bytes_list):
        if not rec:
            continue
        b0 = rec[0] & 0x3F  # clear MB/ME
        if i == 0:
            b0 |= 0x80
        if i == len(record_bytes_list) - 1:
            b0 |= 0x40
        out.append(b0)
        out.extend(rec[1:])
    return bytes(out)


def _wrap_ndef_tlv(ndef_message: bytes) -> bytes:
    if ndef_message is None:
        ndef_message = b""
    n = len(ndef_message)
    tlv = bytearray()
    tlv.append(0x03)
    if n < 0xFF:
        tlv.append(n)
    else:
        tlv.append(0xFF)
        tlv.append((n >> 8) & 0xFF)
        tlv.append(n & 0xFF)
    tlv.extend(ndef_message)
    tlv.append(0xFE)
    return bytes(tlv)


def build_records_from_spec(spec: List[Dict[str, Any]]) -> List[bytes]:
    """
    Spec is a JSON array of objects, e.g.
      [{"type":"url","value":"https://..."},
       {"type":"text","value":"Hi","lang":"en"},
       {"type":"json","value":{"duckId":1}},
       {"type":"mime","mime":"text/plain","value":"raw text"},
       {"type":"external","external_type":"ects.edu:duck","value":{"uuid":"..."}}]
    """
    records: List[bytes] = []
    for item in (spec or []):
        rtype = (item.get("type") or "").strip().lower()

        if rtype == "url":
            records.append(make_url_record(str(item.get("value", ""))))

        elif rtype == "text":
            records.append(make_text_record(
                str(item.get("value", "")), lang=str(item.get("lang", "en"))))

        elif rtype == "json":
            pretty = bool(item.get("pretty", False))
            records.append(make_json_record(item.get("value"), pretty=pretty))

        elif rtype == "mime":
            mime = str(item.get("mime", "application/octet-stream"))
            payload = _coerce_value_to_bytes(item.get("value", b""))
            records.append(make_mime_record(mime, payload))

        elif rtype == "external":
            ext = str(item.get("external_type", "example.com:unknown"))
            payload = _coerce_value_to_bytes(item.get("value", b""))
            records.append(make_external_record(ext, payload))

        else:
            raise ValueError(f"Unknown record type in spec: {rtype!r}")

    return records


def _coerce_value_to_bytes(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, list) and all(isinstance(x, int) for x in value):
        return bytes([x & 0xFF for x in value])
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if isinstance(value, int):
        return bytes([value & 0xFF])
    return str(value).encode("utf-8")


# -----------------------------
# Writer: Type 2 capacity + writes
# -----------------------------

def get_type2_data_area_capacity_bytes(card_connection) -> int:
    """
    Reads Capability Container (CC) at page 3 to estimate data area size.

    CC0 should be 0xE1 for NDEF formatted Type 2 tags.
    CC2 is size of data area in multiples of 8 bytes.
    """
    page3 = read_type2_memory_pages(card_connection, 3, 3)
    if page3 is None or len(page3) != 4:
        raise RuntimeError("Could not read CC (page 3).")

    cc0, cc1, cc2, cc3 = page3[0], page3[1], page3[2], page3[3]
    if cc0 != 0xE1:
        raise RuntimeError(
            f"Unexpected CC0 byte (expected 0xE1, got 0x{cc0:02X}).")

    return int(cc2) * 8


def _write_type2_pages(card_connection, start_page: int, data_bytes: bytes) -> None:
    """
    Writes consecutive 4-byte pages using FF D6.
    """
    if len(data_bytes) % 4 != 0:
        raise ValueError("data_bytes length must be a multiple of 4.")

    page_count = len(data_bytes) // 4
    for i in range(page_count):
        page = start_page + i
        chunk = data_bytes[i * 4:(i + 1) * 4]
        apdu = APDU_WRITE_PAGE_PREFIX + [page & 0xFF, 0x04] + list(chunk)
        _, sw1, sw2 = card_connection.transmit(apdu)
        if not _transmit_ok(sw1, sw2):
            raise RuntimeError(
                f"Write failed at page {page} (SW={sw1:02X}{sw2:02X}).")


def write_ndef_message_to_type2_tag(
    card_connection,
    ndef_message: bytes,
    *,
    data_area_start_page: int = 4,
    pad_with_zeros: bool = True
) -> None:
    """
    Writes NDEF as TLV into Type 2 data area (page 4 by default).
    """
    tlv = _wrap_ndef_tlv(ndef_message)
    capacity = get_type2_data_area_capacity_bytes(card_connection)

    if len(tlv) > capacity:
        raise ValueError(
            f"NDEF TLV ({len(tlv)} bytes) > tag capacity ({capacity} bytes).")

    if pad_with_zeros:
        padded = bytearray(tlv)
        padded.extend(b"\x00" * (capacity - len(padded)))
        tlv_to_write = bytes(padded)
    else:
        tlv_to_write = tlv

    if len(tlv_to_write) % 4 != 0:
        tlv_to_write += b"\x00" * (4 - (len(tlv_to_write) % 4))

    # Write at most the capacity pages
    max_pages = (capacity + 3) // 4
    max_len = max_pages * 4
    tlv_to_write = tlv_to_write[:max_len]

    _write_type2_pages(card_connection, data_area_start_page, tlv_to_write)


# -----------------------------
# Manager (polling) - Reader portal
# -----------------------------

OnTagPresentCallback = Callable[[PortalState], None]
OnTagRemovedCallback = Callable[[PortalState], None]
OnStateChangedCallback = Callable[[PortalState, PortalState], None]


class NfcPortalManager:
    """
    Polling-based manager that detects insert/remove/change per reader.
    """

    def __init__(
        self,
        poll_interval_seconds: float = 0.20,
        memory_page_end_inclusive: int = 0x40,
        on_tag_present: Optional[OnTagPresentCallback] = None,
        on_tag_removed: Optional[OnTagRemovedCallback] = None,
        on_state_changed: Optional[OnStateChangedCallback] = None,
    ):
        self.poll_interval_seconds = poll_interval_seconds
        self.memory_page_end_inclusive = memory_page_end_inclusive

        self.on_tag_present = on_tag_present
        self.on_tag_removed = on_tag_removed
        self.on_state_changed = on_state_changed

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._last_state_by_reader: Dict[str, PortalState] = {}
        self._last_fingerprint_by_reader: Dict[str, str] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def get_current_states(self) -> Dict[str, PortalState]:
        return dict(self._last_state_by_reader)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            current_reader_objects = readers()
            current_reader_names = [str(r) for r in current_reader_objects]

            for reader_name in current_reader_names:
                if reader_name not in self._last_state_by_reader:
                    empty = PortalState(
                        reader_name=reader_name, uid_hex=None, ndef_records=tuple())
                    self._last_state_by_reader[reader_name] = empty
                    self._last_fingerprint_by_reader[reader_name] = fingerprint_state(
                        empty)

            for reader_obj in current_reader_objects:
                reader_name = str(reader_obj)
                old_state = self._last_state_by_reader.get(
                    reader_name,
                    PortalState(reader_name=reader_name,
                                uid_hex=None, ndef_records=tuple())
                )

                new_state = read_portal_state_for_reader(
                    reader_obj, self.memory_page_end_inclusive)
                new_fp = fingerprint_state(new_state)
                old_fp = self._last_fingerprint_by_reader.get(reader_name, "")

                if new_fp != old_fp:
                    if old_state.uid_hex is None and new_state.uid_hex is not None:
                        if self.on_tag_present:
                            self.on_tag_present(new_state)

                    elif old_state.uid_hex is not None and new_state.uid_hex is None:
                        if self.on_tag_removed:
                            self.on_tag_removed(old_state)

                    if self.on_state_changed:
                        self.on_state_changed(old_state, new_state)

                    self._last_state_by_reader[reader_name] = new_state
                    self._last_fingerprint_by_reader[reader_name] = new_fp

            time.sleep(self.poll_interval_seconds)
