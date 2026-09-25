import sys
import modal

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
)

app = modal.App("sensorcloud-sync")


# Eejcutar cada 30 min
@app.function(
    image=image,
    schedule=modal.Period(minutes=30),  # O usa modal.Cron("0 * * * *") para cada hora
    timeout=300,                        # Límite de 5 minutos por ejecución
    workdir="/root/app",
)
def run_sync():
    import main  # Importamos tu main.py existente

    # Usamos 'transfer --fromlastpoint' porque Modal ya se encarga del agendado
    sys.argv = [
        "main.py",
       # "--env-file", ".env.p1",
        "--env-file", ".env.p2",
        "transfer",
        "--fromlastpoint",
    ]

    print(f"Ejecutando comandos en Modal: {' '.join(sys.argv)}")
    
    # Invocamos la función main() directamente
    main.main()