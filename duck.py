import requests
import json
import os

class DuckManager:
    def __init__(self):
        # This initializes the data object containing all the duck information
        # if internet is not available or for some reason the api is down it defaults to a local copy
        try:
            data = requests.get("https://api.ducks.ects-cmp.com/ducks").json()
            with open("cache.json", "w") as file:
                json.dump(data, file, indent=4)
        except:
            if not os.path.isfile("cache.json"):
                # here we're checking to see if the file exists and creating an empty one if it doesnt
                with open("cache.json", "w") as file:
                    pass
            with open("cache.json", "r") as file:
                data = file.read()
        self.data = data
        self.duck_list = []

    def create_duck_list(self):
        """Creates and returns a list of duck objects in the duck manager."""
        for duck in self.data:
            self.duck_list.append(Duck(duck))
        return self.duck_list

    def get_duck_by_id(self, id:list[str]|str = []):
        """Accepts either a string or list of strings containing id's and returns the duck with the matching id"""
        if id:
            return next(filter(lambda duck: duck.id in id, self.duck_list))

    def get_ducks_by_name(self, name: str):
        """Accepts a string and returns the duck with the matching name"""
        return list(filter(lambda duck: duck.name.lower() == name.lower(), self.duck_list))
    
    def get_ducks_by_assembler(self, assembler: str):
        """Accepts a string and returns the duck with the matching assembler, note this currently deos not work"""
        return list(filter(lambda duck: assembler.lower() in duck.assembler.lower(), self.duck_list))

    def update_all_ducks(self):
        for duck in self.duck_list:
            duck.update_data()

        



class Duck:
    def __init__(self, data:dict):
        # Main fields
        self.raw_data = data
        self.id = data["_id"]
        self.name = data["name"]
        self.assembler = data["assembler"]
        self.adjectives = data["adjectives"]
        self.derpy = data["derpy"]
        self.bio = data["bio"]
        self.date = data["date"]
        self.approved = data["approved"]
        self.version = data["__v"]

        # Body fields
        self.head_color = data["body"]["head"]
        self.front_left_color = data["body"]["frontLeft"]
        self.front_right_color = data["body"]["frontRight"]
        self.rear_left_color = data["body"]["rearLeft"]
        self.rear_right_color = data["body"]["rearRight"]

        # Stats fields
        self.strength = data["stats"]["strength"]
        self.health = data["stats"]["health"]
        self.focus = data["stats"]["focus"]
        self.intelligence = data["stats"]["intelligence"]
        self.kindness = data["stats"]["kindness"]

    def __str__(self):
        return f"{self.name.title()}, owned by {self.assembler.title()}"
    
    def update_data(self):
        # Main fields
        self.raw_data["_id"] = self.id
        self.raw_data["name"] = self.name
        self.raw_data["assember"] = self.assembler
        self.raw_data["adjectives"] = self.adjectives
        self.raw_data["derpy"] = self.derpy
        self.raw_data["bio"] = self.bio
        self.raw_data["date"] = self.date
        self.raw_data["approved"] = self.approved
        self.raw_data["__v"] = self.version

        # Body fields
        self.raw_data["body"]["head"] = self.head_color
        self.raw_data["body"]["front1"] = self.front_left_color
        self.raw_data["body"]["front2"] = self.front_right_color
        self.raw_data["body"]["back1"] = self.rear_left_color
        self.raw_data["body"]["back2"] = self.rear_right_color

        # Stats fields
        self.raw_data["stats"]["strength"] = self.strength
        self.raw_data["stats"]["health"] = self.health
        self.raw_data["stats"]["focus"] = self.focus
        self.raw_data["stats"]["intelligence"] = self.intelligence
        self.raw_data["stats"]["kindness"] = self.kindness

        return self.raw_data
    
    def update_online_duck(self):
        print(requests.patch(f"https://api.ducks.ects-cmp.com/ducks/{self.id}", self.update_data()))





if __name__ == "__main__":
    manager = DuckManager()
    for duck in manager.create_duck_list():
        print(duck)
    duck1 = manager.get_duck_by_id("69a8ea5053e250fdaf139d6f")
    print(duck1.raw_data)
    duck1.derpy = True
    print(duck1.raw_data)
    print(duck1.update_data())
    duck1.update_online_duck()

