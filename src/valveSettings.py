import json
import os
import logging

class valveSettings:
    def __init__(self, settings_path='valveSettings.json'):
        self.controllerMac = []
        self.valveUnits = {}

        if os.path.exists(settings_path):
            with (open(settings_path, 'r') as f):
                dt = json.load(f)
                for data in dt.get('data'):
                    self.controllerMac.append(data.get('controllerMac'))
                    self.valveUnits[data.get('controllerMac')] = data.get('valveUnits')

if __name__ == "__main__":
    valveSettings = valveSettings()
    print(valveSettings.controllerMac)
    print(valveSettings.valveUnits)