"""
sample_write_app.py

Interactive console app that uses nfc_writer_portal.py to write NDEF records
defined in a JSON "records file" to a Type 2 NFC tag.

Run:
  python sample_write_app.py

Example records files are shown at the bottom of this script.
"""

from __future__ import annotations


import time
import duck
from typing import List, Dict, Any
import tkinter as t
from tkinter import ttk
from PIL import Image, ImageTk 
import requests
import io

from smartcard.System import readers
from smartcard.Exceptions import CardConnectionException, NoCardException

from duck_color_test import create_duck_image
from nfc_writer_portal import (
    read_uid_hex,
    build_records_from_spec,
    build_ndef_message,
    write_ndef_message_to_type2_tag,
    NfcPortalManager
)


def wait_for_tag_on_reader(reader_obj, poll_seconds: float = 0.20):
    """
    Polls until a stable tag UID can be read.
    Returns (connection, uid_hex).
    """
    while True:
        try:
            conn = reader_obj.createConnection()
            conn.connect()
            uid_hex = read_uid_hex(conn)
            if uid_hex:
                return conn
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
    print(duck_record)
    data = []
    data.append(
        {"type": "url", "value": f"https://api.ducks.ects-cmp.com/ducks/{duck_record.id}"})
    data.append({"type": "text", "lang": "en", "value": duck_record.id})
    data.append({"type": "json", "value": {
                "_id": duck_record.id, "assembler": duck_record.assembler, "name": duck_record.name}})

    return data


def main(ridx):
    
    rlist = readers()
    
    reader_obj = rlist[ridx]

    duck_record = drop_down.get()
    if len(duck_record) < 1:
        message_label.config(text="Please pick a valid duck")
        return
    duck_record = manager.get_ducks_by_name(duck_record)[0]
    formatted = format_duck_record(duck_record)

    record_bytes_list = build_records_from_spec(formatted)
    ndef_message = build_ndef_message(record_bytes_list)


    portal.stop()
    try:
        conn = None

        for attempt in range(10):
            try:
                conn = reader_obj.createConnection()
                conn.connect()
                break

            except (NoCardException, CardConnectionException):
                conn = None
                time.sleep(0.25)

        if conn is None:
            raise RuntimeError("Could not reconnect to tag.")

        write_ndef_message_to_type2_tag(
            conn,
            ndef_message,
            data_area_start_page=4,
            pad_with_zeros=False
        )

        message_label.config(text="Successful write!")

    except Exception as e:
        print(e)
        message_label.config(text=f"Write Failed: {e}")

    finally:
        portal.start()

def on_duck_added(state, photo_response, reader_image):
    for record in state.ndef_records:
        id = record.text_value.split("/")[-1]
        duck = manager.get_duck_by_id(id)
        image = Image.alpha_composite(Image.open(io.BytesIO(photo_response.content)).resize((300, 200), Image.Resampling.LANCZOS).convert("RGBA"), create_duck_image(duck))
        image = ImageTk.PhotoImage(image)
        global photos
        photos.append(image)
        reader_image.config(image=image)



def on_duck_removed(reader_image):
    reader_image.config(image=photos[0])




if __name__ == "__main__":
    manager = duck.DuckManager()
    manager.create_duck_list(True)

    window = t.Tk()
    window.title("CMP Duck Writer")

    main_label = t.Label(window, text="NFC Duck Writer")
    main_label.grid(row=0, column=0)

    reader_frame = t.Frame(window)
    reader_frame.grid(row=1, column=0)

    message_label = t.Label(window, text="")
    message_label.grid(row=2, column=0)

    photos = []
    image = Image.open("illustration-of-nfc-reader-vector.jpg")
    image = image.resize((300, 200), Image.Resampling.LANCZOS)
    photo = ImageTk.PhotoImage(image)
    try:
        rlist = readers()
        photo_response = requests.get("https://static.vecteezy.com/system/resources/previews/068/405/892/non_2x/illustration-of-nfc-reader-vector.jpg")
        photo = ImageTk.PhotoImage(Image.open(io.BytesIO(photo_response.content)).resize((300, 200), Image.Resampling.LANCZOS).convert("RGBA"))
        photos.append(photo)
        for i in range(len(rlist)):
            
            individual_reader_frame = t.Frame(reader_frame)
            individual_reader_frame.grid(row=0, column=i)
            reader_image = t.Label(individual_reader_frame, image=photo)
            reader_image.grid(row=0, column=0)
            reader_label = t.Label(individual_reader_frame, text=f"Reader {i+1}")
            reader_label.grid(row=1, column=0)
            reader_button = t.Button(individual_reader_frame, text="Update this one", command=lambda:main(i))
            reader_button.grid(row=2, column=0)
            portal = NfcPortalManager(
                on_tag_present=lambda state: on_duck_added(state, photo_response, reader_image),
                on_tag_removed=lambda state: on_duck_removed(reader_image),
                on_state_changed=lambda old_state, new_state: on_duck_added(new_state, photo_response, reader_image),
                )
            portal.start()
        drop_down = ttk.Combobox(window, values=[duck.name for duck in manager.duck_list], state="readonly")
        drop_down.grid(row=3, column=0, columnspan=2)
    except:
        message_label.config(text="Smart card service could not start. Ensure you have an NFC reader plugged in.")
    
    
        

    
    window.mainloop()


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
