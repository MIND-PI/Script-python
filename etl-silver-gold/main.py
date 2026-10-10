import config
import boto3
import os
from datetime import datetime, timezone, timedelta
import mysql.connector



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
            "medidas": [],
            "csv": [key],
            "company": id_company
        }
    else:
        json_clp[id_clp]["csv"].append(key)

    return json_clp
        


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

    conn = config.conn_mysql()
    cursor = conn.cursor()

    # TODO: Relação empresa x tipos de medição    


    # TODO: Analises de cada kpi fornecedor
        # TODO: Cliente com menos alerta
        # TODO: Cliente com mais alerta
        # TODO: Modelo com mais alerta
        # TODO: MOdelo com menos alerta
        # TODO: Recurso mais saturado
    # TODO: Analise kpi cliente
        # TODO: Consumo cpu, memória ram, uptime, total de alertas, health Score do clp, pacotes recebidos e enviados, perdas de pacote
        # TODO: Ordenar processos com maior consumo e porcentagem da cpu
        # TODO: COmunicação: ping nos ultimos minutos

if __name__ == "__main__":
    main()


    