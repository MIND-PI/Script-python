import boto3, dotenv, os, pandas as pd
from botocore.exceptions import ClientError
import mysql.connector

def configure_env():
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    ENV_PATH = os.path.join(BASE_DIR, '.env')
    dotenv.load_dotenv(ENV_PATH)

def conn_s3():
    return boto3.client("s3", region_name=os.getenv('AWS_BUCKET_REGION'))

def conn_mysql():
    mydb = mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME")
    )
    return mydb


LIMITE_ESTAVEL = 75
LIMITE_ATENCAO = 50

def classificar_health(score):
    if pd.isna(score):
        return "Sem dados"
    if score >= LIMITE_ESTAVEL:
        return "Estável"
    if score >= LIMITE_ATENCAO:
        return "Atenção"
    return "Crítico"