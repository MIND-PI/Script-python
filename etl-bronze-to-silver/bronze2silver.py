import boto3
import os
import csv
from io import StringIO
import math
import mysql.connector
from datetime import datetime, timedelta, timezone
import json

# Poder rodar local e não rodar
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BUCKET = os.getenv("AWS_BUCKET_NAME")

s3 = boto3.client("s3")

print(f"Bucket configurado: {BUCKET}")

def limpar_valor(valor):
    if valor is None:
        return ""
    return valor.strip()

def formatar_decimal(valor):
    if valor is None or str(valor).strip() == "":
        return ""
    try:
        numero = float(valor)
        if math.isfinite(numero):
            return f"{numero:.2f}"
        return str(valor)
    except (ValueError, TypeError):
        return str(valor)

def validar_percentual(valor, coluna):
    if valor == "":
        return valor
    try:
        numero = float(valor)
        if math.isfinite(numero) and 0 <= numero <= 100:
            return valor
        print(f"Aviso: {coluna} fora do intervalo: {valor}")
        return valor
    except ValueError:
        print(f"Aviso: {coluna} contém valor inválido: {valor}")
        return valor

def buscar_limites(id_clp):
    conexao = mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        port=int(os.getenv("DB_PORT", 3306))
    )
    cursor = conexao.cursor(dictionary=True)
    consulta = """
        SELECT tm.apelido, cm.parametro
        FROM clp_medicao cm
        JOIN tipo_medicao tm
            ON cm.tipoMedicaoId = tm.idTipoMedicao
        WHERE cm.clpId = %s AND cm.status = 1
    """
    cursor.execute(consulta, (id_clp,))
    resultados = cursor.fetchall()
    limites = {}
    for linha in resultados:
        if linha["apelido"] == "CPU":
            limites["cpu"] = linha["parametro"]
        elif linha["apelido"] == "RAM":
            limites["ram"] = linha["parametro"]
        elif linha["apelido"] == "SWAP":
            limites["swap"] = linha["parametro"]
        elif linha["apelido"] == "SCAN":
            limites["scan_time"] = linha["parametro"]
        elif linha["apelido"] == "PING":
            limites["latencia"] = linha["parametro"]
        elif linha["apelido"] == "LOSS":
            limites["perda_pacotes"] = linha["parametro"]
    cursor.close()
    conexao.close()
    return limites

def passou_do_limite(valor, limite):
    # alerta = o valor chegou ao parâmetro cadastrado no banco (ou passou dele)
    # componente sem limite (não monitorado) ou sem leitura válida: não é alerta
    try:
        return 1 if float(valor) >= float(limite) else 0
    except (ValueError, TypeError):
        return 0

# Até esta fração do limite a métrica vale 100; do limite em diante vale 0
FRACAO_SEM_PENALIDADE = 0.5

def calcular_health_score(cpu, ram, swap, scan_time, latencia, limites):
    pesos = {"cpu": 30, "ram": 25, "swap": 5, "scan_time": 25, "latencia": 5}
    valores = {"cpu": cpu, "ram": ram, "swap": swap, "scan_time": scan_time, "latencia": latencia}
    soma_notas = 0
    soma_pesos = 0

    for indicador, peso in pesos.items():
        # métrica que o cliente não monitora (ou limite inválido): fica fora da conta
        try:
            limite = float(limites[indicador])
        except (KeyError, ValueError, TypeError):
            continue
        if not math.isfinite(limite) or limite <= 0:
            continue

        # métrica monitorada, mas sem leitura válida (ex.: ping sem resposta): nota 0
        try:
            numero = float(valores[indicador])
            if not math.isfinite(numero) or numero < 0:
                raise ValueError
            uso = numero / limite
            if uso <= FRACAO_SEM_PENALIDADE:
                nota = 100
            else:
                nota = max(0, (1 - uso) / (1 - FRACAO_SEM_PENALIDADE) * 100)
        except (ValueError, TypeError):
            nota = 0

        soma_notas += nota * peso
        soma_pesos += peso

    if soma_pesos == 0:
        return ""
    return round(soma_notas / soma_pesos, 2)

def converter_uptime(uptime_segundos):
    if uptime_segundos is None or uptime_segundos.strip() == "":
        return ""
    try:
        uptime = int(float(uptime_segundos))
        if uptime < 0:
            print(f"Aviso: uptime inválido: {uptime_segundos}")
            return ""
        horas = uptime // 3600
        minutos = (uptime % 3600) // 60
        return f"{horas:02d}:{minutos:02d}"
    except ValueError:
        print(f"Aviso: uptime inválido: {uptime_segundos}")
        return ""

