import csv, psutil, time, random, json, os, sys
from datetime import datetime

b_to_gb = 1073741824

CONFIG_PATH = './config_empresa.json'

if not os.path.isfile(CONFIG_PATH):
    print(f"Arquivo de configuração '{CONFIG_PATH}' não encontrado. Encerrando o script.")
    sys.exit(1)

with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    config_empresa = json.load(f)

id_empresa = config_empresa.get('id_empresa')
modelo_clp = config_empresa.get('modelo_plc_monitorado')

if not id_empresa or not modelo_clp:
    print("O arquivo de configuração precisa conter 'id_empresa' e 'modelo_plc_monitorado'. Encerrando o script.")
    sys.exit(1)


def write_header():
    with open('./leituras.csv', 'w') as csvfile:
        csv.writer(csvfile, lineterminator='\n').writerow([
            'user_fornecedor',
            'user_cliente',
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
        ])


def write_content():
    with open('./leituras.csv', 'a') as csvfile:
        csv.writer(csvfile, lineterminator='\n').writerow([
            user_fornecedor,
            user_cliente,
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
        ])


write_header()

contador = 0
while(contador < 10):
    time.sleep(10)

    user_fornecedor = "Ricardo"
    user_cliente = 'Eduardo'

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

    # Latencia (tempo de resposta simulado, em ms)
    # tem que arrumar isso ainda mas nao sei a biblioteca certa pra isso, por enquanto é randomico
    latencia_ms = random.uniform(1, 50)

    # Rede
    pacNaoRecebido = psutil.net_io_counters().errin
    pacNaoEnviado = psutil.net_io_counters().errout
    perdaPacotes = pacNaoEnviado + pacNaoRecebido

    velUpload = psutil.net_io_counters().bytes_sent / b_to_gb
    velDownload = psutil.net_io_counters().bytes_recv / b_to_gb

    contador += 1
    write_content()


    # O SCRIPT ESPERA UM ARQUIVO DE CONFIGURAÇÃO JSON COM O SEGUINTE FORMATO:
    # {
    #    "id_empresa": "Id da empresa",
    #    "modelo_plc_monitorado": "Modelo do PLC"
    # }