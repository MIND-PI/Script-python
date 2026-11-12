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
import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from botocore.exceptions import ClientError
import io


def silver_read(s3, prefix, start: datetime, end: datetime):
    print(f"[{datetime.now()}] Iniciando leitura da camada Silver...")
    print(f"[{datetime.now()}] Período de filtro Início: {start} | Fim: {end}")

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

            validate_json(json_clp, id_company, id_clp, key, s3)

    if not json_clp:
        print("Nenhum válido arquivo encontrado")
        return None

    print(f"[{datetime.now()}] Mapeamento concluído. Total de CLPs únicos encontrados: {len(json_clp)}")
    return json_clp

# TODO: Refazer filtro e validar valores do filtro
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

def validate_json(json_clp: dict, id_company, id_clp, key, s3):

    if (id_company, id_clp) not in json_clp:
        response = get_csv(key, s3)
        df = pd.read_csv(BytesIO(response["Body"].read()))

        id_fornecedor = str(df["id_fornecedor"].iloc[0])
        json_clp[(id_company, id_clp)] = {
            "medicoes": get_measurements(id_clp),
            "csv": [key],
            "company": id_company,
            "id_fornecedor": id_fornecedor
        }
    else:
        json_clp[id_company, id_clp]["csv"].append(key)

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
        df_new["porcentagem_uso_cpu"] = df.loc[df["timestamp"] == date, "porcentagem_uso_cpu"].iloc[-1]
        df_new["pico_porcentagem_uso_cpu"] = [df["porcentagem_uso_cpu"].max()]
        df_new["minimo_porcentagem_uso_cpu"] = [df["porcentagem_uso_cpu"].min()]
    
    if "SCAN" in value:
        df_new["scan_time_atual"] = df.loc[df["timestamp"] == date, "scan_time_ms"].iloc[-1]
        df_new["scan_time_pico"] = [df["scan_time_ms"].max()]
        df_new["scan_time_minimo"] = [df["scan_time_ms"].min()]

    if "RAM" in value and value.get("RAM", "") == "GB":
        df_new["consumo_memoria_ram"] = df.loc[df["timestamp"] == date, "ram_usada_gb"].iloc[-1]
        df_new["pico_consumo_memoria_ram_gb"] = [df["ram_usada_gb"].max()]
        df_new["minimo_consumo_memoria_ram_gb"] = [df["ram_usada_gb"].min()]

    if "RAM" in value and value.get("RAM", "") == "%":
        df_new["porcentagem_uso_ram"] = df.loc[df["timestamp"] == date, "porcentagem_uso_ram"].iloc[-1]
        df_new["pico_consumo_memoria_ram_porcentagem"] = [df["porcentagem_uso_ram"].max()]
        df_new["minimo_consumo_memoria_ram_porcentagem"] = [df["porcentagem_uso_ram"].min()]

    if "UPLOAD" in value:
        df_new["pacotes_enviados"] = df.loc[df["timestamp"] == date, "vel_upload"].iloc[-1]

    if "DOWNLOAD" in value:
        df_new["pacotes_recebido"] = df.loc[df["timestamp"] == date, "vel_download"].iloc[-1]

    if "LOSS" in value:
        df_new["perda_pacote"] = df.loc[df["timestamp"] == date, "perda_pacotes"].iloc[-1]

    if "SWAP" in value:
        df_new["porcentagem_uso_swap"] = df.loc[df["timestamp"] == date, "porcentagem_uso_swap"].iloc[-1]

    if "UPTIME" in value:
        df_new["uptime_horas_minutos"] = df.loc[df["timestamp"] == date, "uptime_horas_minutos"].iloc[-1]

    if "HEALTH" in value:
        health_score = df.loc[df["timestamp"] == date, "health_score"].iloc[-1]

        df_new["health_score"] = [health_score]

        condition = [health_score < 40,health_score < 60]

        choice = ["Crítico", "Atenção"]

        df_new["health_score_status"] = np.select(
            condition,
            choice,
            default="Estável"
        )

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
    return df.to_json(orient="records", date_format="iso")

def upload_json(s3, df, key):
    json = convert_json(df)
    s3.put_object(
        Bucket=os.getenv("AWS_BUCKET_NAME"),
        Key=f"gold/{key}.json",
        Body=json.encode("utf-8"),
        ContentType="application/json"
    )
    print(f"[{datetime.now()}] Upload realizado com sucesso para: gold/{key}.json")

