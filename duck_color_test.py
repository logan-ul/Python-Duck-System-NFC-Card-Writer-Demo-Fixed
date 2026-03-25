import tkinter as t
from PIL import Image, ImageTk 
import requests
import io
import duck

def apply_mask(base, mask_path, color):
    mask = Image.open(mask_path).convert("L") # converts to greyscale
    color_layer = Image.new("RGBA", base.size, color)

    colored = Image.composite(color_layer, base, mask)

    base.paste(colored, (0, 0), mask)

    return base


def create_duck_image(duck):
    base = Image.open("duck_templates/body.png").convert("RGBA")
    colors = {
    "red": (255, 0, 0, 255),
    "yellow": (255, 255, 0, 255),
    "green": (0, 128, 0, 255),
    "blue": (0, 0, 255, 255),
    "brown": (139, 69, 19, 255),
    "purple": (128, 0, 128, 255),
    "pink": (255, 192, 203, 255),
    }
    #head:
    base = apply_mask(base, "duck_templates/head.png", colors[duck.head_color])
    #front:
    base = apply_mask(base, "duck_templates/front.png", colors[duck.front_right_color])
    #back:
    base = apply_mask(base, "duck_templates/back.png", colors[duck.rear_right_color])

    data = base.getdata()
    new_data = []

    for item in data:
        # Change white pixels to transparent
        if item[0] > 230 and item[1] > 230 and item[2] > 230:
            new_data.append((255, 255, 255, 0))
        else:
            new_data.append(item)

    base.putdata(new_data)
    return base.resize((300, 200), Image.Resampling.LANCZOS).convert("RGBA")








if __name__ == "__main__":
    window = t.Tk()
    window.title("CMP Duck Writer")

    main_label = t.Label(window, text="NFC Duck Writer")
    main_label.grid(row=0, column=0)

    reader_frame = t.Frame(window)
    reader_frame.grid(row=1, column=0)
    
    photo_response = requests.get("https://static.vecteezy.com/system/resources/previews/068/405/892/non_2x/illustration-of-nfc-reader-vector.jpg")
    manager = duck.DuckManager()
    manager.create_duck_list()

    photos = [] #python destroys image after every iteration of the loop, we store them here to keep them in memory so that when the mainloop flag is called it still has access to the dynamic images.


    for i in range(4):
        image = Image.alpha_composite(Image.open(io.BytesIO(photo_response.content)).resize((300, 200), Image.Resampling.LANCZOS).convert("RGBA"), create_duck_image(manager.duck_list[i]))
        photo = ImageTk.PhotoImage(image)
        photos.append(photo)
        individual_reader_frame = t.Frame(reader_frame)
        individual_reader_frame.grid(row=0, column=i)
        reader_image = t.Label(individual_reader_frame, image=photo)
        reader_image.grid(row=0, column=0)
        reader_label = t.Label(individual_reader_frame, text=f"Reader {i+1}")
        reader_label.grid(row=1, column=0)

    window.mainloop()