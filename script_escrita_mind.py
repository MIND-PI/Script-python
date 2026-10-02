import csv, psutil, time, random, json, os, sys, boto3
from datetime import datetime
from ping3 import ping
from dotenv import load_dotenv

b_to_gb = 1073741824

# user = "CPL_YAGHO"
# user = "CPL_MARIANNE"
# user = "CPL_MARIANA"
# user = "CPL_VINICIUS"
# user = "CPL_RAFAEL"
# user = "CPL_DANIEL"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, '.env')
CSV_PATH = os.path.join(BASE_DIR, 'leituras.csv')

if not os.path.isfile(ENV_PATH):
    print(f"Arquivo '{ENV_PATH}' não encontrado. Encerrando o script.")
    sys.exit(1)

load_dotenv(ENV_PATH)

id_empresa = os.getenv('ID_EMPRESA')
modelo_clp = os.getenv('MODELO_PLC_MONITORADO')
ip_destino = os.getenv('IP_DESTINO_LATENCIA', '8.8.8.8')
# AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
# AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
# AWS_SESSION_TOKEN = os.getenv('AWS_SESSION_TOKEN')

if not id_empresa or not modelo_clp:
    print("O arquivo de configuração precisa conter 'id_empresa' e 'modelo_plc_monitorado'. Encerrando o script.")
    sys.exit(1)

# session = boto3.Session(
#  aws_access_key_id=AWS_ACCESS_KEY_ID,
#  aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
#  aws_session_token=AWS_SESSION_TOKEN,
#  region_name="us-east-1",
# )

def get_top_processos(n=3):
    procs = []
    for p in psutil.process_iter(['pid', 'name', 'cpu_percent']):
        try:
            if p.info['pid'] == 0:  # ignora o "System Idle Process" no Windows
                continue
            procs.append((p.info['name'], p.info['cpu_percent'] or 0))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    procs.sort(key=lambda x: x[1], reverse=True)
    nomes = [nome for nome, _ in procs[:n]]
    while len(nomes) < n:  # garante sempre n itens para não quebrar o CSV
        nomes.append(None)
    return nomes


def write_header():
    with open(CSV_PATH, 'w') as csvfile:
        csv.writer(csvfile, lineterminator='\n').writerow([
            'user',
            'modelo_clp',
            'id_empresa',
            'timestamp',
            'porcentagem_uso_cpu',
            'ram_total_gb',
            'ram_disponivel_gb',
            'ram_usada_gb',
            'porcentagem_uso_ram',
            'porcentagem_uso_swap',
            'scan_time_ms',
            'latencia_ms',
            'perda_pacotes',
            'vel_upload',
            'vel_download',
            'top1_processo',
            'top2_processo',
            'top3_processo',
        ])


def write_content():
    with open(CSV_PATH, 'a') as csvfile:
        csv.writer(csvfile, lineterminator='\n').writerow([
            user,
            modelo_clp,
            id_empresa,
            timestamp,
            pct_cpu,
            ram_total_gb,
            ram_disponivel_gb,
            ram_usada_gb,
            ramPercent,
            swapPercent,
            scan_time_ms,
            latencia_ms,
            perdaPacotes,
            velUpload,
            velDownload,
            top_procs[0],
            top_procs[1],
            top_procs[2],
        ])


write_header()

for p in psutil.process_iter(['cpu_percent']):
    pass

contador = 0
while(contador < 10):
    time.sleep(10)

    # Timestamp
    timestamp = datetime.now()

    # CPU
    pct_cpu = psutil.cpu_percent(interval=0)

    # RAM
    mem = psutil.virtual_memory()
    ram_total_gb = mem.total / b_to_gb
    ram_disponivel_gb = mem.available / b_to_gb
    ram_usada_gb = mem.used / b_to_gb
    ramPercent = mem.percent

    # Memoria Swap
    swap = psutil.swap_memory()
    swapPercent = swap.percent

    #fator CPU de 1.0 (0% uso) até 7.0 (100% uso)
    fator_cpu = 1 + (pct_cpu / 100) * 6
    #fator memoria de 1.0 (0% uso) até 4.0 (100% uso) (influencia menos que a CPU))
    fator_mem = 1 + (ramPercent / 100) * 3

    base_scan_time = random.uniform(3, 6)
    scan_time_ms = base_scan_time * fator_cpu * fator_mem
    scan_time_ms = min(scan_time_ms, 150)

    # Latencia (tempo de resposta, em ms)
    # Tem que mudar para um Ip util para testar realmente a latencia
    resposta = ping(ip_destino, timeout=1)
    latencia_ms = resposta * 1000 if resposta else None

    # Rede
    pacNaoRecebido = psutil.net_io_counters().errin
    pacNaoEnviado = psutil.net_io_counters().errout
    perdaPacotes = pacNaoEnviado + pacNaoRecebido

    velUpload = psutil.net_io_counters().bytes_sent / b_to_gb
    velDownload = psutil.net_io_counters().bytes_recv / b_to_gb

    contador += 1
    top_procs = get_top_processos(3)
    write_content()



# O SCRIPT ESPERA UM ARQUIVO .env COM:
# ID_EMPRESA=Id da empresa
# MODELO_PLC_MONITORADO=Modelo do PLC
# IP_DESTINO_LATENCIA=IP para medir a latência (opcional)


# def upload_file(file_name, bucket, object_name=None):
#     if object_name is None:
#         object_name = os.path.basename(file_name)

#     # Upload the file
#     s3_client = session.client('s3')
#     try:
#         response = s3_client.upload_file(file_name, bucket, object_name)
#     except ClientError as e:
#         logging.error(e)
#         return False
#     return True