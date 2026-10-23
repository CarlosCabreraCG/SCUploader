import sys
import modal
import os

image = (
    modal.Image.debian_slim(python_version="3.14")
    .pip_install(
        # Agrega aquí las librerías externas que use tu proyecto (requests, pandas, etc.)
        "xdrlib3",
        "python-dotenv",
        "requests",
    )
   # .add_local_file(".env.p1", remote_path="/root/.env.p1")  # Copiamos los archivos .env al contenedor
    .add_local_file(".env.p2", remote_path="/root/.env.p2")
    .add_local_dir(".", remote_path="/root/app")            # Copiamos todo el código del proyecto
    .add_local_dir(
        ".",
        remote_path="/root/app",
        # EXCLUIR carpetas pesadas o locales:
        ignore=[
            ".venv",
            "venv",
            "ENV",
            "__pycache__",
            ".git",
            "*.pyc",
            "data",             # si tienes datos locales pesados
            "datos_procesados", # si guardas CSVs locales
        ],
    )
)

app = modal.App("sensorcloud-sync")


# Eejcutar cada 30 min
@app.function(
    image=image,
    schedule=modal.Cron("1,31 * * * *"),
    timeout=300,
)
def run_sync():
    # 1. Cambiamos de directorio
    os.chdir("/root/app")

    # 2. Agregamos /root/app a la lista de rutas de importación de Python
    if "/root/app" not in sys.path:
        sys.path.insert(0, "/root/app")

    # 3. Ahora Python encuentra main.py sin problemas
    import main

    sys.argv = [
        "main.py",
        "--env-file", "/root/.env.p2",
        "transfer",
        "--fromlastpoint",
    ]

    print(f"Ejecutando: {' '.join(sys.argv)}")
    main.main()