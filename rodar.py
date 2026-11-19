import os, subprocess, sys, time
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
intervalo = int(os.getenv("INTERVALO_MIN", "15"))

while True:
    subprocess.run([sys.executable, os.path.join(BASE_DIR, "script_escrita_mind.py")])
    time.sleep(intervalo * 60)