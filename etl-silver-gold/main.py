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

    files = []

    for i in response.get("Contents", []):
        # TODO: Verificar se a empresa está inativa
        lastmodfied = i.get("LastModified")

        if lastmodfied and start <= lastmodfied < end:
            files.append(i)

    if not files:
        print("Nenhum arquivo encontrado")
        return None

    return files

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

def main():
    # Configuração do env
    config.configure_env()
    # Configuração do s3
    s3 = config.conn_s3()

    # Pegando parametros para filtro
    start, end = validation_filter()

    # Pegando arquivos do silver
    files = silver_read(s3, "silver", start, end)

    if (not files):
        return


    conn = config.conn_mysql()
    cursor = conn.cursor()

    # TODO: Relação empresa x tipos de medição


    # TODO: Filtro por tempo ?

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


    