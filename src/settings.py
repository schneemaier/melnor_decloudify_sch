import json
import os
import logging

class Settings:
    def __init__(self, settings_path='settings.json'):
        self.enabled = True
        self.port = 80
        self.mac = ""
        self.valveId = ""
        self.disableDNS = False
        self.dnsForwarder = "8.8.8.8"
        self.bindDNS = "0.0.0.0"
        self.myIP = "127.0.0.1"
        self.loglevel = "DEBUG"

        if os.path.exists(settings_path):
            with open(settings_path, 'r') as f:
                data = json.load(f)
                self.enabled = data.get('enabled', self.enabled)
                self.port = data.get('port', self.port)
                self.mac = data.get('mac', self.mac)
                self.valveId = data.get('valveId', self.valveId)
                self.disableDNS = data.get('disableDNS', self.disableDNS)
                self.dnsForwarder = data.get('dnsForwarder', self.dnsForwarder)
                self.bindDNS = data.get('bindDNS', self.bindDNS)
                self.myIP = data.get('myIP', self.myIP)
                self.loglevel = data.get('loglevel', self.loglevel).upper()

settings = Settings()

def setup_logging():
    level = getattr(logging, settings.loglevel, logging.INFO)
    logging.basicConfig(level=level, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

setup_logging()
