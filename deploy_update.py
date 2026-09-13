# -*- coding: utf-8 -*-
"""Deploy verificable del paquete souenergy-api al VPS.

Plano SE-PRICES-002 §8.1/§8.2:
- SSH por clave de usuario dedicado, `known_hosts` previamente validado,
  SIN `AutoAddPolicy` y SIN contraseña embebida. Credenciales solo por env.
- Release inmutable con MANIFEST/checksum; se verifica cada comando remoto
  (fail fast). Configuración y DB NO se sobrescriben.
- Rollback: se mantiene el puntero de la release anterior y se restaura en
  caso de fallo, con healthcheck autenticado.
- Uso:  python deploy_update.py [--rollback]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import logging
import os
import re
import shlex
import sys
import tarfile
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("deploy")

BASE = Path(__file__).resolve().parent

# Paquete completo (no solo 3 archivos). Config/DB quedan fuera.
PAQUETE = [
    "api.py", "scraper.py", "coleta.py", "db.py", "normalizacao.py",
    "catalogo.py", "exportar_precos.py", "publicar_precos.py", "alertas.py",
    "requirements.txt", "requirements-deploy.txt",
    "migrations/", "config/", "schemas/", "ops/",
]

REMOTE_BASE = os.getenv("VPS_REMOTE_BASE", "/opt/souenergy-api")
REMOTE_RELEASES = f"{REMOTE_BASE}/releases"
REMOTE_CURRENT = f"{REMOTE_BASE}/current"
REMOTE_PREVIOUS = f"{REMOTE_BASE}/previous"
REMOTE_MANIFEST = "MANIFEST.sha256"
SERVICIO = os.getenv("VPS_SERVICIO", "souenergy")
HEALTHCHECK = os.getenv("VPS_HEALTHCHECK", "http://localhost:8000/")


class DeployError(Exception):
    pass


def _requerido(nombre: str) -> str:
    valor = os.getenv(nombre)
    if not valor:
        raise DeployError(f"Falta variable de entorno: {nombre}")
    return valor


def _conectar():
    try:
        import paramiko
    except ImportError as exc:
        raise DeployError("Instale requirements-deploy.txt antes do deploy") from exc
    host = _requerido("VPS_HOST")
    user = _requerido("VPS_USER")
    clave = _requerido("VPS_SSH_KEY")
    known_hosts = _requerido("VPS_KNOWN_HOSTS")
    if not Path(clave).exists():
        raise DeployError(f"Clave SSH no encontrada: {clave}")
    if not Path(known_hosts).exists():
        raise DeployError(f"known_hosts no encontrado: {known_hosts}")
    cliente = paramiko.SSHClient()
    cliente.load_host_keys(known_hosts)  # validado, sin AutoAddPolicy
    cliente.connect(host, port=int(os.getenv("VPS_PORT", "22")), username=user,
                    key_filename=clave, timeout=30)
    return cliente


def _ejecutar(ssh, comando: str, timeout: int = 120) -> str:
    """Ejecuta un comando remoto y falla si el exit code no es 0."""
    stdin, stdout, stderr = ssh.exec_command(comando, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    codigo = stdout.channel.recv_exit_status()
    if codigo != 0:
        raise DeployError(f"Comando remoto falhou (código {codigo}); saída omitida por segurança")
    return out.strip()


def _construir_paquete() -> tuple[bytes, dict]:
    """Tar con MANIFEST (sha256 por archivo). Devuelve (tar_bytes, manifest)."""
    manifest = {}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for nombre in PAQUETE:
            ruta = BASE / nombre
            if ruta.is_dir():
                for f in sorted(ruta.rglob("*")):
                    if f.is_file() and not f.is_symlink() and "__pycache__" not in f.parts and f.suffix not in (".pyc", ".key", ".pem") and not f.name.startswith(".env"):
                        _agregar(tar, f, manifest)
            elif ruta.is_file():
                _agregar(tar, ruta, manifest)
        contenido = "\n".join(f"{h}  {n}" for n, h in sorted(manifest.items()))
        datos = io.BytesIO(contenido.encode("utf-8"))
        info = tarfile.TarInfo(REMOTE_MANIFEST)
        info.size = len(datos.getvalue())
        tar.addfile(info, datos)
    return buf.getvalue(), manifest


def _agregar(tar, ruta: Path, manifest: dict) -> None:
    rel = ruta.relative_to(BASE).as_posix()
    datos = ruta.read_bytes()
    manifest[rel] = hashlib.sha256(datos).hexdigest()
    info = tarfile.TarInfo(rel)
    info.size = len(datos)
    tar.addfile(info, io.BytesIO(datos))


def _subir(ssh, tar_bytes: bytes, release: str) -> None:
    sftp = ssh.open_sftp()
    try:
        sftp.mkdir(f"{REMOTE_RELEASES}/{release}")
        with sftp.open(f"{REMOTE_RELEASES}/{release}/paquete.tar.gz", "wb") as f:
            f.write(tar_bytes)
    finally:
        sftp.close()


def _verificar_checksums(ssh, release: str) -> None:
    _ejecutar(ssh, f"cd {shlex.quote(REMOTE_RELEASES + '/' + release)} && "
                   f"tar xzf paquete.tar.gz && sha256sum -c {REMOTE_MANIFEST}")


def _release_actual(ssh) -> str | None:
    try:
        return _ejecutar(ssh, f"readlink {shlex.quote(REMOTE_CURRENT)}")
    except DeployError:
        return None


def _healthcheck(ssh) -> bool:
    try:
        import json
        salida = _ejecutar(ssh, f"curl --fail --silent --show-error --max-time 20 {shlex.quote(HEALTHCHECK)}")
        return json.loads(salida).get("status") == "ok"
    except (DeployError, ValueError):
        return False


def _trocar_ponteiro(ssh, destino: str, ponteiro: str) -> None:
    temporario = ponteiro + ".tmp"
    _ejecutar(ssh, f"ln -sfn {shlex.quote(destino)} {shlex.quote(temporario)} && "
                   f"mv -Tf {shlex.quote(temporario)} {shlex.quote(ponteiro)}")


def _preparar_release(ssh, release: str) -> None:
    """Instala dependências isoladas e migra sem abrir navegador."""
    pasta = f"{REMOTE_RELEASES}/{release}"
    python = shlex.quote(pasta + "/venv/bin/python")
    _ejecutar(ssh, f"python3 -m venv {shlex.quote(pasta + '/venv')}")
    _ejecutar(ssh, f"{python} -m pip install -r {shlex.quote(pasta + '/requirements.txt')}", timeout=600)
    _ejecutar(ssh, f"PLAYWRIGHT_BROWSERS_PATH=/var/lib/souenergy/.cache/ms-playwright "
                   f"{python} -m playwright install chromium", timeout=600)
    codigo = "import db; c=db.conectar('/var/lib/souenergy/catalogo.sqlite3'); db.migrar(c); c.close()"
    _ejecutar(ssh, f"cd {shlex.quote(pasta)} && {python} -c {shlex.quote(codigo)}")


def _desplegar(ssh, release: str) -> None:
    _trocar_ponteiro(ssh, f"{REMOTE_RELEASES}/{release}", REMOTE_CURRENT)
    _ejecutar(ssh, f"sudo -n systemctl restart {shlex.quote(SERVICIO)}")
    time.sleep(3)
    _ejecutar(ssh, f"systemctl is-active {shlex.quote(SERVICIO)}")
    if not _healthcheck(ssh):
        raise DeployError(f"Healthcheck falló: {HEALTHCHECK}")


def _rollback(ssh, previa: str | None) -> None:
    if previa:
        _trocar_ponteiro(ssh, previa, REMOTE_CURRENT)
        _ejecutar(ssh, f"sudo -n systemctl restart {shlex.quote(SERVICIO)}")
        time.sleep(3)
        if _healthcheck(ssh):
            log.info(f"Rollback ok a {previa}")
            return
    raise DeployError("Rollback falló — intervención manual requerida")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deploy souenergy-api al VPS")
    parser.add_argument("--rollback", action="store_true",
                        help="Restaurar release anterior y salir")
    parser.add_argument("--release", default=time.strftime("%Y%m%d%H%M%S"))
    args = parser.parse_args(argv)

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.release):
        log.error("Identificador de release inválido")
        return 1
    try:
        ssh = _conectar()
    except DeployError as e:
        log.error(f"Configuración de deploy inválida: {e}")
        return 1
    try:
        previa = _release_actual(ssh)
        if args.rollback:
            anterior = _ejecutar(ssh, f"readlink {shlex.quote(REMOTE_PREVIOUS)}")
            _rollback(ssh, anterior)
            return 0

        tar_bytes, _manifest = _construir_paquete()
        _ejecutar(ssh, f"mkdir -p {shlex.quote(REMOTE_RELEASES)}")
        _subir(ssh, tar_bytes, args.release)
        _verificar_checksums(ssh, args.release)
        _preparar_release(ssh, args.release)
        try:
            _desplegar(ssh, args.release)
        except DeployError:
            log.error("Deploy falló — restaurando release anterior")
            _rollback(ssh, previa)
            return 1
        if previa:
            _trocar_ponteiro(ssh, previa, REMOTE_PREVIOUS)
        log.info(f"Deploy ok: release {args.release} -> {REMOTE_CURRENT}")
        return 0
    finally:
        ssh.close()


if __name__ == "__main__":
    sys.exit(main())
