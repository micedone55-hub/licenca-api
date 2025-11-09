import os
import datetime
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
from pymongo import MongoClient
from dotenv import load_dotenv
import subprocess
from typing import Optional

# --- Configuração ---
load_dotenv()
MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = "license_db"
COLLECTION_NAME = "keys"

if not MONGODB_URI:
    print("ERRO: MONGODB_URI não encontrada. Verifique seu arquivo .env")

app = FastAPI()
client = None
collection = None

@app.on_event("startup")
def startup_db_client():
    global client, collection
    try:
        client = MongoClient(MONGODB_URI)
        db = client[DB_NAME]
        collection = db[COLLECTION_NAME]
        client.admin.command('ping') # Testa a conexão
        print("Conectado ao MongoDB Atlas com sucesso!")
    except Exception as e:
        print(f"ERRO CRÍTICO ao conectar ao MongoDB: {e}")
        client = None
        collection = None

@app.on_event("shutdown")
def shutdown_db_client():
    if client:
        client.close()
        print("Conexão com MongoDB fechada.")

# --- Modelos de Dados ---
class LicenseRequest(BaseModel):
    key: str
    hwid: str

class LicenseResponse(BaseModel):
    status: str
    message: str
    key_type: Optional[str] = None
    days_remaining: Optional[int] = None

# --- Endpoints da API ---
@app.get("/")
def read_root():
    return {"status": "Servidor de Licenças Online"}

@app.post("/validate", response_model=LicenseResponse)
async def validate_license(request: LicenseRequest):
    if not collection:
         raise HTTPException(status_code=503, detail="Serviço indisponível: Não foi possível conectar ao banco de dados.")

    print(f"Recebida requisição para a chave: {request.key} | HWID: {request.hwid}")

    license_data = collection.find_one({"key": request.key})

    if not license_data:
        print("Resultado: Chave não encontrada.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chave de licença não encontrada.")

    print(f"Chave encontrada. Dados: {license_data}")

    # 2. Validar o HWID
    bound_hwid = license_data.get("hwid")

    if bound_hwid is not None:
        if bound_hwid == "":
            print(f"Vinculando HWID {request.hwid} à chave {request.key}")
            collection.update_one(
                {"_id": license_data["_id"]},
                {"$set": {"hwid": request.hwid}}
            )
        elif bound_hwid != request.hwid:
            print(f"Resultado: Conflito de HWID. BD: {bound_hwid}, Cliente: {request.hwid}")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Esta chave está vinculada a outra máquina.")

    # 3. Validar a Duração (Expiração)
    duration = license_data.get("duration_days", 9999)
    activation_date_str = license_data.get("activation_date")

    if duration >= 9000: # Chave permanente
        print("Resultado: Chave permanente válida.")
        return LicenseResponse(
            status="valid", 
            message="Licença permanente ativada.",
            key_type="permanent"
        )

    today = datetime.date.today()
    activation_date = None

    if activation_date_str is None:
        print(f"Primeira ativação da chave. Ativando por {duration} dias.")
        activation_date = today
        collection.update_one(
            {"_id": license_data["_id"]},
            {"$set": {"activation_date": activation_date.isoformat()}} 
        )
    else:
        activation_date = datetime.date.fromisoformat(activation_date_str)

    days_passed = (today - activation_date).days
    days_remaining = duration - days_passed

    if days_passed > duration:
        print(f"Resultado: Chave expirada. {days_passed} dias se passaram.")
        expiration_date = activation_date + datetime.timedelta(days=duration)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Licença expirada em {expiration_date}.")

    print(f"Resultado: Chave válida. Dias restantes: {days_remaining}")
    return LicenseResponse(
        status="valid",
        message=f"Licença válida. Dias restantes: {days_remaining}",
        key_type="trial",
        days_remaining=days_remaining
    )
