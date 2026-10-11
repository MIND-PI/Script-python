import boto3
import os
import csv
from dotenv import load_dotenv
from io import StringIO

load_dotenv()

BUCKET = os.getenv("AWS_BUCKET_NAME")

s3 = boto3.client("s3")

print(f"Bucket configurado: {BUCKET}")


def converter_uptime(uptime_segundos):
    uptime = int(float(uptime_segundos))

    horas = uptime // 3600
    minutos = (uptime % 3600) // 60

    return f"{horas:02d}:{minutos:02d}"


# Lista os arquivos da Bronze
resposta = s3.list_objects_v2(
    Bucket=BUCKET,
    Prefix="bronze/"
)

for arquivo in resposta.get("Contents", []):
    print(arquivo["Key"])

# Percorre os arquivos encontrados
for arquivo in resposta.get("Contents", []):
    arquivo_bronze = arquivo["Key"]

    # Ignora objetos que não sejam CSV
    if not arquivo_bronze.endswith(".csv"):
        continue

    print(f"\nArquivo encontrado: {arquivo_bronze}")

    # Lê o CSV da Bronze
    resposta_csv = s3.get_object(
        Bucket=BUCKET,
        Key=arquivo_bronze
    )

    conteudo = resposta_csv["Body"].read().decode("utf-8")

    # Converte o CSV em uma lista de registros
    leitor = csv.DictReader(StringIO(conteudo))
    registros = list(leitor)

    if not registros:
        print("CSV vazio. Arquivo ignorado.")
        continue

    # Aplica a transformação do uptime em todos os registros
    for linha in registros:
        linha["uptime_horas_minutos"] = converter_uptime(
            linha["uptime_segundos"]
        )

    id_empresa = registros[0]["id_empresa"]

    saida = StringIO()

    colunas = list(registros[0].keys())

    escritor = csv.DictWriter(saida, fieldnames=colunas)
    escritor.writeheader()
    escritor.writerows(registros)

    conteudo_transformado = saida.getvalue()

    nome_arquivo = arquivo_bronze.split("/")[-1]
    arquivo_silver = f"silver/{id_empresa}/{nome_arquivo}"

    print(f"Empresa identificada: {id_empresa}")
    print(f"Destino na Silver: {arquivo_silver}")

    s3.put_object(
        Bucket=BUCKET,
        Key=arquivo_silver,
        Body=conteudo_transformado.encode("utf-8"),
        ContentType="text/csv"
    )

    print("Arquivo enviado para a Silver!")