def listar_arquivos(prefixo):
    arquivos = []
    paginator = s3.get_paginator("list_objects_v2")
    for pagina in paginator.paginate(Bucket=BUCKET, Prefix=prefixo):
        arquivos.extend(pagina.get("Contents", []))
    return arquivos


def main():
    limite_tempo = datetime.now(timezone.utc) - timedelta(minutes=15)

    for arquivo in listar_arquivos("bronze/"):
        if arquivo["LastModified"] < limite_tempo:
            continue

        arquivo_bronze = arquivo["Key"]
        if not arquivo_bronze.endswith(".csv"):
            continue

        print(f"\nArquivo: {arquivo_bronze}")
        resposta_csv = s3.get_object(Bucket=BUCKET, Key=arquivo_bronze)
        conteudo = resposta_csv["Body"].read().decode("utf-8")
        leitor = csv.DictReader(StringIO(conteudo))
        registros = list(leitor)
        if not registros:
            print("CSV vazio. Arquivo ignorado.")
            continue
        if "id_fornecedor" not in registros[0]:
            print("Arquivo ignorado: coluna id_fornecedor não encontrada.")
            continue
        for linha in registros:
            for coluna in linha:
                linha[coluna] = limpar_valor(linha[coluna])
            linha["uptime_horas_minutos"] = converter_uptime(linha["uptime_segundos"])
            linha["porcentagem_uso_cpu"] = validar_percentual(linha["porcentagem_uso_cpu"], "CPU")
            linha["porcentagem_uso_ram"] = validar_percentual(linha["porcentagem_uso_ram"], "RAM")
            linha["porcentagem_uso_swap"] = validar_percentual(linha["porcentagem_uso_swap"], "Swap")
            limites = buscar_limites(linha["id_clp"])
            linha["health_score"] = calcular_health_score(
                cpu=linha["porcentagem_uso_cpu"],
                ram=linha["porcentagem_uso_ram"],
                swap=linha["porcentagem_uso_swap"],
                scan_time=linha["scan_time_ms"],
                latencia=linha["latencia_ms"],
                limites=limites
            )
            linha["alerta_cpu"] = passou_do_limite(linha["porcentagem_uso_cpu"], limites.get("cpu"))
            linha["alerta_ram"] = passou_do_limite(linha["porcentagem_uso_ram"], limites.get("ram"))
            linha["alerta_swap"] = passou_do_limite(linha["porcentagem_uso_swap"], limites.get("swap"))
            linha["alerta_scan"] = passou_do_limite(linha["scan_time_ms"], limites.get("scan_time"))
            linha["alerta_ping"] = passou_do_limite(linha["latencia_ms"], limites.get("latencia"))
            linha["alerta_loss"] = passou_do_limite(linha["perda_pacotes"], limites.get("perda_pacotes"))
            colunas_decimais = [
                "porcentagem_uso_cpu", "porcentagem_uso_ram", "porcentagem_uso_swap",
                "scan_time_ms", "latencia_ms", "health_score", "ram_total_gb",
                "ram_disponivel_gb", "ram_usada_gb", "vel_upload", "vel_download"
            ]
            for coluna in colunas_decimais:
                if coluna in linha:
                    linha[coluna] = formatar_decimal(linha[coluna])
        id_empresa = registros[0]["id_empresa"]
        saida = StringIO()
        colunas = list(registros[0].keys())
        escritor = csv.DictWriter(saida, fieldnames=colunas)
        escritor.writeheader()
        escritor.writerows(registros)
        conteudo_transformado = saida.getvalue()
        nome_arquivo = arquivo_bronze.split("/")[-1]
        arquivo_silver = f"silver/{nome_arquivo}"
        print(f"ID da Empresa: {id_empresa}")
        print(f"ID do Fornecedor: {registros[0]['id_fornecedor']}")
        print(f"Destino na Silver: {arquivo_silver}")
        s3.put_object(
            Bucket=BUCKET,
            Key=arquivo_silver,
            Body=conteudo_transformado.encode("utf-8"),
            ContentType="text/csv"
        )
        print("Arquivo enviado para a Silver!")

def lambda_handler(event, context):
    # TODO implement
    main()
    return {
        'statusCode': 200,
        'body': json.dumps('Hello from Lambda!')
    }

if __name__ == "__main__":
    main()