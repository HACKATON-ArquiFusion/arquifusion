import io
import os
import sys
import time
import zipfile

import boto3
import botocore
import urllib.request

S = os.path.dirname(os.path.abspath(__file__))
FRONT = os.path.join(S, "arquifusion", "frontend")
APP = "arquifusion-panel"
RAMA = "main"

am = boto3.client("amplify", region_name="us-east-1")

apps = am.list_apps(maxResults=100).get("apps", [])
app = next((a for a in apps if a["name"] == APP), None)
if app:
    app_id = app["appId"]
    print("app existente:", app_id)
else:
    r = am.create_app(
        name=APP,
        description="Panel de operacion ArquiFusion (estatico, consume Cloud Run)",
        platform="WEB",
        customRules=[{"source": "/<*>", "target": "/index.html", "status": "404-200"}],
    )
    app_id = r["app"]["appId"]
    print("app creada:", app_id)

try:
    am.get_branch(appId=app_id, branchName=RAMA)
    print("rama existente:", RAMA)
except am.exceptions.NotFoundException:
    am.create_branch(appId=app_id, branchName=RAMA, stage="PRODUCTION")
    print("rama creada:", RAMA)

buf = io.BytesIO()
incluidos = []
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    for raiz, _, archivos in os.walk(FRONT):
        for a in archivos:
            ruta = os.path.join(raiz, a)
            rel = os.path.relpath(ruta, FRONT).replace("\\", "/")
            if rel.startswith("docs/"):
                continue
            z.write(ruta, rel)
            incluidos.append(rel)
datos = buf.getvalue()
print("zip:", round(len(datos) / 1024, 1), "KB ->", incluidos)

d = am.create_deployment(appId=app_id, branchName=RAMA)
job_id = d["jobId"]
pet = urllib.request.Request(d["zipUploadUrl"], data=datos, method="PUT")
pet.add_header("Content-Type", "application/zip")
with urllib.request.urlopen(pet, timeout=120) as r:
    print("subida HTTP", r.status)

am.start_deployment(appId=app_id, branchName=RAMA, jobId=job_id)
print("despliegue", job_id, "lanzado")

for _ in range(40):
    time.sleep(5)
    j = am.get_job(appId=app_id, branchName=RAMA, jobId=job_id)["job"]["summary"]
    print("  estado:", j["status"])
    if j["status"] in ("SUCCEED", "FAILED", "CANCELLED"):
        break

print()
print("URL: https://%s.%s.amplifyapp.com" % (RAMA, app_id))