def scan_cpu_performance(df: pd.DataFrame, medicoes: dict):
    df_new = pd.DataFrame({
        "timestamp": df["timestamp"]
    })

    if "CPU" in medicoes:
        df_new["porcentagem_uso_cpu"] = df["porcentagem_uso_cpu"]

    if "SCAN" in medicoes:
        df_new["scan_time_ms"] = df["scan_time_ms"]

    if "RAM" in medicoes and medicoes.get("RAM", "") == "GB":
        df_new["consumo_memoria_ram"] = df["ram_usada_gb"]

    if "RAM" in medicoes and medicoes.get("RAM", "") == "%":
        df_new["porcentagem_uso_ram"] = df["porcentagem_uso_ram"]

    return df_new.sort_values("timestamp", ascending=False)

def history_performance_transform(new_df: pd.DataFrame, s3, key):
    try:
        response = s3.get_object(
            Bucket=os.getenv("AWS_BUCKET_NAME"),
            Key=key
        )
        old_df = pd.read_json(io.BytesIO(response["Body"].read()))

    except ClientError as error:
        if error.response["Error"]["Code"] == "NoSuchKey":
            old_df = pd.DataFrame(columns=new_df.columns)
        else:
            raise


    att_df = pd.concat(
        [old_df, new_df],
        ignore_index=True
    )

    att_df = att_df.tail(5000)

    return att_df
    
def performance_prediction(df_performance: pd.DataFrame, medicoes: dict):
    df = df_performance.copy().reset_index()

    df["timestamp"] = pd.to_datetime(df["timestamp"])

    start = df["timestamp"].min()

    df["timestamp"] = (
        df["timestamp"] - start
    ).dt.total_seconds() / 60

    X = df[["timestamp"]]
    y = df["porcentagem_uso_cpu"]

    last_min = df["timestamp"].max()

    future_min = np.arange(
        last_min + 1,
        last_min + 61
    ).reshape(-1, 1)

    future_timestamp = start + pd.to_timedelta(future_min.flatten(), unit="m")


    df_new = pd.DataFrame({
        "timestamp": future_timestamp
    }).set_index("timestamp")

    metricas = {
        "CPU": "porcentagem_uso_cpu",
        "SCAN": "scan_time_ms",
        "RAM": ( "ram_usada_gb" if medicoes.get("RAM") == "GB" else "porcentagem_uso_ram" )
    }

    metricas_modelo = []

    for metrica, coluna in metricas.items():

        if metrica not in medicoes:
            continue

        if coluna not in df.columns:
            continue

        X = df[["timestamp"]]
        y = df[coluna]

        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)

        model = LinearRegression()
        model.fit(X_train, y_train)

        y_test_pred = model.predict(X_test)

        r2 = r2_score(y_test, y_test_pred)
        mae = mean_absolute_error(y_test, y_test_pred)
        mse = mean_squared_error(y_test, y_test_pred)
        rmse = np.sqrt(mse)

        metricas_modelo.append({
            "metrica": metrica,
            "r2": round(r2, 2),
            "mae": round(mae, 2),
            "mse": round(mse, 2),
            "rmse": round(rmse, 2)
        })

        model = LinearRegression()
        model.fit(X, y)

        previsoes = model.predict(future_min)

        if metrica in ["CPU"] or (metrica == "RAM" and medicoes.get("RAM") == "%"):
            previsoes = np.clip(previsoes, 0, 100)

        if metrica == "SCAN":
            previsoes = np.clip(previsoes, 0, None)

        df_new[coluna] = np.round(previsoes, decimals=2)

    df_metrics = pd.DataFrame(metricas_modelo)
    return df_new, df_metrics

