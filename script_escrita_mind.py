import csv, psutil, time, random, json, os, sys, boto3, socket
from datetime import datetime
from dotenv import load_dotenv
from botocore.exceptions import ClientError
import mysql.connector

b_to_gb = 1073741824

user = "CPL_YAGHO"
# user = "CPL_MARIANNE"
# user = "CPL_MARIANA"
# user = "CPL_VINICIUS"
# user = "CPL_RAFAEL"
# user = "CPL_DANIEL"

enviar_bucket = True

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, '.env')

if not os.path.isfile(ENV_PATH):
    print(f"Arquivo '{ENV_PATH}' não encontrado. Encerrando o script.")
    sys.exit(1)

load_dotenv(ENV_PATH)

id_clp = os.getenv('ID_CLP')
id_empresa = os.getenv('ID_EMPRESA')
modelo_clp = os.getenv('MODELO_PLC_MONITORADO')  # aqui é o modeloId da tabela modelo
localizacao = os.getenv('LOCALIZACAO_CLP', '')
IP_DESTINO_LATENCIA = '8.8.8.8'
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
AWS_SESSION_TOKEN = os.getenv('AWS_SESSION_TOKEN')
AWS_BUCKET_NAME = os.getenv('AWS_BUCKET_NAME')

DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
}

session = boto3.Session(
 aws_access_key_id=AWS_ACCESS_KEY_ID,
 aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
 aws_session_token=AWS_SESSION_TOKEN,
 region_name="us-east-1",
)

if not id_empresa or not modelo_clp:
    print("O arquivo .env precisa conter 'ID_EMPRESA' e 'MODELO_PLC_MONITORADO'. Encerrando o script.")
    sys.exit(1)

try:
    id_empresa = int(id_empresa)
    modelo_clp = int(modelo_clp)
except ValueError:
    print("'ID_EMPRESA' e 'MODELO_PLC_MONITORADO' precisam ser números inteiros (empresaId e modeloId). Encerrando o script.")
    sys.exit(1)


def salvar_id_clp_no_env(novo_id):
    #Acrescenta ID_CLP no arquivo .env para que as próximas execuções
    #usem esse id em vez de inserir um novo registro no banco.
    with open(ENV_PATH, 'a') as f:
        f.write(f"\nID_CLP={novo_id}\n")


