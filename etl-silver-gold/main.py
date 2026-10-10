import config
import boto3
import os
from datetime import datetime, timezone, timedelta
import mysql.connector
from mysql.connector.cursor import MySQLCursor
from botocore.client import BaseClient
import json
from io import BytesIO
import pandas as pd


def silver_read(s3, prefix, start: datetime, end: datetime):
    response = s3.list_objects_v2(
        Bucket=os.getenv("AWS_BUCKET_NAME"),
        Prefix=prefix
    )

    json_clp = {}

    for i in response.get("Contents", []):
        # TODO: Verificar se a empresa está inativa
        lastmodfied = i.get("LastModified")
        key = i.get("Key")

        if lastmodfied and start <= lastmodfied < end and i.get("Key").endswith(".csv"):
            id_company = get_id_company(key)
            id_clp = get_id_plc(key)

            validate_json(json_clp, id_company, id_clp, key)

    if not json_clp:
        print("Nenhum arquivo encontrado")
        return None

    return json_clp

def validation_filter():
    now = datetime.now(timezone.utc)
    if now.day == 28:
        start_actual_month = now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
        )
        end = start_actual_month + timedelta(days=27)
        start = (start_actual_month - timedelta(days=1)).replace(day=28)
    elif now.hour == 23 and now.minute == 30:
        start = now.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = now
    else:
        start = now - timedelta(minutes=15)
        end = now

    return start, end

def get_id_plc(key):
    start_id_clp = key.rfind("-")
    end_id_clp = key.rfind(".")
    id_clp = key[start_id_clp + 1:end_id_clp]

    return id_clp


def get_id_company(key):
    start_id_company = key.rfind("_")
    end_id_company = key.rfind("-")
    id_company = key[start_id_company + 1:end_id_company]

    return id_company

def validate_json(json_clp: dict, id_company, id_clp, key):
    if id_clp not in json_clp:
        json_clp[id_clp] = {
            "medicoes": get_measurements(id_clp),
            "csv": [key],
            "company": id_company
        }
    else:
        json_clp[id_clp]["csv"].append(key)

    return json_clp
        
# Relação entre id do clp e medições
def get_measurements(id):
    conn = config.conn_mysql()
    cursor = conn.cursor()
    query = """SELECT 
	apelido,
    unidade_medida
    FROM clp_medicao cm
    JOIN tipo_medicao tm ON tm.idTipoMedicao = cm.tipoMedicaoId
    WHERE clpId = %s;"""

    filter = (id, )

    cursor.execute(query, filter)

    result = cursor.fetchall()

    cursor.close()

    return dict(result)


def get_csv(key, s3: BaseClient):

    return s3.get_object(
        Bucket=os.getenv("AWS_BUCKET_NAME"),
        Key=key
    )

def convert_csv_to_df(value, s3: BaseClient, df):
    for csv in value.get("csv"):
        response = get_csv(csv, s3)
        if df is None:
            df = pd.read_csv(BytesIO(response["Body"].read()))
            continue
        
        data = pd.read_csv(BytesIO(response["Body"].read()))
        df = pd.concat([df, data], ignore_index=True)
    return df



def main():
    # Configuração do env
    config.configure_env()
    # Configuração do s3
    s3 = config.conn_s3()

    # Pegando parametros para filtro
    start, end = validation_filter()

    # Pegando arquivos do silver e colocando em um json
    json_clp = silver_read(s3, "silver", start, end)

    # Validando se o json tem algo
    if not json_clp:
        return
    
    for ch, value in json_clp.items():
        df = None
        df = convert_csv_to_df(value, s3, df)

    # TODO: Analise kpi cliente
        # TODO: Consumo cpu, memória ram, uptime, health Score do clp, pacotes recebidos e enviados, perdas de pacote, total de alertas, scan time max, scan time maximo, pico de cpu, modelo, processo1, processo2, processo3, processo1 porcentagem, processo2 porcentagem, processo3 porcentagem

        # TODO: Ordenar processos com maior consumo e porcentagem da cpu
        # TODO: COmunicação: ping nos ultimos minutos
        # TODO: 

    # TODO: Analises de cada kpi fornecedor
        # TODO: Cliente com menos alerta
        # TODO: Cliente com mais alerta
        # TODO: Modelo com mais alerta
        # TODO: MOdelo com menos alerta
        # TODO: Recurso mais saturado


if __name__ == "__main__":
    main()


    