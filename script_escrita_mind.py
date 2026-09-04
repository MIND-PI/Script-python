import csv, psutil, time, random
from datetime import datetime

b_to_gb = 1073741824
id_clp = random.randint(1, 10000)

def write_header():
    with open('./leituras.csv', 'w') as csvfile:
        csv.writer(csvfile, lineterminator='\n').writerow(['user_fornecedor','user_cliente', 'id_clp' ,'timestamp', 'porcentagem_uso_cpu', 'porcentagem_uso_ram', 'scan_time_ms', 'perda_pacotes', 'vel_upload', 'vel_download'])


def write_content():
    with open('./leituras.csv', 'a') as csvfile:
        csv.writer(csvfile, lineterminator='\n').writerow([user_fornecedor, user_cliente, id_clp, timestamp, pct_cpu, ramPercent, avg_response_time, perdaPacotes, velUpload, velDownload])


write_header()

contador = 0
while(contador<10):
    time.sleep(10)

    user_fornecedor = "Ricardo"
    user_cliente = 'Eduardo';

    # Timestamp
    timestamp = datetime.now()
    # CPU
    pct_cpu = psutil.cpu_percent(interval=0)

    # RAM

    ramPercent = psutil.virtual_memory().percent 

    # SCAN TIME (Disco)
    if(pct_cpu > 90):
        avg_response_time = (psutil.disk_io_counters().read_time / psutil.disk_io_counters().read_count) + random.uniform(2, 5);
    elif(pct_cpu > 60):
        avg_response_time = (psutil.disk_io_counters().read_time / psutil.disk_io_counters().read_count) + random.uniform(1.0, 3);
    else:
        avg_response_time = (psutil.disk_io_counters().read_time / psutil.disk_io_counters().read_count) + random.uniform(0.3, 2);


    #Rede    
    pacNaoRecebido = psutil.net_io_counters().errin
    pacNaoEnviado = psutil.net_io_counters().errout
    perdaPacotes = pacNaoEnviado + pacNaoRecebido

    velUpload = psutil.net_io_counters().bytes_sent / b_to_gb
    velDownload = psutil.net_io_counters().bytes_recv / b_to_gb

    contador+=1
    write_content()