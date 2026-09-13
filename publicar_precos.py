# -*- coding: utf-8 -*-
"""Publicación idempotente de `solo-prices/precios.json` en el repo
propostas-soloenergia, con acompañamiento en `publicacoes`.

Plano SE-PRICES-002 §8.3:
- Checkout aislado y limpio; credencial de máquina restringida al repo de
  propuestas; solo archivos de datos esperados.
- Validar archivo temporal, sustituir atómicamente y crear commit SOLO cuando
  cambia el hash comercial. Nunca sobrescribir cambios humanos, ni force-push,
  ni incluir otros archivos.
- Si la branch avanzó, actualizar base y reaplicar solo el artefacto validado;
  conflicto no resuelto = error de publicación. Falha de red retoma el mismo
  artefacto sin repetir scraping ni duplicar commit (hash único en `publicacoes`).
- Push exitoso != publicación concluida: confirmar deploy (HTTP 200 + schema +
  dataset_id esperado) cuando se configura la URL pública.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import db
from exportar_precos import cargar_mapeo, validar_contrato

log = logging.getLogger("publicar")

ARCHIVO_DATOS = "solo-prices/precios.json"


class PublicationError(Exception):
    pass


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise PublicationError(
            f"git {' '.join(args)} falló: {proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


def _origen_repo(repo: Path) -> str:
    """URL remota del checkout local (GitHub); fallback al propio path.

    El publicador clona y hace push al remoto real del checkout local
    (credencial de máquina restringida al repo de propuestas, plano §8.3).
    Sin remoto configurado (testes) usa el propio path local.
    """
    # Repo bare (el "remoto" en sí, como en los tests o un mirror local):
    # clonar/pushear contra su propia ruta, no contra su origin (que apunta
    # al repo no-bare de origen y rechazaría el push por estar checked out).
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--is-bare-repository"],
            capture_output=True, text=True, timeout=30)
        if proc.returncode == 0 and proc.stdout.strip() == "true":
            return str(repo)
    except Exception:
        pass
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=30)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass
    return str(repo)


def _validar_archivo(ruta: Path) -> None:
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    errores = validar_contrato(datos)
    if errores:
        raise PublicationError(f"Contrato inválido: {'; '.join(errores[:5])}")


def _copiar_atomico(origen: Path, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(destino.parent), suffix=".tmp")
    try:
        shutil.copyfile(origen, tmp)
        os.replace(tmp, destino)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _hash_archivo(ruta: Path) -> str:
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    return datos.get("dataset_id", "")


def _verificar_deploy(url: str, hash_esperado: str) -> bool:
    """Confirma que el artefacto servido tiene el dataset_id esperado."""
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            if resp.status != 200:
                return False
            datos = json.loads(resp.read().decode("utf-8"))
        return datos.get("dataset_id") == hash_esperado
    except Exception:
        return False


def publicar(conn, ruta_json: str | os.PathLike, hash_conteudo: str,
             repo: str | os.PathLike | None = None,
             branch: str | None = None,
             url_publica: str | None = None) -> str | None:
    """Publica precios.json en propostas-soloenergia. Idempotente por hash.

    Devuelve el commit SHA (o None si ya estaba publicado / sin cambios).
    """
    ruta_json = Path(ruta_json)
    repo = Path(repo or os.getenv("REPO_PROPUESTAS", ""))
    branch = branch or os.getenv("REPO_PROPUESTAS_BRANCH", "master")

    if not repo.exists():
        raise PublicationError(
            "REPO_PROPUESTAS no existe — configurar checkout del repo de propuestas")
    if db.hash_ya_publicado(conn, hash_conteudo):
        log.info(f"Hash {hash_conteudo[:16]}… ya publicado — omitido")
        return None

    _validar_archivo(ruta_json)

    tmp = Path(tempfile.mkdtemp(prefix="pub-precios-"))
    try:
        # Clone completo (sin --depth): el push desde un clone shallow a un
        # repo bare local (como GitHub) puede fallar; el checkout aislado y
        # limpio del plano §8.3 no requiere shallow.
        _git(repo, "clone", "--branch", branch, _origen_repo(repo), str(tmp))
        # Identidad de máquina para el commit de datos: no depender de la
        # config global del entorno (contenedores/CI sin user.name/email).
        _git(tmp, "config", "user.name", "souenergy-bot")
        _git(tmp, "config", "user.email", "souenergy-bot@souenergy.com.br")
        destino = tmp / ARCHIVO_DATOS
        _copiar_atomico(ruta_json, destino)

        if _hash_archivo(destino) == hash_conteudo and _sin_cambios(tmp):
            # Sin cambio comercial: no generar commit diario
            head = _git(tmp, "rev-parse", "HEAD")
            publicacion_id = db.registrar_publicacion(
                conn, scrape_id=_scrape_id(conn, hash_conteudo),
                hash_conteudo=hash_conteudo)
            db.actualizar_publicacion(conn, publicacion_id=publicacion_id,
                                      status="enviado", commit_sha=head)
            log.info("Sin cambio comercial — sin commit nuevo")
            return head

        _git(tmp, "add", "--", ARCHIVO_DATOS)
        _git(tmp, "commit", "-m",
             f"data(solo-prices): actualizar precios.json ({hash_conteudo[:12]})")
        try:
            _git(tmp, "push", "origin", branch)
        except PublicationError:
            # Branch avanzó: actualizar base y reaplicar solo el artefacto
            _git(tmp, "pull", "--rebase", "origin", branch)
            _copiar_atomico(ruta_json, destino)
            _git(tmp, "add", "--", ARCHIVO_DATOS)
            _git(tmp, "commit", "-m",
                 f"data(solo-prices): actualizar precios.json ({hash_conteudo[:12]})")
            _git(tmp, "push", "origin", branch)

        commit_sha = _git(tmp, "rev-parse", "HEAD")
        publicacion_id = db.registrar_publicacion(
            conn, scrape_id=_scrape_id(conn, hash_conteudo),
            hash_conteudo=hash_conteudo)
        db.actualizar_publicacion(conn, publicacion_id=publicacion_id,
                                  status="enviado", commit_sha=commit_sha)
        log.info(f"Publicado commit {commit_sha[:12]} (hash {hash_conteudo[:16]}…)")

        if url_publica:
            if _verificar_deploy(url_publica, hash_conteudo):
                db.actualizar_publicacion(conn, publicacion_id=publicacion_id,
                                          status="implantado")
                log.info("Deploy confirmado (HTTP 200 + dataset_id esperado)")
            else:
                db.actualizar_publicacion(conn, publicacion_id=publicacion_id,
                                          status="erro",
                                          erro_codigo="deploy_no_confirmado")
                raise PublicationError(
                    "Push ok pero deploy no confirmado en la URL pública")
        return commit_sha
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _sin_cambios(tmp: Path) -> bool:
    proc = subprocess.run(["git", "-C", str(tmp), "status", "--porcelain"],
                          capture_output=True, text=True, timeout=60)
    return not proc.stdout.strip()


def _scrape_id(conn, hash_conteudo: str) -> str:
    """Scrape asociado a la publicación (el último aprobado)."""
    scrape = db.obtener_ultimo_scrape_aprobado(conn)
    return scrape["id"] if scrape else ""


def publicar_si_cambia(conn, ruta_json: str | os.PathLike,
                       hash_conteudo: str, **kwargs) -> str | None:
    """Wrapper: publica solo si PUBLICAR_AUTOMATICAMENTE=true."""
    if os.getenv("PUBLICAR_AUTOMATICAMENTE", "false").lower() != "true":
        log.info("Publicación automática desactivada (PUBLICAR_AUTOMATICAMENTE)")
        return None
    return publicar(conn, ruta_json, hash_conteudo, **kwargs)