def obter_id_clp():
    #Se já existe ID_CLP no .env, usa ele, senao, insere um novo
    # registro na tabela clp do banco e retorna o idClp gerado.
    if id_clp:
        return id_clp

    if not all([DB_CONFIG['host'], DB_CONFIG['user'], DB_CONFIG['password'], DB_CONFIG['database']]):
        print("ID_CLP não está no .env e faltam dados de conexão com o banco (DB_HOST, DB_USER, DB_PASSWORD, DB_NAME). Encerrando o script.")
        sys.exit(1)

    agora = datetime.now()
    conexao = mysql.connector.connect(**DB_CONFIG)
    try:
        cursor = conexao.cursor()
        cursor.execute(
            """INSERT INTO clp (empresaId, data_criacao, data_atualizacao, status, localizacao, modeloId)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (id_empresa, agora, agora, 1, localizacao, modelo_clp),
        )
        conexao.commit()
        novo_id = cursor.lastrowid
        salvar_id_clp_no_env(novo_id)
        print(f"Novo CLP cadastrado no banco com idClp {novo_id}. ID_CLP={novo_id} foi salvo no .env automaticamente.")
        return str(novo_id)
    finally:
        conexao.close()


id_clp = obter_id_clp()

# Nome do CSV = id do CLP + id da empresa
CSV_PATH = os.path.join(BASE_DIR, f'{id_clp}_{id_empresa}.csv')

def medir_latencia(destino, porta=53, timeout_s=1):
    #Mede a latência abrindo uma conexão TCP simples até o destino.
    #Funciona igual em Windows e Linux, sem precisar de comandos externos
    #nem permissão de root/admin. Porta 53 (DNS) funciona bem com 8.8.8.8.
    try:
        inicio = time.time()
        with socket.create_connection((destino, porta), timeout=timeout_s):
            pass
        return (time.time() - inicio) * 1000
    except Exception:
        return None


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
            'id_clp',
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
            id_clp,
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
path_name = f'bronze/{id_empresa}/{id_clp}/{os.path.basename(CSV_PATH)}'

def upload_csv(file, bucket, path):
    try:
        s3 = session.client('s3')
        s3.upload_file(file, bucket, path)
    except ClientError as e:
        print(f"Erro ao tentar enviar o arquivo ao bucket: {e}")
        sys.exit(1)


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

    # Fator CPU: de 1.0 (0% uso) até 8.0 (100% uso) - principal influência no scan time
    fator_cpu = 1 + (pct_cpu / 100) * 7

    # Fator RAM: fraco até 85% de uso, depois passa a pesar mais, com teto de 4.0
    # (RAM "sobrecarregada" começa a atrapalhar de verdade o scan time)
    LIMIAR_RAM = 85
    FATOR_RAM_NO_LIMIAR = 1 + (LIMIAR_RAM / 100) * 1.5  # ~2.275, ponto de partida acima do limiar
    FATOR_RAM_MAX = 4.0

    if ramPercent <= LIMIAR_RAM:
        fator_mem = 1 + (ramPercent / 100) * 1.5
    else:
        extra_ram = (ramPercent - LIMIAR_RAM) / (100 - LIMIAR_RAM)  # 0 -> 1 conforme vai de 85% a 100%
        fator_mem = FATOR_RAM_NO_LIMIAR + extra_ram * (FATOR_RAM_MAX - FATOR_RAM_NO_LIMIAR)

    base_scan_time = random.uniform(3, 6)
    scan_time_ms = base_scan_time * fator_cpu * fator_mem
    scan_time_ms = min(scan_time_ms, 150)

    # Latencia (tempo de resposta, em ms) - igual em Windows e Linux
    latencia_ms = medir_latencia(IP_DESTINO_LATENCIA)

    # Rede
    pacNaoRecebido = psutil.net_io_counters().errin
    pacNaoEnviado = psutil.net_io_counters().errout
    perdaPacotes = pacNaoEnviado + pacNaoRecebido

    velUpload = psutil.net_io_counters().bytes_sent / b_to_gb
    velDownload = psutil.net_io_counters().bytes_recv / b_to_gb

    contador += 1
    top_procs = get_top_processos(3)
    write_content()
if enviar_bucket:
    upload_csv(CSV_PATH, AWS_BUCKET_NAME, path_name)
# O SCRIPT ESPERA UM ARQUIVO .env COM:
# ID_EMPRESA=empresaId (número inteiro, FK pra tabela empresa)
# MODELO_PLC_MONITORADO=modeloId (número inteiro, FK pra tabela modelo)
# LOCALIZACAO_CLP=localização do CLP (opcional)
# ID_CLP=idClp (opcional - se não existir, o script cria o registro na tabela 'clp' e grava o idClp gerado aqui no .env automaticamente)
# DB_HOST=host do MySQL
# DB_PORT=porta do MySQL (opcional, padrão 3306)
# DB_USER=usuário do MySQL
# DB_PASSWORD=senha do MySQL
# DB_NAME=nome do banco
#
# Usa a tabela 'clp'
# CREATE TABLE clp(
#     idClp INT AUTO_INCREMENT,
#     empresaId INT,
#     data_criacao DATETIME,
#     data_atualizacao DATETIME,
#     status TINYINT,
#     localizacao VARCHAR(255),
#     modeloId INT,
#     PRIMARY KEY (idClp, empresaId),
#     CONSTRAINT fk_clp_empresa FOREIGN KEY (empresaId) REFERENCES empresa(idEmpresa),
#     CONSTRAINT fk_clp_modelo FOREIGN KEY (modeloId) REFERENCES modelo(idmodelo)
# );

# PROCESSOS