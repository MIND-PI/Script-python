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
import macro_clp

RESAMPLE_FREQ = {
    "": None,           
    "7-dias": "30min",  
    "30-dias": "2h",    
}

def resample_performance(df: pd.DataFrame, location: str):
    freq = RESAMPLE_FREQ.get(location)

    if not freq:
        return df.sort_values("timestamp", ascending=False)

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="mixed")

    colunas = [c for c in df.columns if c != "timestamp"]
    grupo = df.set_index("timestamp")[colunas].resample(freq)

    media = grupo.mean().round(2)
    pico = grupo.max().round(2).add_suffix("_pico")
    minimo = grupo.min().round(2).add_suffix("_minimo")

    resultado = media.join(pico).join(minimo).dropna(how="all").reset_index()    
    return resultado.sort_values("timestamp", ascending=False)

def prediction_exceeds(df_prediction, column: str, limit) -> bool:
    if df_prediction is None or column not in df_prediction.columns:
        return False
    return df_prediction[column].max() >= limit

def silver_read(s3, prefix, start: datetime, end: datetime):
    print(f"[{datetime.now()}] Iniciando leitura da camada Silver...")
    print(f"[{datetime.now()}] Período de filtro Início: {start} | Fim: {end}")

    response = s3.list_objects_v2(
        Bucket=os.getenv("AWS_BUCKET_NAME"),
        Prefix=prefix
    )

    json_clp = {}
    location = get_location(start, end)

    for i in response.get("Contents", []):
        lastmodfied = i.get("LastModified")
        key = i.get("Key")

        if lastmodfied and start <= lastmodfied < end and i.get("Key").endswith(".csv"):
            id_company = get_id_company(key)
            id_clp = get_id_plc(key)

            validate_json(json_clp, id_company, id_clp, key, s3, location)

    if not json_clp:
        print("Nenhum válido arquivo encontrado")
        return None

    print(f"[{datetime.now()}] Mapeamento concluído. Total de CLPs únicos encontrados: {len(json_clp)}")
    return json_clp

def validation_filter():
    now = datetime.now(timezone.utc)
    if now.day == 28 and now.hour == 1 and now.minute == 30:
        start_actual_month = now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
        )
        end = start_actual_month + timedelta(days=27)
        start = (start_actual_month - timedelta(days=1)).replace(day=28)
    elif now.weekday() == 6 and now.hour == 23 and now.minute == 30:
        start = now - timedelta(days=7)
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

def get_location(start: datetime, end: datetime):
    days = (end - start).days
    if days >= 28:      
        return "30-dias"
    if days >= 7:
        return "7-dias"
    return ""  

def build_key(*parts: str):
    clean = []
    for p in parts:
        if p:
            clean.append(p.strip("/"))

    return "/".join(clean)

