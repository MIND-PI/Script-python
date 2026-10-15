import boto3
import os
import csv
from dotenv import load_dotenv
from io import StringIO
import math

load_dotenv()

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

def calcular_health_score(cpu, ram, swap, scan_time, latencia):
    limites = {
        "cpu": 80,
        "ram": 80,
        "swap": 20,
        "scan_time": 100,
        "latencia": 200
    }

    pesos = {
        "cpu": 20,
        "ram": 20,
        "swap": 15,
        "scan_time": 20,
        "latencia": 15
    }

    valores = {
        "cpu": cpu,
        "ram": ram,
        "swap": swap,
        "scan_time": scan_time,
        "latencia": latencia
    }

    notas = {}

    for indicador, valor in valores.items():
        try:
            numero = float(valor)

            if not math.isfinite(numero) or numero < 0:
                return ""

            nota = max(
                0,
                100 - (numero / limites[indicador] * 100)
            )

            notas[indicador] = nota

        except (ValueError, TypeError):
            return ""

    score = sum(
        notas[indicador] * pesos[indicador]
        for indicador in pesos
    ) / sum(pesos.values())

    return round(score, 2)

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

resposta = s3.list_objects_v2(
    Bucket=BUCKET,
    Prefix="bronze/"
)

for arquivo in resposta.get("Contents", []):
    print(arquivo["Key"])

for arquivo in resposta.get("Contents", []):
    arquivo_bronze = arquivo["Key"]

    if not arquivo_bronze.endswith(".csv"):
        continue

    print(f"\nArquivo: {arquivo_bronze}")

    resposta_csv = s3.get_object(
        Bucket=BUCKET,
        Key=arquivo_bronze
    )

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

        linha["uptime_horas_minutos"] = converter_uptime(
            linha["uptime_segundos"]
        )

        linha["porcentagem_uso_cpu"] = validar_percentual(
            linha["porcentagem_uso_cpu"], "CPU"
        )

        linha["porcentagem_uso_ram"] = validar_percentual(
            linha["porcentagem_uso_ram"], "RAM"
        )

        linha["porcentagem_uso_swap"] = validar_percentual(
            linha["porcentagem_uso_swap"], "Swap"
        )

        linha["health_score"] = calcular_health_score(
            cpu=linha["porcentagem_uso_cpu"],
            ram=linha["porcentagem_uso_ram"],
            swap=linha["porcentagem_uso_swap"],
            scan_time=linha["scan_time_ms"],
            latencia=linha["latencia_ms"]
        )

        colunas_decimais = [
            "porcentagem_uso_cpu",
            "porcentagem_uso_ram",
            "porcentagem_uso_swap",
            "scan_time_ms",
            "latencia_ms",
            "health_score",
            "ram_total_gb",
            "ram_disponivel_gb",
            "ram_usada_gb",
            "vel_upload",
            "vel_download"
        ]

        for coluna in colunas_decimais:
            if coluna in linha:
                linha[coluna] = formatar_decimal(linha[coluna])

    id_empresa = registros[0]["id_empresa"]

    saida = StringIO()

    colunas = list(registros[0].keys())

    escritor = csv.DictWriter(
        saida,
        fieldnames=colunas
    )

    escritor.writeheader()
    escritor.writerows(registros)

    conteudo_transformado = saida.getvalue()

    nome_arquivo = arquivo_bronze.split("/")[-1]
    arquivo_silver = f"silver/{id_empresa}-{nome_arquivo}"

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