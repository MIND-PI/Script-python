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

def micro_analysis_plc(df: pd.DataFrame, value: dict):
    date = df["timestamp"].max()

    df_new = pd.DataFrame(
        {
        "id_clp":[df.iloc[0]["id_clp"]],
        "modelo_clp": [df.iloc[0]["modelo_clp"]],
        "id_empresa": [df.iloc[0]["id_empresa"]],
        "timestamp": [date],
        }
    
    )

    if "CPU" in value:
        df_new["porcentagem_uso_cpu"] = [df[date == df["timestamp"]]["porcentagem_uso_cpu"]]
        df_new["pico_porcentagem_uso_cpu"] = [df["porcentagem_uso_cpu"].max()]
    
    if "SCAN" in value:
        df_new["scan_time_atual"] = [df[date == df["timestamp"]]["scan_time_ms"]],
        df_new["scan_time_pico"] = [df["scan_time_ms"].max()]

    if "RAM" in value and value.get("RAM", "") == "GB":
        df_new["consumo_memoria_ram"] = [df[date == df["timestamp"]]["ram_usada_gb"]],
        df_new["pico_consumo_memoria_ram_gb"] = [df["ram_usada_gb"].max()]

    if "RAM" in value and value.get("RAM", "") == "%":
        df_new["porcentagem_uso_ram"] = [df[date == df["timestamp"]]["porcentagem_uso_ram"]]
        df_new["pico_consumo_memoria_ram_porcentagem"] = [df["porcentagem_uso_ram"].max()]

    if "UPLOAD" in value:
        df_new["pacotes_enviados"] = [df[date == df["timestamp"]]["vel_upload"]]

    if "DOWNLOAD" in value:
        df_new["pacotes_recebido"] = [df[date == df["timestamp"]]["vel_download"]]

    if "LOSS" in value:
        df_new["perda_pacote"] = [df[date == df["timestamp"]]["perda_pacotes"]]

    if "SWAP" in value:
        df_new["porcentagem_uso_swap"] = [df[date == df["timestamp"]]["porcentagem_uso_swap"]]

    if "UPTIME" in value:
        df_new["uptime_horas_minutos"] = [df[date == df["timestamp"]]["uptime_horas_minutos"]]

    if "HEALTH SCORE" in value:
        df_new["health_score"] = [df[date == df["timestamp"]]["health_score"]]

    return df_new

def analysis_latest_process(df: pd.DataFrame):
    date = df["timestamp"].max()

    df_new = pd.DataFrame({
        "id_clp":[df.iloc[0]["id_clp"]],
        "modelo_clp": [df.iloc[0]["modelo_clp"]],
        "id_empresa": [df.iloc[0]["id_empresa"]],
        "timestamp": [date]
    })

    df_new["top1_processo"] = [df[date == df["timestamp"]]["top1_processo"]]
    df_new["top1_cpu"] = [df[date == df["timestamp"]]["top1_cpu"]]
    df_new["top1_ram"] = [df[date == df["timestamp"]]["top1_ram"]]
    df_new["top2_processo"] = [df[date == df["timestamp"]]["top2_processo"]]
    df_new["top2_cpu"] = [df[date == df["timestamp"]]["top2_cpu"]]
    df_new["top2_ram"] = [df[date == df["timestamp"]]["top2_ram"]]
    df_new["top3_processo"] = [df[date == df["timestamp"]]["top3_processo"]]
    df_new["top3_cpu"] = [df[date == df["timestamp"]]["top3_cpu"]]
    df_new["top3_ram"] = [df[date == df["timestamp"]]["top3_ram"]]

    return df_new

def ranking_process(df: pd.DataFrame):
    processos = []

    for i in range(1, 4):
        df_processos = df[[
                "timestamp",
                f"top{i}_processo",
                f"top{i}_cpu",
                f"top{i}_ram"]].rename(columns={
            f"top{i}_processo": "processo",
            f"top{i}_cpu": "cpu",
            f"top{i}_ram": "ram"
        })

        processos.append(df_processos)

    df_new = pd.concat(processos, ignore_index=True)

    ranking = (
        df_new
        .groupby("processo")
        .agg(
            aparicoes=("timestamp", "count"),
            cpu_media=("cpu", "mean"),
            cpu_maxima=("cpu", "max"),
            ram_media=("ram", "mean"),
            ram_maxima=("ram", "max")
        )
        .reset_index()
        .sort_values("aparicoes", ascending=False)
        .reset_index(drop=True)
    )

    return ranking
  
def ping_performance(df: pd.DataFrame):
    df_new = pd.DataFrame({
        "timestamp": df["timestamp"],
        "latencia_ms": df["latencia_ms"]
    })
    return df_new.sort_values("timestamp", ascending=False)

def convert_json(df: pd.DataFrame):
    return df.to_json(orient="records")

def upload_json(s3, df, key):
    json = convert_json(df)
    now = datetime.now()
    formatt = now.strftime("%Y-%M-%d_%H-%M")
    s3.put_object(
        Bucket=os.getenv("AWS_BUCKET_NAME"),
        Key=f"gold/{key}/{formatt}.json",
        Body=json.encode("utf-8"),
        ContentType="application/json"
    )
    print(f"Envio para: {key} realizado com sucesso")
    
def scan_cpu_performance(df: pd.DataFrame):
    df_new = pd.DataFrame({
        "timestamp": df["timestamp"],
        "scan_time_ms": df["scan_time_ms"],
        "porcentagem_uso_cpu": df["porcentagem_uso_cpu"]
    })
    return df_new.sort_values("timestamp", ascending=False)

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
        micro_analysis = micro_analysis_plc(df, value["medicoes"])
        id_empresa = get_id_company(value["csv"][0])
        upload_json(s3, micro_analysis, f"id-empresa-{id_empresa}/id-clp-{ch}/clp-micro")
        
        if "PROCESSOS" in value["medicoes"]:
            latest_process = analysis_latest_process(df)
            upload_json(s3, latest_process, f"id-empresa-{id_empresa}/id-clp-{ch}/clp-ultimos-processos")
            ranking = ranking_process(df)
            upload_json(s3, ranking, f"id-empresa-{id_empresa}/id-clp-{ch}/clp-ranking-processos")
        if "PING" in value["medicoes"]:
            ping = ping_performance(df)
            upload_json(s3, ping, f"id-empresa-{id_empresa}/id-clp-{ch}/clp-ping")
        
        if "CPU" in value["medicoes"] and "SCAN" in value["medicoes"]:
            scan_cpu = scan_cpu_performance(df)
            upload_json(s3, scan_cpu, f"id-empresa-{id_empresa}/id-clp-{ch}/scan-cpu-performance")

        

    # TODO: Analises de cada kpi fornecedor
        # TODO: Cliente com menos alerta
        # TODO: Cliente com mais alerta
        # TODO: Modelo com mais alerta
        # TODO: Modelo com menos alerta
        # TODO: Recurso mais saturado


if __name__ == "__main__":
    main()


    