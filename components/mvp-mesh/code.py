import subprocess
import requests
import json

def call(component: str, endpoint: str, payload: dict = None):
    """
    Выполняет вызов endpoint компонента по его имени, обращаясь к нужному instance/port.
    Использует `mvp call mvp-manager ls` для получения актуального списка.
    """
    # 1. Получаем список компонентов через `mvp call mvp-manager ls`
    try:
        result = subprocess.run(["mvp", "call", "mvp-manager", "ls"],
                                capture_output=True, text=True, check=True)
        components_info = json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        return {"error": "Failed to get component list", "details": e.stderr}

    # 2. Ищем нужный компонент по имени
    components = components_info.get("components", [])
    target = next((c for c in components if c["name"] == component), None)

    if not target:
        return {"error": f"Component '{component}' not found"}

    ip = target["ip"]
    port = target["port"]
    url = f"http://{ip}:{port}/{endpoint.lstrip('/')}"  # защита от двойного слэша

    # 3. Метод POST если есть payload, иначе GET
    try:
        if payload:
            resp = requests.post(url, json=payload)
        else:
            resp = requests.get(url)

        # Пытаемся распарсить JSON, иначе возвращаем текст
        try:
            return resp.json()
        except:
            return {"response": resp.text}

    except Exception as e:
        return {"error": str(e)}

