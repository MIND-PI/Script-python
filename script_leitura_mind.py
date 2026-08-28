import csv;
from datetime import datetime, timedelta
import pandas as pd;
import glob


def read_content():
    arquivos_csv = glob.glob('C:/Users/eduar/OneDrive/Área de Trabalho/SPTECH/2SEM/CALCULO_COMP/PYTHON/0817/leituras.csv')


    listaDataframes = []

    for arquivo in arquivos_csv:
        df_temp = pd.read_csv(arquivo)
        listaDataframes.append(df_temp)

    df_final = pd.concat(listaDataframes, ignore_index=True)

    return df_final;
    

def verificar_uso_(porcentagem):
    alerta = False
    if(porcentagem >= 90):
        alerta = True
    return alerta



def uso_cpu_by_user():
    df = ''
    user = input("Insira o nome do usuário que deseja consultar: ")
    df = read_content()

    df['timestamp'] = pd.to_datetime(df['timestamp'])
    registros = df[df['user'] == user]

    if registros.empty:
        print("Registros não encontrados")
        return

    media = registros['porcentagem_uso_cpu'].astype(float).mean()

    print(f"Registros: {len(registros)}")
    print(f"Média de uso da CPU nos últimos registros: {media:.2f}%")

    contadorAlertas = 0
    horariosAlerta = []
    contadorAtencao = 0
    for indice, linha in registros.iterrows():
        porcentagemAtual = verificar_uso_(linha['porcentagem_uso_cpu'])
        if porcentagemAtual:
            contadorAlertas += 1
            horariosAlerta.append(linha['timestamp'].strftime("Y-%m-%d %H:%M:%S"))
        print(f'User: {linha['user']}, ID_CLP: {linha['id_clp']}, Horário do registro: {linha['timestamp'].strftime("%Y-%m-%d %H:%M:%S")}, Uso de CPU: {linha['porcentagem_uso_cpu']}%\n\n')

    if contadorAlertas > 0:
        print(f"Atenção, você obteve {contadorAlertas} alertas de uso de cpu excessivo")
        for i in range(0, len(horariosAlerta), 1):
            print(f"Você obteve um alerta crítico as: {horariosAlerta[i]}")

    

def uso_ram_by_user():
    df = ''
    user = input("Insira o nome do usuário que deseja consultar: ")
    df = read_content()

    df['timestamp'] = pd.to_datetime(df['timestamp'])
    registros = df[df['user'] == user]

    media = registros['porcentagem_uso_ram'].astype(float).mean()

    if registros.empty:
            print("Registros não encontrados")
            return

    print(f"Registros: {len(registros)}")
    print(f"Média de uso da RAM nos últimos registros: {media:.2f}%")

    contadorAlertas = 0
    horariosAlerta = []
    for indice, linha in registros.iterrows():
        if verificar_uso_(linha['porcentagem_uso_ram']):
            contadorAlertas += 1
            horariosAlerta.append(linha['timestamp'].strftime("%Y-%m-%d %H:%M:%S"))
        print(f'User: {linha['user']}, ID_CLP: {linha['id_clp']}, Horário do registro: {linha['timestamp'].strftime("%Y-%m-%d %H:%M:%S")}, Uso de RAM: {linha['porcentagem_uso_ram']}%\n\n')

    if contadorAlertas > 0:
        print(f"ATENÇÃO, VOCÊ OBTEVE {contadorAlertas} ALERTA(S) POR USO DE RAM EXCESSIVO!")
        for i in range(0, len(horariosAlerta), 1):
            print(f"Você obteve um alerta crítico às {horariosAlerta[i]}")
    


def calculo_scantime_byuser():
    user = str(input('Insira o nome do usuario: '))
    df = read_content();
    
    registros = df[df['user'] == user]

    if registros.empty:
        print("Houve um erro na busca dos dados")
        return
    
    print(f"Registros: {len(registros)}")

    contadorAlertas = 0

    for indice, linha in registros.iterrows():
        if(float(linha['scan_time_ms']) > 2):
            contadorAlertas += 1

        print(f"User: {linha['user']} | Scan Time: {float(linha['scan_time_ms']):.2f}ms")

    if(contadorAlertas > 0):
        print(f"ATENÇÃO, HOUVERAM {contadorAlertas} REGISTROS FORA DOS PARÂMETROS ADEQUADOS DE SCAN TIME")

continuar = 's'
while(continuar != 'n'):
    print("Selecione qual opção deseja: ")
    print("""
        1 - Uso da CPU por usuário
        2 - Uso de RAM por usuário
        3 - Cálculo do Scan Time por usuário; 
        """)
    opt = input("Insira a opção: ")
    if(opt == '1'):
        uso_cpu_by_user()

    if(opt == '2'):
        uso_ram_by_user()

    if(opt == '3'):
        calculo_scantime_byuser()

    continuar = input("Deseja continuar? 'aperte n para sair' ")
    

    