from __future__ import annotations

import re
from pathlib import Path


def _project_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-") or "term5-app"


def flask_celery_mongo(name: str, port: int, *, with_celery: bool = True, with_mongo: bool = True) -> dict[str, str]:
    """Small but operational development scaffold. Redis is added when Celery is enabled."""
    slug = _project_slug(name)
    requirements = ["Flask>=3.0,<4", "gunicorn>=22,<24"]
    if with_mongo:
        requirements.append("pymongo>=4.8,<5")
    if with_celery:
        requirements += ["celery>=5.4,<6", "redis>=5,<7"]

    imports = ["import os", "from flask import Flask, jsonify, request"]
    setup = []
    health_checks = []
    if with_mongo:
        imports.append("from pymongo import MongoClient")
        setup += ["mongo = MongoClient(os.getenv('MONGO_URL', 'mongodb://mongo:27017/'))", "db = mongo[os.getenv('MONGO_DB', 'app')]" ]
        health_checks.append("db.command('ping')")
    if with_celery:
        imports.append("from tasks import process_message")

    task_endpoint = ""
    if with_celery:
        task_endpoint = '''\n@app.post("/tasks")\ndef create_task():\n    payload = request.get_json(silent=True) or {}\n    message = str(payload.get("message") or "").strip()\n    if not message:\n        return jsonify({"error": "message is required"}), 400\n    task = process_message.delay(message)\n    return jsonify({"task_id": task.id}), 202\n'''

    app_py = f'''{chr(10).join(imports)}\n\napp = Flask(__name__)\n{chr(10).join(setup)}\n\n@app.get("/")\ndef index():\n    return """<!doctype html><html><head><title>{name}</title></head><body style='font-family:system-ui;max-width:720px;margin:60px auto'><h1>{name}</h1><p>Created and managed locally by term_5.</p><p><a href='/health'>Health</a></p></body></html>"""\n\n@app.get("/health")\ndef health():\n    try:\n        {health_checks[0] if health_checks else 'pass'}\n        return jsonify({{"status": "ok"}})\n    except Exception as exc:\n        return jsonify({{"status": "error", "detail": str(exc)}}), 503\n{task_endpoint}\nif __name__ == "__main__":\n    app.run(host="0.0.0.0", port=5000)\n'''

    tasks_py = '''import os\nfrom celery import Celery\n\ncelery = Celery(\n    "term5_tasks",\n    broker=os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"),\n    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1"),\n)\n\n@celery.task\ndef process_message(message: str):\n    return {"processed": message, "length": len(message)}\n'''

    service_env = []
    depends = []
    if with_mongo:
        service_env += ["      MONGO_URL: mongodb://mongo:27017/", "      MONGO_DB: app"]
        depends.append("      mongo:\n        condition: service_healthy")
    if with_celery:
        service_env += ["      CELERY_BROKER_URL: redis://redis:6379/0", "      CELERY_RESULT_BACKEND: redis://redis:6379/1"]
        depends.append("      redis:\n        condition: service_healthy")

    compose = f'''services:\n  web:\n    build: .\n    command: gunicorn -b 0.0.0.0:5000 app:app\n    ports:\n      - "127.0.0.1:{port}:5000"\n    environment:\n{chr(10).join(service_env) if service_env else '      PYTHONUNBUFFERED: "1"'}\n    healthcheck:\n      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=3)"]\n      interval: 5s\n      timeout: 4s\n      retries: 20\n      start_period: 5s\n'''
    if depends:
        compose += "    depends_on:\n" + "\n".join(depends) + "\n"
    if with_celery:
        compose += f'''  worker:\n    build: .\n    command: celery -A tasks.celery worker --loglevel=INFO\n    environment:\n      CELERY_BROKER_URL: redis://redis:6379/0\n      CELERY_RESULT_BACKEND: redis://redis:6379/1\n'''
        if with_mongo:
            compose += "      MONGO_URL: mongodb://mongo:27017/\n      MONGO_DB: app\n"
        compose += "    depends_on:\n      redis:\n        condition: service_healthy\n"
        if with_mongo:
            compose += "      mongo:\n        condition: service_healthy\n"
        compose += '''  redis:\n    image: redis:7-alpine\n    healthcheck:\n      test: ["CMD", "redis-cli", "ping"]\n      interval: 5s\n      timeout: 3s\n      retries: 20\n'''
    if with_mongo:
        compose += '''  mongo:\n    image: mongo:8\n    volumes:\n      - mongo_data:/data/db\n    healthcheck:\n      test: ["CMD", "mongosh", "--quiet", "--eval", "db.adminCommand('ping').ok"]\n      interval: 5s\n      timeout: 5s\n      retries: 20\n'''
    if with_mongo:
        compose += "volumes:\n  mongo_data:\n"

    dockerfile = '''FROM python:3.12-slim\nENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1\nWORKDIR /app\nCOPY requirements.txt .\nRUN pip install --no-cache-dir -r requirements.txt\nCOPY . .\nCMD ["gunicorn", "-b", "0.0.0.0:5000", "app:app"]\n'''
    manifest = f'''[app]\nname = "{name}"\nruntime = "docker-compose"\ncompose_project = "{slug}"\nentry_service = "web"\nurl = "http://127.0.0.1:{port}"\nhealth_url = "http://127.0.0.1:{port}/health"\n'''
    readme = f'''# {name}\n\nGenerated locally by term_5 5.9.0.\n\n- Web: http://127.0.0.1:{port}\n- Health: http://127.0.0.1:{port}/health\n- Runtime: Docker Compose\n'''
    files = {
        "app.py": app_py,
        "requirements.txt": "\n".join(requirements) + "\n",
        "Dockerfile": dockerfile,
        "compose.yaml": compose,
        ".dockerignore": ".git\n.term5\n__pycache__\n*.pyc\n.venv\nvenv\n",
        "term5.app.toml": manifest,
        "README.md": readme,
    }
    if with_celery:
        files["tasks.py"] = tasks_py
    return files
