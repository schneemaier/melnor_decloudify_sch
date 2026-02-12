import json
import os
import logging

class Settings:
    def __init__(self, settings_path='settings.json'):
        self.enabled = True
        self.port = 80
        self.mac1 = ""
        self.valveId11 = ""
        self.valveId12 = ""
        self.mac2 = ""
        self.valveId21 = ""
        self.valveId22 = ""
        self.myIP = "127.0.0.1"
        self.loglevel = "DEBUG"

        if os.path.exists(settings_path):
            with (open(settings_path, 'r') as f):
                data = json.load(f)
                self.enabled = data.get('enabled', self.enabled)
                self.port = data.get('port', self.port)
                self.mac1 = data.get('mac1', self.mac1)
                self.valveId11 = data.get('valveId1.1', self.valveId11)
                self.valveId12 = data.get('valveId1.2', self.valveId12)
                self.mac2 = data.get('mac2', self.mac2)
                self.valveId21 = data.get('valveId2.1', self.valveId21)
                self.valveId22 = data.get('valveId2.2', self.valveId22)
                self.myIP = data.get('myIP', self.myIP)
                self.loglevel = data.get('loglevel', self.loglevel).upper()

settings = Settings()

def setup_logging():
    level = getattr(logging, settings.loglevel, logging.INFO)
    logging.basicConfig(level=level, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

setup_logging()
