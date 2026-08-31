from colorama import Fore, Style, init
import csv;
from datetime import datetime, timedelta
import pandas as pd;
import glob

init(autoreset=True)

def read_content():
    arquivos_csv = glob.glob('C:/Users/eduar/OneDrive/Área de Trabalho/SPTECH/2SEM/CALCULO_COMP/PYTHON/0817/leituras.csv')


    listaDataframes = []

    for arquivo in arquivos_csv:
        df_temp = pd.read_csv(arquivo)
        listaDataframes.append(df_temp)

    df_final = pd.concat(listaDataframes, ignore_index=True)

    return df_final;
    

def verificar_uso_(porcentagem):
    alerta = ""
    if(porcentagem >= 90):
        alerta = "Vermelho"
    elif(porcentagem >=60):
        alerta = "Amarelo"
    else:
        return;

    return alerta



def uso_cpu_by_user():
    df = ''
    user = input("Insira o nome do usuário que deseja consultar: ")
    df = read_content()

    df['timestamp'] = pd.to_datetime(df['timestamp'])
    registros = df[df['user_cliente'] == user]

    if registros.empty:
        print("Registros não encontrados")
        return

    media = registros['porcentagem_uso_cpu'].astype(float).mean()

    print(f"Registros: {len(registros)}")
    print(f"Média de uso da CPU nos últimos registros: {media:.2f}%")

    contadorVermelhos = 0
    contadorAmarelos = 0

    horariosAlerta = []

    for indice, linha in registros.iterrows():
        nivelAlerta = verificar_uso_(linha['porcentagem_uso_cpu'])
        if nivelAlerta == "Vermelho":
            contadorVermelhos += 1
            horariosAlerta.append(linha['timestamp'].strftime("Y-%m-%d %H:%M:%S"))
        elif nivelAlerta == "Amarelo":
            contadorAmarelos += 1
            horariosAlerta.append(linha['timestamp'].strftime("Y-%m-%d %H:%M:%S"))
        print(f'User: {linha['user_cliente']}, ID_CLP: {linha['id_clp']}, Horário do registro: {linha['timestamp'].strftime("%Y-%m-%d %H:%M:%S")}, Uso de CPU: {linha['porcentagem_uso_cpu']}%\n\n')

    if contadorVermelhos > 0:
        print(Fore.RED + f"Atenção, você obteve {contadorVermelhos} alertas VERMELHOS de uso de cpu excessivo")
    if contadorAmarelos > 0:
        print(Fore.YELLOW + f"Atenção, você obteve {contadorAmarelos} alertas AMARELOS de uso de cpu excessivo")
    if len(horariosAlerta) > 0:
        for i in range(0, len(horariosAlerta), 1):
            print(f"Você obteve um alerta as: {horariosAlerta[i]}")    

def uso_ram_by_user():
    df = ''
    user = input("Insira o nome do usuário que deseja consultar: ")
    df = read_content()

    df['timestamp'] = pd.to_datetime(df['timestamp'])
    registros = df[df['user_cliente'] == user]

    media = registros['porcentagem_uso_ram'].astype(float).mean()

    if registros.empty:
            print("Registros não encontrados")
            return

    print(f"Registros: {len(registros)}")
    print(f"Média de uso da RAM nos últimos registros: {media:.2f}%")
    contadorVermelhos = 0
    contadorAmarelos = 0

    horariosAlerta = []

    for indice, linha in registros.iterrows():
        nivelAlerta = verificar_uso_(linha['porcentagem_uso_ram'])
        if nivelAlerta == "Vermelho":
            contadorVermelhos += 1
            horariosAlerta.append(linha['timestamp'].strftime("Y-%m-%d %H:%M:%S"))
        elif nivelAlerta == "Amarelo":
            contadorAmarelos += 1
            horariosAlerta.append(linha['timestamp'].strftime("Y-%m-%d %H:%M:%S"))
        print(f'User: {linha['user_cliente']}, ID_CLP: {linha['id_clp']}, Horário do registro: {linha['timestamp'].strftime("%Y-%m-%d %H:%M:%S")}, Uso de RAM: {linha['porcentagem_uso_ram']}%\n\n')

    if contadorVermelhos > 0:
        print(Fore.RED + f"Atenção, você obteve {contadorVermelhos} alertas VERMELHOS de uso de cpu excessivo")
    if contadorAmarelos > 0:
        print(Fore.YELLOW + f"Atenção, você obteve {contadorAmarelos} alertas AMARELOS de uso de cpu excessivo")
    if len(horariosAlerta) > 0:
        for i in range(0, len(horariosAlerta), 1):
            print(f"Você obteve um alerta as: {horariosAlerta[i]}")    
    


def calculo_scantime_byuser():
    user = str(input('Insira o nome do usuario: '))
    df = read_content();
    
    registros = df[df['user_cliente'] == user]

    if registros.empty:
        print("Houve um erro na busca dos dados")
        return
    
    print(f"Registros: {len(registros)}")

    contadorAlertas = 0

    for indice, linha in registros.iterrows():
        if(float(linha['scan_time_ms']) > 2):
            contadorAlertas += 1

        print(f"User: {linha['user_cliente']} | Scan Time: {float(linha['scan_time_ms']):.2f}ms")

    if(contadorAlertas > 0):
        print(Fore.RED + f"ATENÇÃO, HOUVERAM {contadorAlertas} REGISTROS FORA DOS PARÂMETROS ADEQUADOS DE SCAN TIME")

def perda_pacotes_by_user():
    df = ''
    user = input("Insira o nome do usuário que deseja consultar: ")
    df = read_content()

    df['timestamp'] = pd.to_datetime(df['timestamp'])
    registros = df[df['user_cliente'] == user]


    if registros.empty:
            print("Registros não encontrados")
            return

    contadorPerda = 0
    for indice, linha in registros.iterrows():
        if(linha['perda_pacotes'] != 0):
            print(Fore.YELLOW + f'User: {linha['user_cliente']}, ID_CLP: {linha['id_clp']}, Horário do registro: {linha['timestamp'].strftime("%Y-%m-%d %H:%M:%S")}, Perda de pacotes: {linha['perda_pacotes']}%\n\n')
            contadorPerda += 1;

    if contadorPerda == 0:
        print("Não houve perda de pacotes!!!")

continuar = 's'
while(continuar != 'n'):
    print("Selecione qual opção deseja: ")
    print("""
        1 - Uso da CPU por usuário;
        2 - Uso de RAM por usuário;
        3 - Cálculo do Scan Time por usuário; 
        4 - Perda de pacotes por usuário;
        """)
    opt = input("Insira a opção: ")
    if(opt == '1'):
        uso_cpu_by_user()

    if(opt == '2'):
        uso_ram_by_user()

    if(opt == '3'):
        calculo_scantime_byuser()

    if(opt == '4'):
        perda_pacotes_by_user()

    continuar = input("Deseja continuar? 'aperte n para sair' ")
    

    