def client_analysis(json_clp, s3):
    print(f"\n[{datetime.now()}] [Análise Micro] Iniciando processamento por cliente/CLP...")
    for (id_company, id_clp), value in json_clp.items():
        now = datetime.now()
        formatt = now.strftime("%Y-%m-%d_%H-%M")
        df = None
        df = convert_csv_to_df(value, s3, df)
        micro_analysis = micro_analysis_plc(df, value["medicoes"])
        id_empresa = get_id_company(value["csv"][0])
        upload_json(s3, micro_analysis, f"id-empresa-{id_empresa}/id-clp-{id_clp}/clp-micro/{formatt}")
        
        if "PROCESSOS" in value["medicoes"]:
            latest_process = analysis_latest_process(df)
            upload_json(s3, latest_process, f"id-empresa-{id_empresa}/id-clp-{id_clp}/clp-ultimos-processos/{formatt}")
            ranking = ranking_process(df)
            upload_json(s3, ranking, f"id-empresa-{id_empresa}/id-clp-{id_clp}/clp-ranking-processos/{formatt}")
        if "PING" in value["medicoes"]:
            ping = ping_performance(df)
            upload_json(s3, ping, f"id-empresa-{id_empresa}/id-clp-{id_clp}/clp-ping/{formatt}")

        # TODO: Vaidar medições e script
        if "CPU" in value["medicoes"] or "SCAN" in value["medicoes"] or "RAM" in value["medicoes"]:
            # CPU performance padrão - gráfico de linhas
            scan_cpu = scan_cpu_performance(df, value["medicoes"])
            upload_json(s3, scan_cpu, f"id-empresa-{id_empresa}/id-clp-{id_clp}/scan-cpu-performance/{formatt}")
            upload_json(s3, scan_cpu, f"id-empresa-{id_empresa}/id-clp-{id_clp}/scan-cpu-performance/ultimo-resultado")

            # Histórico
            history_performance = history_performance_transform(scan_cpu, s3, f"gold/id-empresa-{id_empresa}/id-clp-{id_clp}/history-performance/history.json")
            upload_json(s3, history_performance, f"id-empresa-{id_empresa}/id-clp-{id_clp}/history-performance/history")


            # Predição
            df_prediction, df_metrics = performance_prediction(history_performance, value["medicoes"])
            upload_json(s3, df_prediction, f"id-empresa-{id_empresa}/id-clp-{id_clp}/prediction-performance/{formatt}")
            upload_json(s3, df_prediction, f"id-empresa-{id_empresa}/id-clp-{id_clp}/prediction-performance/ultimo-resultado")

            # Métrica
            upload_json(s3, df_metrics, f"id-empresa-{id_empresa}/id-clp-{id_clp}/prediction-metrics/{formatt}")
            upload_json(s3, df_metrics, f"id-empresa-{id_empresa}/id-clp-{id_clp}/prediction-metrics/ultimo-resultado")

    print(f"[{datetime.now()}] [Análise Micro] Concluída para todos os CLPs.")

def model_analysis(df: pd.DataFrame):
    df_analysis = df.groupby("modelo_clp").agg(
        health_medio=("health_score", "mean")
    ).reset_index()

    condition = [df_analysis["health_medio"] < 40, df_analysis["health_medio"] <60]
    choice = ["Crítico", "Atenção"]
    df_analysis["status"] = np.select(condition, choice, default="Estável")

    df_analysis["health_medio"] = df_analysis["health_medio"].round(2)


    return df_analysis

def model_critical(df: pd.DataFrame):
    df_analysis = (
        df.groupby(["modelo_clp", "id_clp"])
        .agg(
            health_medio=("health_score", "mean")
        )
        .reset_index()
    )

    condition = [df_analysis["health_medio"] < 40, df_analysis["health_medio"] < 60]
    choice = ["Crítico", "Atenção"]
    df_analysis["status"] = np.select(condition, choice, default="Estável")

    ranking_status = (
        df_analysis["status"]
        .value_counts()
        .reset_index()
    )

    ranking_status.columns = ["status", "quantidade_clps"]

    return ranking_status

def model_health_score(df: pd.DataFrame):
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["timestamp"] = df["timestamp"].dt.floor("min")

    df_analysis = (
        df.groupby(["timestamp", "modelo_clp"])
        .agg(
            health_score_mean=("health_score", "mean")
        )
        .reset_index()
        .sort_values("timestamp")
    )

    df_analysis["health_score_mean"] = df_analysis["health_score_mean"].round(2)

    return df_analysis

def provider_analysis(json_clp, s3):
    print(f"\n[{datetime.now()}] [Análise Macro] Iniciando processamento por fornecedor...")
    empresa_processada = []

    for (id_empresa, id_clp), value in json_clp.items():
        now = datetime.now()
        formatt = now.strftime("%Y-%m-%d_%H-%M")
        empresa = value["id_fornecedor"]

        if empresa in empresa_processada:
            continue

        empresa_processada.append(empresa)
        print(f"[{datetime.now()}] Consolidando dados para o Fornecedor ID: {empresa}")

        df_empresa = None

        for (id_empresa, id_clp), value_now in json_clp.items():
            if empresa == value_now["id_fornecedor"]:
                df_clp = convert_csv_to_df(value_now, s3, None)
                if df_empresa is None:
                    df_empresa = df_clp
                else:
                    df_empresa = pd.concat(
                        [df_empresa, df_clp],
                        ignore_index=True
                    )

        model = model_analysis(df_empresa)
        model_score = model_health_score(df_empresa)
        critical = model_critical(df_empresa)
        upload_json(s3, model, f"id-empresa-{empresa}/modelo-resumo/{formatt}")
        upload_json(s3, model_score, f"id-empresa-{empresa}/modelos-health-score-historico/{formatt}")
        upload_json(s3, critical, f"id-empresa-{empresa}/modelos-health-score-status/{formatt}")

    print(f"[{datetime.now()}] [Análise Macro] Concluída para todos os fornecedores.")
        
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

    client_analysis(json_clp, s3)
    provider_analysis(json_clp, s3)

    print(f"[{datetime.now()}] PIPELINE GOLD FINALIZADO COM SUCESSO")


if __name__ == "__main__":
    main()


    