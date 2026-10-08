import boto3, dotenv, os
from botocore.exceptions import ClientError

def configure_env():
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    ENV_PATH = os.path.join(BASE_DIR, '.env')
    dotenv.load_dotenv(ENV_PATH)

def conn_s3():
    return boto3.client("s3", region_name=os.getenv('AWS_BUCKET_REGION'))

