import config


if __name__ == "__main__":
    config.configure_env()
    s3 = config.conn_s3()

    # TODO: pegar arquivos do silver

    # TODO: Relação empresa x tipos de medição

    # TODO: Filtro por tempo ?
    # TODO: filtro por empresa
    