from smartcard.System import readers
from nfc_writer_portal import (
    read_uid_hex,
    get_type2_data_area_capacity_bytes,
    build_records_from_spec,
    build_ndef_message,
    write_ndef_message_to_type2_tag,
)
from smartcard.Exceptions import CardConnectionException, NoCardException
print(readers())

while True:
    for reader_obj in readers():
            try:
                conn = reader_obj.createConnection()
                conn.connect()
                uid_hex = read_uid_hex(conn)
                print(conn)
                if uid_hex:
                    print(conn) 
                    print(uid_hex)
            except (CardConnectionException, NoCardException):
                pass