def validate_json(json_clp: dict, id_company, id_clp, key, s3, location):

    if (id_company, id_clp) not in json_clp:
        response = get_csv(key, s3)
        df = pd.read_csv(BytesIO(response["Body"].read()))

        id_fornecedor = str(df["id_fornecedor"].iloc[0])
        json_clp[(id_company, id_clp)] = {
            "medicoes": get_measurements(id_clp),
            "csv": [key],
            "company": id_company,
            "id_fornecedor": id_fornecedor,
            "limit": get_limit(id_clp),
            "location": location
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
    WHERE cm.clpId = %s AND cm.status = 1;"""

    filter = (id, )

    cursor.execute(query, filter)

    result = cursor.fetchall()

    cursor.close()

    return dict(result)

def get_limit(id):
    conn = config.conn_mysql()
    cursor = conn.cursor()

    query = """SELECT 
        apelido, 
        parametro
    FROM clp_medicao cm
    JOIN tipo_medicao tm ON tm.idTipoMedicao = cm.tipoMedicaoId
    WHERE   
        cm.clpId = %s
    AND
        cm.status = 1;
    """

    filter = (id, )

    cursor.execute(query, filter)

    result = cursor.fetchall()

    cursor.close()

    if not result:
        return {}

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

def micro_analysis_plc(df: pd.DataFrame, value: dict, df_prediction: pd.DataFrame, location: str = ""):
    date = df["timestamp"].max()

    measurement = value["medicoes"]
    limit = value["limit"]

    df_new = pd.DataFrame(
        {
        "id_clp":[df.iloc[0]["id_clp"]],
        "modelo_clp": [df.iloc[0]["modelo_clp"]],
        "id_empresa": [df.iloc[0]["id_empresa"]],
        "timestamp": [date],
        }
    )

    out_limit = []
    prevision_risk = False

    if "CPU" in measurement:
        peak_cpu = df["porcentagem_uso_cpu"].max()
        df_new["porcentagem_uso_cpu"] = df.loc[df["timestamp"] == date, "porcentagem_uso_cpu"].iloc[-1]
        df_new["pico_porcentagem_uso_cpu"] = [peak_cpu]
        df_new["minimo_porcentagem_uso_cpu"] = [df["porcentagem_uso_cpu"].min()]
        if peak_cpu >= limit["CPU"]:
            out_limit.append("CPU")

        if prediction_exceeds(df_prediction, "porcentagem_uso_cpu", limit["CPU"]):
            prevision_risk = True
    
    if "SCAN" in measurement:
        peak_scan = df["scan_time_ms"].max()
        df_new["scan_time_atual"] = df.loc[df["timestamp"] == date, "scan_time_ms"].iloc[-1]
        df_new["scan_time_pico"] = [peak_scan]
        df_new["scan_time_minimo"] = [df["scan_time_ms"].min()]
        if peak_scan >= limit["SCAN"]:
            out_limit.append("SCAN")

        if prediction_exceeds(df_prediction, "scan_time_ms", limit["SCAN"]):
            prevision_risk = True

    if "RAM" in measurement and measurement.get("RAM", "") == "GB":
        peak_ram_gb = df["ram_usada_gb"].max()
        df_new["consumo_memoria_ram"] = df.loc[df["timestamp"] == date, "ram_usada_gb"].iloc[-1]
        df_new["pico_consumo_memoria_ram_gb"] = [peak_ram_gb]
        df_new["minimo_consumo_memoria_ram_gb"] = [df["ram_usada_gb"].min()]

        if peak_ram_gb >= limit["RAM"] and "RAM" not in out_limit:
            out_limit.append("RAM")

        if prediction_exceeds(df_prediction, "ram_usada_gb", limit["RAM"]):
            prevision_risk = True

    if "RAM" in measurement and measurement.get("RAM", "") == "%":
        peak_ram_percent = df["porcentagem_uso_ram"].max()
        df_new["porcentagem_uso_ram"] = df.loc[df["timestamp"] == date, "porcentagem_uso_ram"].iloc[-1]
        df_new["pico_consumo_memoria_ram_porcentagem"] = [peak_ram_percent]
        df_new["minimo_consumo_memoria_ram_porcentagem"] = [df["porcentagem_uso_ram"].min()]

        if peak_ram_percent >= limit["RAM"] and "RAM" not in out_limit:
            out_limit.append("RAM")

        if prediction_exceeds(df_prediction, "porcentagem_uso_ram", limit["RAM"]):
            prevision_risk = True

    if "UPLOAD" in measurement:
        df_new["pacotes_enviados"] = df.loc[df["timestamp"] == date, "vel_upload"].iloc[-1]

    if "DOWNLOAD" in measurement:
        df_new["pacotes_recebido"] = df.loc[df["timestamp"] == date, "vel_download"].iloc[-1]

    if "LOSS" in measurement:
        df_new["perda_pacote"] = df.loc[df["timestamp"] == date, "perda_pacotes"].iloc[-1]

    if "SWAP" in measurement:
        df_new["porcentagem_uso_swap"] = df.loc[df["timestamp"] == date, "porcentagem_uso_swap"].iloc[-1]

    if "UPTIME" in measurement:
        df_new["uptime_horas_minutos"] = df.loc[df["timestamp"] == date, "uptime_horas_minutos"].iloc[-1]

    if "HEALTH" in measurement:
        if location == "":
            health_score = df.loc[df["timestamp"] == date, "health_score"].iloc[-1]
            df_new["health_score"] = [health_score]
        else:
            health_score = round(df["health_score"].mean(), 2)
            df_new["health_score"] = [health_score]
            df_new["health_score_minimo"] = [round(df["health_score"].min(), 2)]
            df_new["health_score_maximo"] = [round(df["health_score"].max(), 2)]

        condition = [health_score < 40, health_score < 60]
        choice = ["Crítico", "Atenção"]

        df_new["health_score_status"] = np.select(
            condition,
            choice,
            default="Estável"
        )

    df_new["acima_limite"] = [out_limit]
    df_new["risco_predicao"] = prevision_risk

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

def scan_cpu_performance(df: pd.DataFrame, medicoes: dict, location: str = ""):
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

    return resample_performance(df_new, location)

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
    df = df_performance.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="mixed")
    df = (
        df.drop_duplicates(subset="timestamp", keep="last")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

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
        id_empresa = get_id_company(value["csv"][0])
        location = value["location"]
        base = f"id-empresa-{id_empresa}/id-clp-{id_clp}"
        
        if "PROCESSOS" in value["medicoes"]:
            latest_process = analysis_latest_process(df)
            upload_json(s3, latest_process, build_key(base, "clp-ultimos-processos", location, formatt))

            ranking = ranking_process(df)
            upload_json(s3, ranking, build_key(base, "clp-ranking-processos", location, formatt))


        if "PING" in value["medicoes"]:
            ping = ping_performance(df)
            upload_json(s3, ping, build_key(base, "clp-ping", location, formatt))

        # TODO: Vaidar medições e script
        if "CPU" in value["medicoes"] or "SCAN" in value["medicoes"] or "RAM" in value["medicoes"]:
            # CPU performance padrão - gráfico de linhas
            scan_cpu = scan_cpu_performance(df, value["medicoes"], location)
            upload_json(s3, scan_cpu, build_key(base, "scan-cpu-performance", location, formatt))
            upload_json(s3, scan_cpu, build_key(base, "scan-cpu-performance", location, "ultimo-resultado"))

            df_prediction = None
            if location == "":
                raw = scan_cpu_performance(df, value["medicoes"])
                history_performance = history_performance_transform(raw, s3, build_key("gold", base, "history-performance", "history.json"))

                upload_json(s3, history_performance, build_key(base, "history-performance", "history"))

                df_prediction, df_metrics = performance_prediction(history_performance, value["medicoes"])
                upload_json(s3, df_prediction, build_key(base, "prediction-performance", formatt))
                upload_json(s3, df_prediction, build_key(base, "prediction-performance", "ultimo-resultado"))
                upload_json(s3, df_metrics, build_key(base, "prediction-metrics", formatt))
                upload_json(s3, df_metrics, build_key(base, "prediction-metrics", "ultimo-resultado"))

        micro_analysis = micro_analysis_plc(df, value, df_prediction, location)
        upload_json(s3, micro_analysis, build_key(base, "clp-micro", location, formatt))
        upload_json(s3, micro_analysis, build_key(base, "clp-micro", location, "ultimo-resultado"))



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
        location = value["location"]
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


        base = f"id-empresa-{empresa}"

        upload_json(s3, model, build_key(base, "modelo-resumo", location, formatt))
        upload_json(s3, model_score, build_key(base, "modelos-health-score-historico", location, formatt))
        upload_json(s3, critical, build_key(base, "modelos-health-score-status", location, formatt))

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
    macro_clp.client_macro_analysis(json_clp, s3)

    print(f"[{datetime.now()}] PIPELINE GOLD FINALIZADO COM SUCESSO")


if __name__ == "__main__":
    main()


    
