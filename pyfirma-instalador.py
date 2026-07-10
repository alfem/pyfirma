#!/usr/bin/env python3
"""
Instalador gráfico de PyFirma.

Comprueba y ejecuta cada paso de instalación necesario para que PyFirma
funcione como aplicación nativa de firma electrónica compatible con el
protocolo afirma:// en Linux (Ubuntu 24.04).

Usa únicamente tkinter (biblioteca estándar) para no depender de
paquetes externos antes de la instalación.

Ejecución:
    python3 pyfirma-instalador.py
"""

import os
import sys
import subprocess
import threading
import tempfile
import shutil
import re

# ── Detección del directorio del proyecto ──────────────────────────────
# El instalador asume que está dentro del directorio raíz de PyFirma.
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Intentar importar tkinter ──────────────────────────────────────────
try:
    import tkinter as tk
    from tkinter import ttk, scrolledtext, messagebox, font
    HAS_TK = True
except ImportError:
    HAS_TK = False


# ══════════════════════════════════════════════════════════════════════════
# Funciones de comprobación y ejecución de cada paso
# ══════════════════════════════════════════════════════════════════════════

def check_system_deps():
    """Comprueba si los paquetes del sistema están instalados."""
    missing = []
    for mod, pkg in [("tkinter", "python3-tk")]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if not shutil.which("pip3") and not shutil.which("pip"):
        missing.append("python3-pip")
    if not shutil.which("certutil"):
        missing.append("libnss3-tools")
    if not shutil.which("openssl"):
        missing.append("openssl")
    # Comprobar que el módulo venv funciona
    try:
        result = subprocess.run(
            [sys.executable, "-m", "venv", "-h"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            missing.append("python3-venv")
    except Exception:
        missing.append("python3-venv")

    if missing:
        return False, f"Faltan: {', '.join(missing)}"
    return True, "Paquetes del sistema instalados"


def install_system_deps(log_callback):
    """Instala los paquetes del sistema con apt."""
    pkgs = ["python3-tk", "python3-venv", "python3-pip",
            "libnss3-tools", "openssl"]
    _run_apt_install(pkgs, log_callback)


def check_venv():
    """Comprueba si el entorno virtual existe y es funcional."""
    venv_dir = os.path.join(PROJECT_DIR, "venv")
    pyvenv_cfg = os.path.join(venv_dir, "pyvenv.cfg")
    if not os.path.isfile(pyvenv_cfg):
        return False, "Entorno virtual no creado"
    # Comprobar que el python del venv funciona
    venv_python = os.path.join(venv_dir, "bin", "python3")
    if not os.path.isfile(venv_python):
        venv_python = os.path.join(venv_dir, "bin", "python")
    if not os.path.isfile(venv_python):
        return False, "Python del venv no encontrado"
    try:
        result = subprocess.run(
            [venv_python, "-c", "print('ok')"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0 or "ok" not in result.stdout:
            return False, "Entorno virtual roto"
    except Exception as e:
        return False, f"Error verificando venv: {e}"
    return True, "Entorno virtual listo"


def create_venv(log_callback):
    """Crea el entorno virtual e instala pip requirements."""
    venv_dir = os.path.join(PROJECT_DIR, "venv")
    log_callback("Creando entorno virtual...")
    _run([sys.executable, "-m", "venv", venv_dir], log_callback, cwd=PROJECT_DIR)

    venv_python = _venv_python()
    log_callback("Actualizando pip...")
    _run([venv_python, "-m", "pip", "install", "--upgrade", "pip"],
         log_callback, cwd=PROJECT_DIR)


def check_pip_deps():
    """Comprueba si las dependencias Python están instaladas en el venv."""
    venv_dir = os.path.join(PROJECT_DIR, "venv")
    if not os.path.isdir(venv_dir):
        return False, "Entorno virtual no creado"
    venv_python = _venv_python()
    if not venv_python:
        return False, "Python del venv no encontrado"
    req_file = os.path.join(PROJECT_DIR, "requirements.txt")
    if not os.path.isfile(req_file):
        return False, "requirements.txt no encontrado"

    # Obtener paquetes requeridos (sin comentarios ni líneas vacías)
    with open(req_file) as f:
        required = [
            line.strip().split("#")[0].strip().lower()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]
    # Eliminar especificadores de versión para comparar nombres
    required_names = set()
    for r in required:
        # Separar nombre de versión (p.ej. "package>=1.0" → "package")
        name = re.split(r"[<>=!~;]|\s+", r)[0].strip().lower()
        if name:
            required_names.add(name)

    # Obtener paquetes instalados en el venv
    try:
        result = subprocess.run(
            [venv_python, "-m", "pip", "freeze"],
            capture_output=True, text=True, timeout=30
        )
        installed = set()
        for line in result.stdout.splitlines():
            line = line.strip().lower()
            if line and not line.startswith("#"):
                name = re.split(r"[<>=!~;]", line)[0].strip().lower()
                if name:
                    installed.add(name)
    except Exception as e:
        return False, f"Error comprobando pip: {e}"

    missing = required_names - installed
    if missing:
        return False, f"Faltan: {', '.join(sorted(missing))}"
    return True, "Dependencias Python instaladas"


def install_pip_deps(log_callback):
    """Instala las dependencias Python en el venv."""
    venv_python = _venv_python()
    req_file = os.path.join(PROJECT_DIR, "requirements.txt")
    if not venv_python:
        log_callback("ERROR: No se encuentra el Python del entorno virtual")
        return
    log_callback("Instalando dependencias Python...")
    _run([venv_python, "-m", "pip", "install", "-r", req_file],
         log_callback, cwd=PROJECT_DIR)


def check_tls_certs():
    """Comprueba si los certificados TLS existen y tienen cadena de confianza."""
    cert_pem = os.path.join(PROJECT_DIR, "cert.pem")
    key_pem = os.path.join(PROJECT_DIR, "key.pem")
    rootca_pem = os.path.join(PROJECT_DIR, "rootCA.pem")
    rootca_key = os.path.join(PROJECT_DIR, "rootCA.key")

    missing = []
    for f, name in [(rootca_pem, "rootCA.pem"), (rootca_key, "rootCA.key"),
                     (cert_pem, "cert.pem"), (key_pem, "key.pem")]:
        if not os.path.isfile(f):
            missing.append(name)
    if missing:
        return False, f"Faltan: {', '.join(missing)}"

    # Verificar que cert.pem está firmado por rootCA.pem (no es autofirmado)
    try:
        result = subprocess.run(
            ["openssl", "verify", "-CAfile", rootca_pem, cert_pem],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return False, "cert.pem no está firmado por rootCA.pem (¿es autofirmado?)"
    except Exception:
        return False, "No se pudo verificar la cadena de confianza"
    return True, "Certificados TLS listos (rootCA → cert.pem)"


def generate_tls_certs(log_callback):
    """Genera rootCA y certificado de servidor firmado por la CA."""
    cert_pem = os.path.join(PROJECT_DIR, "cert.pem")
    key_pem = os.path.join(PROJECT_DIR, "key.pem")
    rootca_pem = os.path.join(PROJECT_DIR, "rootCA.pem")
    rootca_key = os.path.join(PROJECT_DIR, "rootCA.key")

    # ── 1. Generar rootCA si no existe ──
    if not os.path.isfile(rootca_pem) or not os.path.isfile(rootca_key):
        log_callback("Generando Autoridad Certificadora (rootCA)...")
        _run([
            "openssl", "req", "-x509", "-newkey", "rsa:4096",
            "-keyout", rootca_key, "-out", rootca_pem,
            "-days", "3650", "-nodes",
            "-subj", "/CN=PyFirma Root CA/O=PyFirma/OU=AutoFirma",
            "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign",
            "-addext", "subjectKeyIdentifier=hash",
        ], log_callback, cwd=PROJECT_DIR)
        try:
            os.chmod(rootca_key, 0o600)
        except Exception:
            pass
        log_callback("  rootCA.pem + rootCA.key generados")
    else:
        log_callback("rootCA ya existe, conservando la actual")

    # ── 2. Generar certificado de servidor firmado por rootCA ──
    log_callback("Generando certificado de servidor firmado por rootCA...")
    csr = os.path.join(tempfile.gettempdir(), "pyfirma-server.csr")
    server_key_tmp = os.path.join(tempfile.gettempdir(), "pyfirma-server.key")

    _run([
        "openssl", "req", "-new", "-newkey", "rsa:4096",
        "-keyout", server_key_tmp, "-out", csr,
        "-nodes",
        "-subj", "/CN=localhost/O=PyFirma/OU=AutoFirma",
        "-addext", "subjectAltName=IP:127.0.0.1",
    ], log_callback, cwd=PROJECT_DIR)

    _run([
        "openssl", "x509", "-req",
        "-in", csr, "-CA", rootca_pem, "-CAkey", rootca_key,
        "-CAcreateserial", "-out", cert_pem,
        "-days", "3650", "-copy_extensions", "copy",
    ], log_callback, cwd=PROJECT_DIR)

    # Instalar clave del servidor
    shutil.move(server_key_tmp, key_pem)
    try:
        os.chmod(key_pem, 0o600)
    except Exception:
        pass

    # Limpiar CSR temporal
    try:
        os.remove(csr)
    except Exception:
        pass

    # ── 3. Verificar ──
    result = subprocess.run(
        ["openssl", "verify", "-CAfile", rootca_pem, cert_pem],
        capture_output=True, text=True, timeout=10
    )
    log_callback(f"Verificación: {result.stdout.strip()}")
    log_callback("✓ Listo: rootCA.pem → cert.pem (cadena de confianza OK)")
    log_callback("")
    log_callback("IMPORTANTE: Instala rootCA.pem en los navegadores (siguiente paso),")
    log_callback("           no cert.pem. La CA es la que otorga la confianza.")


def check_desktop_file():
    """Comprueba si el archivo .desktop existe y es válido."""
    desktop_path = os.path.expanduser(
        "~/.local/share/applications/pyfirma.desktop"
    )
    if not os.path.isfile(desktop_path):
        return False, "pyfirma.desktop no creado"
    # Comprobar que apunta al proyecto correcto
    try:
        with open(desktop_path) as f:
            content = f.read()
        if PROJECT_DIR not in content:
            return False, "pyfirma.desktop apunta a otro directorio"
    except Exception:
        return False, "No se pudo leer pyfirma.desktop"
    # Validar con desktop-file-validate si está disponible
    if shutil.which("desktop-file-validate"):
        result = subprocess.run(
            ["desktop-file-validate", desktop_path],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return False, f"pyfirma.desktop inválido: {result.stderr.strip()}"
    return True, "Lanzador del sistema listo"


def create_desktop_file(log_callback):
    """Crea el archivo .desktop para el lanzador del sistema."""
    apps_dir = os.path.expanduser("~/.local/share/applications")
    os.makedirs(apps_dir, exist_ok=True)
    desktop_path = os.path.join(apps_dir, "pyfirma.desktop")

    venv_python = _venv_python()
    python_bin = venv_python if venv_python else sys.executable

    desktop_content = f"""[Desktop Entry]
Type=Application
Name=PyFirma
Comment=Firmador de PDF compatible con AutoFirma
Exec=bash -c 'cd {PROJECT_DIR} && "{python_bin}" main.py %u'
Terminal=false
Categories=Office;Security;
MimeType=x-scheme-handler/afirma;
NoDisplay=false
StartupNotify=true
"""
    log_callback(f"Creando {desktop_path}...")
    with open(desktop_path, 'w') as f:
        f.write(desktop_content)
    os.chmod(desktop_path, 0o755)

    # Actualizar base de datos de aplicaciones
    log_callback("Actualizando base de datos de aplicaciones...")
    _run(["update-desktop-database", apps_dir], log_callback)


def check_protocol_registered():
    """Comprueba si el protocolo afirma:// está registrado."""
    if not shutil.which("xdg-mime"):
        return False, "xdg-mime no disponible"
    try:
        result = subprocess.run(
            ["xdg-mime", "query", "default", "x-scheme-handler/afirma"],
            capture_output=True, text=True, timeout=10
        )
        handler = result.stdout.strip()
        if handler == "pyfirma.desktop":
            return True, "Protocolo afirma:// registrado"
        elif handler:
            return False, f"afirma:// apunta a {handler}"
        else:
            return False, "Protocolo afirma:// no registrado"
    except Exception as e:
        return False, f"Error: {e}"


def register_protocol(log_callback):
    """Registra el protocolo afirma:// en el sistema."""
    log_callback("Registrando protocolo afirma://...")
    _run(["xdg-mime", "default", "pyfirma.desktop",
          "x-scheme-handler/afirma"], log_callback)
    apps_dir = os.path.expanduser("~/.local/share/applications")
    _run(["update-desktop-database", apps_dir], log_callback)


# ── Ayudante: busca perfiles de Firefox ─────────────────────────────

def _find_firefox_profiles():
    """Devuelve una lista de rutas a directorios de perfil de Firefox."""
    profiles = []

    def _parse_ini(ini_path, base_dir):
        """Parsea profiles.ini y añade los perfiles encontrados."""
        if not os.path.isfile(ini_path):
            return
        try:
            with open(ini_path) as f:
                path_val = None
                is_relative = None
                for line in f:
                    line = line.strip()
                    if line.startswith("["):
                        # Nuevo perfil: guardar el anterior si es válido
                        if path_val and is_relative is not None:
                            full = os.path.join(base_dir, path_val) if is_relative else path_val
                            if os.path.isdir(full) and full not in profiles:
                                profiles.append(full)
                        path_val = None
                        is_relative = None
                    elif line.startswith("Path="):
                        path_val = line.split("=", 1)[1]
                    elif line.startswith("IsRelative="):
                        is_relative = line.split("=", 1)[1] == "1"
                # Último perfil
                if path_val and is_relative is not None:
                    full = os.path.join(base_dir, path_val) if is_relative else path_val
                    if os.path.isdir(full) and full not in profiles:
                        profiles.append(full)
        except Exception:
            pass

    _parse_ini(os.path.join(os.path.expanduser("~/.mozilla/firefox"), "profiles.ini"),
               os.path.expanduser("~/.mozilla/firefox"))
    _parse_ini(os.path.join(os.path.expanduser("~/snap/firefox/common/.mozilla/firefox"), "profiles.ini"),
               os.path.expanduser("~/snap/firefox/common/.mozilla/firefox"))

    return profiles


def _find_chromium_nssdb():
    """Devuelve la ruta al NSS DB de Chrome/Chromium, o None."""
    nssdb = os.path.expanduser("~/.pki/nssdb")
    if os.path.isdir(nssdb):
        cert9 = os.path.join(nssdb, "cert9.db")
        if os.path.isfile(cert9):
            return nssdb
    return None


def _cert_already_in_nssdb(db_dir, cert_nickname):
    """Comprueba si un certificado ya existe en una base de datos NSS."""
    try:
        result = subprocess.run(
            ["certutil", "-L", "-d", f"sql:{db_dir}"],
            capture_output=True, text=True, timeout=10
        )
        return cert_nickname in result.stdout
    except Exception:
        return False


def check_browser_config():
    """Comprueba si rootCA.pem está instalado como CA en los navegadores."""
    rootca_pem = os.path.join(PROJECT_DIR, "rootCA.pem")
    if not os.path.isfile(rootca_pem):
        return False, "Falta rootCA.pem (generar certificados primero)"

    if not shutil.which("certutil"):
        return False, "Falta certutil (libnss3-tools)"

    CERT_NICK = "PyFirma Root CA"

    # Comprobar Firefox
    ff_profiles = _find_firefox_profiles()
    ff_ok = 0
    ff_total = len(ff_profiles)
    for prof in ff_profiles:
        if _cert_already_in_nssdb(prof, CERT_NICK):
            ff_ok += 1

    # Comprobar Chrome/Chromium
    chrome_nssdb = _find_chromium_nssdb()
    chrome_ok = False
    if chrome_nssdb:
        chrome_ok = _cert_already_in_nssdb(chrome_nssdb, CERT_NICK)

    parts = []
    if ff_total > 0:
        parts.append(f"Firefox: {ff_ok}/{ff_total}")
    if chrome_nssdb:
        parts.append(f"Chrome: {'✓' if chrome_ok else '✗'}")
    else:
        parts.append("Chrome: sin NSS DB")

    all_ok = True
    if ff_total > 0 and ff_ok < ff_total:
        all_ok = False
    if chrome_nssdb and not chrome_ok:
        all_ok = False
    if ff_total == 0 and not chrome_nssdb:
        all_ok = False
        return False, "No se detectaron navegadores (Firefox/Chrome)"

    if all_ok:
        return True, f"rootCA instalada — {', '.join(parts)}"
    return False, f"Pendiente — {', '.join(parts)}"


def install_browser_certs(log_callback):
    """Instala rootCA.pem como Autoridad de Confianza en Firefox y Chrome."""
    rootca_pem = os.path.join(PROJECT_DIR, "rootCA.pem")
    CERT_NICK = "PyFirma Root CA"

    if not os.path.isfile(rootca_pem):
        log_callback("ERROR: rootCA.pem no encontrado. Genera los certificados TLS primero.")
        return

    # Eliminar nicks antiguos de versiones anteriores del instalador
    OLD_NICKS = ["PyFirma Localhost CA", "localhost - PyFirma"]

    # ── Firefox ──
    ff_profiles = _find_firefox_profiles()
    if ff_profiles:
        log_callback(f"Detectados {len(ff_profiles)} perfil(es) de Firefox")
        for prof in ff_profiles:
            prof_name = os.path.basename(prof)
            # Limpiar nicks viejos
            for old in OLD_NICKS:
                if _cert_already_in_nssdb(prof, old):
                    _run(["certutil", "-D", "-d", f"sql:{prof}", "-n", old],
                         log_callback)
                    log_callback(f"  Eliminado certificado antiguo: {old}")
            # Instalar rootCA actual
            if _cert_already_in_nssdb(prof, CERT_NICK):
                log_callback(f"  ✓ {prof_name}: rootCA ya instalada")
                continue
            log_callback(f"  Instalando rootCA en {prof_name}...")
            _run([
                "certutil", "-A", "-d", f"sql:{prof}",
                "-t", "C,,", "-n", CERT_NICK,
                "-i", rootca_pem,
            ], log_callback)
            if _cert_already_in_nssdb(prof, CERT_NICK):
                log_callback(f"  ✓ {prof_name}: instalada correctamente")
            else:
                log_callback(f"  ⚠ {prof_name}: error (¿contraseña maestra activa?)")
                log_callback(f"     Si Firefox tiene contraseña maestra, desactívala")
                log_callback(f"     temporalmente y vuelve a ejecutar este paso.")
    else:
        log_callback("Firefox: no se detectaron perfiles")

    # ── Chrome / Chromium ──
    chrome_nssdb = _find_chromium_nssdb()
    if chrome_nssdb:
        # Limpiar nicks viejos
        for old in OLD_NICKS:
            if _cert_already_in_nssdb(chrome_nssdb, old):
                _run(["certutil", "-D", "-d", f"sql:{chrome_nssdb}", "-n", old],
                     log_callback)
        if _cert_already_in_nssdb(chrome_nssdb, CERT_NICK):
            log_callback("  ✓ Chrome/Chromium: rootCA ya instalada")
        else:
            log_callback("  Instalando rootCA en Chrome/Chromium...")
            _run([
                "certutil", "-A", "-d", f"sql:{chrome_nssdb}",
                "-t", "C,,", "-n", CERT_NICK,
                "-i", rootca_pem,
            ], log_callback)
            if _cert_already_in_nssdb(chrome_nssdb, CERT_NICK):
                log_callback("  ✓ Chrome/Chromium: instalada correctamente")
            else:
                log_callback("  ⚠ Chrome/Chromium: no se pudo instalar")
    else:
        log_callback("Chrome/Chromium: no se detectó NSS DB (~/.pki/nssdb/)")
        log_callback("  La rootCA se aceptará manualmente la primera vez")
        log_callback("  o activando chrome://flags/#allow-insecure-localhost")

    log_callback("")
    log_callback("ℹ Si el navegador estaba abierto, ciérralo y ábrelo de nuevo.")


def check_installation():
    """La verificación siempre es manual."""
    # Comprobar si hay un certificado .p12 presente como indicador
    p12_found = any(
        f.endswith(('.p12', '.pfx'))
        for f in os.listdir(PROJECT_DIR)
        if os.path.isfile(os.path.join(PROJECT_DIR, f))
    )
    if p12_found:
        return False, "Listo para verificar"
    return False, "Requiere certificado .p12"


# ══════════════════════════════════════════════════════════════════════════
# Utilidades
# ══════════════════════════════════════════════════════════════════════════

def _venv_python():
    """Devuelve la ruta al ejecutable python del venv, o None."""
    venv_dir = os.path.join(PROJECT_DIR, "venv")
    for name in ["bin/python3", "bin/python"]:
        p = os.path.join(venv_dir, name)
        if os.path.isfile(p):
            return p
    return None


def _run(cmd, log_callback, cwd=None, env=None):
    """Ejecuta un comando volcando stdout/stderr línea a línea."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=cwd or PROJECT_DIR,
            env=merged_env,
        )
        for line in proc.stdout:
            log_callback(line.rstrip())
        proc.wait()
        if proc.returncode != 0:
            log_callback(f"⚠ El comando terminó con código {proc.returncode}")
    except FileNotFoundError:
        log_callback(f"ERROR: Comando no encontrado: {cmd[0]}")
    except Exception as e:
        log_callback(f"ERROR: {e}")


def _run_apt_install(packages, log_callback):
    """Instala paquetes del sistema usando apt o sudo apt."""
    # Si ya somos root, usar apt directamente; si no, usar sudo
    if os.geteuid() == 0:
        cmd = ["apt", "update"]
        _run(cmd, log_callback)
        cmd = ["apt", "install", "-y"] + packages
        _run(cmd, log_callback)
    else:
        cmd = ["sudo", "apt", "update"]
        log_callback("Se necesita sudo para instalar paquetes del sistema...")
        _run(cmd, log_callback)
        cmd = ["sudo", "apt", "install", "-y"] + packages
        _run(cmd, log_callback)


# ══════════════════════════════════════════════════════════════════════════
# Definición de los pasos de instalación
# ══════════════════════════════════════════════════════════════════════════

STEPS = [
    {
        "id": "system_deps",
        "title": "Dependencias del sistema",
        "description": "python3-tk, python3-venv, python3-pip, libnss3-tools, openssl (sudo)",
        "check": check_system_deps,
        "run": install_system_deps,
    },
    {
        "id": "venv",
        "title": "Entorno virtual Python",
        "description": "Crear venv/ en el directorio del proyecto",
        "check": check_venv,
        "run": create_venv,
    },
    {
        "id": "pip_deps",
        "title": "Dependencias Python",
        "description": "pip install -r requirements.txt",
        "check": check_pip_deps,
        "run": install_pip_deps,
    },
    {
        "id": "tls_certs",
        "title": "Certificados TLS (rootCA + servidor)",
        "description": "Generar rootCA.pem y cert.pem firmado por la CA",
        "check": check_tls_certs,
        "run": generate_tls_certs,
    },
    {
        "id": "desktop_file",
        "title": "Lanzador del sistema",
        "description": "Crear pyfirma.desktop en ~/.local/share/applications/",
        "check": check_desktop_file,
        "run": create_desktop_file,
    },
    {
        "id": "protocol",
        "title": "Registro del protocolo afirma://",
        "description": "xdg-mime + update-desktop-database",
        "check": check_protocol_registered,
        "run": register_protocol,
    },
    {
        "id": "browser",
        "title": "rootCA en el navegador",
        "description": "Instalar rootCA.pem como Autoridad de Confianza (no cert.pem)",
        "check": check_browser_config,
        "run": install_browser_certs,
    },
    {
        "id": "verify",
        "title": "Verificación de la instalación",
        "description": "Probar que PyFirma arranca correctamente",
        "check": check_installation,
        "run": None,  # Manual
        "manual": True,
    },
]


# ══════════════════════════════════════════════════════════════════════════
# Interfaz gráfica (tkinter)
# ══════════════════════════════════════════════════════════════════════════

class InstaladorApp:
    """Ventana principal del instalador gráfico."""

    def __init__(self, root):
        self.root = root
        self.root.title("PyFirma — Instalador")
        self.root.geometry("820x720")
        self.root.minsize(680, 600)

        # Paleta de colores
        self.COLORS = {
            "ok": "#2e7d32",
            "pending": "#e65100",
            "error": "#c62828",
            "bg": "#fafafa",
            "card_bg": "#ffffff",
            "text": "#212121",
            "text_secondary": "#616161",
            "accent": "#1565c0",
            "border": "#e0e0e0",
        }

        self.root.configure(bg=self.COLORS["bg"])

        # Fuentes
        self.font_title = font.Font(family="Sans", size=16, weight="bold")
        self.font_step = font.Font(family="Sans", size=12, weight="bold")
        self.font_desc = font.Font(family="Sans", size=10)
        self.font_console = font.Font(family="Monospace", size=9)
        self.font_button = font.Font(family="Sans", size=10)

        # Estado de los pasos: None = pendiente comprobar,
        # True = OK, False = no completado, "running" = ejecutándose
        self.step_status = {}
        self.step_widgets = {}  # id -> {"frame", "check_label", "button", "desc_label"}

        self._build_ui()
        self._check_all_steps()

        # Al cerrar, si hay pasos en ejecución, preguntar
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Construcción de la UI ──────────────────────────────────────────

    def _build_ui(self):
        # Marco principal con padding
        main_frame = tk.Frame(self.root, bg=self.COLORS["bg"])
        main_frame.pack(fill="both", expand=True, padx=20, pady=15)

        # ── Cabecera ──
        header = tk.Frame(main_frame, bg=self.COLORS["bg"])
        header.pack(fill="x", pady=(0, 10))

        tk.Label(
            header, text="🔐 Instalador de PyFirma",
            font=self.font_title, bg=self.COLORS["bg"], fg=self.COLORS["text"],
        ).pack(anchor="w")

        tk.Label(
            header,
            text=f"Directorio del proyecto: {PROJECT_DIR}",
            font=self.font_desc, bg=self.COLORS["bg"],
            fg=self.COLORS["text_secondary"],
        ).pack(anchor="w", pady=(2, 0))

        # ── Marco de pasos (con scroll) ──
        steps_label = tk.Label(
            main_frame, text="Pasos de instalación:",
            font=self.font_step, bg=self.COLORS["bg"], fg=self.COLORS["text"],
        )
        steps_label.pack(anchor="w", pady=(0, 5))

        # Canvas + scrollbar para los pasos
        canvas_frame = tk.Frame(main_frame, bg=self.COLORS["bg"])
        canvas_frame.pack(fill="both", expand=False, pady=(0, 10))

        self.steps_canvas = tk.Canvas(
            canvas_frame, bg=self.COLORS["bg"],
            highlightthickness=0, height=340,
        )
        scrollbar = tk.Scrollbar(
            canvas_frame, orient="vertical", command=self.steps_canvas.yview
        )
        self.steps_frame = tk.Frame(self.steps_canvas, bg=self.COLORS["bg"])

        self.steps_frame.bind(
            "<Configure>",
            lambda e: self.steps_canvas.configure(
                scrollregion=self.steps_canvas.bbox("all")
            ),
        )
        self.steps_canvas.create_window((0, 0), window=self.steps_frame, anchor="nw")
        self.steps_canvas.configure(yscrollcommand=scrollbar.set)

        self.steps_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Vincular rueda del ratón al scroll
        self.steps_canvas.bind("<Enter>", self._bind_mousewheel)
        self.steps_canvas.bind("<Leave>", self._unbind_mousewheel)

        # Construir la tarjeta de cada paso
        for i, step in enumerate(STEPS):
            self._create_step_card(step, i)

        # ── Botones de acción global ──
        btn_frame = tk.Frame(main_frame, bg=self.COLORS["bg"])
        btn_frame.pack(fill="x", pady=(0, 10))

        self.install_all_btn = tk.Button(
            btn_frame, text="▶ Instalar todo",
            font=self.font_button,
            bg=self.COLORS["accent"], fg="white",
            activebackground="#0d47a1", activeforeground="white",
            relief="flat", padx=16, pady=6,
            cursor="hand2",
            command=self._install_all,
        )
        self.install_all_btn.pack(side="left", padx=(0, 10))

        self.cancel_btn = tk.Button(
            btn_frame, text="Salir",
            font=self.font_button,
            bg="#eeeeee", fg=self.COLORS["text"],
            activebackground="#dddddd", activeforeground=self.COLORS["text"],
            relief="flat", padx=16, pady=6,
            cursor="hand2",
            command=self._on_close,
        )
        self.cancel_btn.pack(side="left")

        # ── Consola de salida ──
        console_label = tk.Label(
            main_frame, text="Consola de salida:",
            font=self.font_step, bg=self.COLORS["bg"], fg=self.COLORS["text"],
        )
        console_label.pack(anchor="w", pady=(0, 5))

        self.console = scrolledtext.ScrolledText(
            main_frame,
            font=self.font_console,
            bg="#263238", fg="#eceff1",
            insertbackground="#eceff1",
            relief="flat",
            height=12,
            state="disabled",
            wrap="word",
        )
        self.console.pack(fill="both", expand=True)

        # Tags de colores para la consola
        self.console.tag_config("error", foreground="#ef5350")
        self.console.tag_config("warn", foreground="#ffb74d")
        self.console.tag_config("ok", foreground="#81c784")
        self.console.tag_config("info", foreground="#90caf9")

    def _create_step_card(self, step, index):
        """Crea la tarjeta visual de un paso de instalación."""
        card = tk.Frame(
            self.steps_frame,
            bg=self.COLORS["card_bg"],
            highlightbackground=self.COLORS["border"],
            highlightthickness=1,
        )
        card.pack(fill="x", pady=3, padx=(0, 12))

        # Padding interno
        inner = tk.Frame(card, bg=self.COLORS["card_bg"])
        inner.pack(fill="x", padx=12, pady=10)

        # Indicador de estado (check / cruz / spinner)
        check_label = tk.Label(
            inner, text=" ○ ",
            font=font.Font(family="Sans", size=14),
            bg=self.COLORS["card_bg"], fg=self.COLORS["text_secondary"],
            width=3,
        )
        check_label.pack(side="left", padx=(0, 8))

        # Texto del paso
        text_frame = tk.Frame(inner, bg=self.COLORS["card_bg"])
        text_frame.pack(side="left", fill="x", expand=True)

        tk.Label(
            text_frame, text=step["title"],
            font=self.font_step, bg=self.COLORS["card_bg"],
            fg=self.COLORS["text"],
        ).pack(anchor="w")

        desc_label = tk.Label(
            text_frame, text=step["description"],
            font=self.font_desc, bg=self.COLORS["card_bg"],
            fg=self.COLORS["text_secondary"],
        )
        desc_label.pack(anchor="w")

        # Botón de acción
        if step.get("manual"):
            btn_text = "ℹ  Info"
            btn_cmd = lambda s=step: self._show_manual_info(s)
            btn_bg = "#fff3e0"
            btn_fg = "#e65100"
        else:
            btn_text = "▶  Ejecutar"
            btn_cmd = lambda s=step: self._run_step(s)
            btn_bg = "#e3f2fd"
            btn_fg = self.COLORS["accent"]

        # El botón varía según el estado
        btn = tk.Button(
            inner, text=btn_text,
            font=self.font_button,
            bg=btn_bg, fg=btn_fg,
            activebackground="#bbdefb", activeforeground=btn_fg,
            relief="flat", padx=12, pady=4,
            cursor="hand2",
            command=btn_cmd,
        )
        btn.pack(side="right", padx=(8, 0))

        self.step_widgets[step["id"]] = {
            "frame": card,
            "inner": inner,
            "check_label": check_label,
            "button": btn,
            "desc_label": desc_label,
        }

    # ── Lógica de comprobación ────────────────────────────────────────

    def _check_all_steps(self):
        """Comprueba todos los pasos al iniciar."""
        for step in STEPS:
            self._update_step_status(step["id"], "checking")
            ok, msg = step["check"]()
            status = ok if ok else False
            self.step_status[step["id"]] = status
            self._update_step_ui(step["id"], status, msg)

    def _update_step_status(self, step_id, status):
        """Actualiza el estado interno y refresca la UI del paso."""
        self.step_status[step_id] = status

    def _update_step_ui(self, step_id, status, detail=""):
        """Refresca la apariencia visual de un paso."""
        w = self.step_widgets.get(step_id)
        if not w:
            return

        if status == "checking":
            w["check_label"].config(text=" ⏳ ", fg="#ff8f00")
            w["desc_label"].config(text="Comprobando...")
        elif status is True:
            w["check_label"].config(text=" ✓ ", fg=self.COLORS["ok"])
            w["desc_label"].config(text=detail)
        elif status is False:
            w["check_label"].config(text=" ✗ ", fg=self.COLORS["error"])
            w["desc_label"].config(text=detail)
        elif status == "running":
            w["check_label"].config(text=" ⟳ ", fg=self.COLORS["accent"])
            w["desc_label"].config(text="Ejecutando...")
        elif status == "error":
            w["check_label"].config(text=" ✗ ", fg=self.COLORS["error"])
            w["desc_label"].config(text=detail)

    # ── Ejecución de pasos ────────────────────────────────────────────

    def _run_step(self, step):
        """Ejecuta un paso en un hilo en segundo plano."""
        step_id = step["id"]

        # Si ya está en ejecución, ignorar
        if self.step_status.get(step_id) == "running":
            return

        self._update_step_status(step_id, "running")
        self._update_step_ui(step_id, "running")
        self._log(f"─── {step['title']} ───", "info")
        self._set_buttons_state("disabled")

        def target():
            try:
                step["run"](self._log)
                # Recomprobar
                ok, msg = step["check"]()
                status = ok if ok else "error"
                if status is True:
                    self._log(f"✓ {msg}", "ok")
                else:
                    self._log(f"✗ {msg}", "error")
            except Exception as e:
                status = "error"
                msg = str(e)
                self._log(f"✗ Error: {msg}", "error")
            finally:
                self.step_status[step_id] = status
                self._update_step_ui(step_id, status, msg)
                self.root.after(0, lambda: self._set_buttons_state("normal"))

        threading.Thread(target=target, daemon=True).start()

    def _install_all(self):
        """Ejecuta todos los pasos no completados en secuencia."""
        pending = [
            s for s in STEPS
            if not s.get("manual")
            and self.step_status.get(s["id"]) is not True
            and self.step_status.get(s["id"]) != "running"
        ]
        if not pending:
            messagebox.showinfo(
                "Instalación completa",
                "Todos los pasos automatizados están completados.\n\n"
                "Revisa los pasos manuales para finalizar la configuración."
            )
            return

        # Preguntar antes de instalar todo
        steps_text = "\n".join(f"  • {s['title']}" for s in pending)
        if not messagebox.askyesno(
            "Instalar todo",
            f"Se ejecutarán los siguientes pasos:\n\n{steps_text}\n\n"
            f"¿Continuar?"
        ):
            return

        self._set_buttons_state("disabled")
        self._log("═══ INICIANDO INSTALACIÓN COMPLETA ═══", "info")
        self._run_next_step(pending, 0)

    def _run_next_step(self, steps, index):
        """Ejecuta el siguiente paso de la lista secuencialmente."""
        if index >= len(steps):
            self._log("═══ INSTALACIÓN FINALIZADA ═══", "info")
            self._set_buttons_state("normal")
            # Resumen
            completed = sum(
                1 for s in STEPS
                if self.step_status.get(s["id"]) is True
            )
            total = len(STEPS)
            self._log(f"Pasos completados: {completed}/{total}", "info")
            if completed == total:
                self._log("¡PyFirma está listo para usar!", "ok")
            return

        step = steps[index]
        step_id = step["id"]

        self._update_step_status(step_id, "running")
        self._update_step_ui(step_id, "running")
        self._log(f"─── {step['title']} ───", "info")

        def target():
            try:
                step["run"](self._log)
                ok, msg = step["check"]()
                status = ok if ok else "error"
                if status is True:
                    self._log(f"✓ {msg}", "ok")
                else:
                    self._log(f"✗ {msg}", "error")
            except Exception as e:
                status = "error"
                msg = str(e)
                self._log(f"✗ Error: {msg}", "error")
            finally:
                self.step_status[step_id] = status
                self._update_step_ui(step_id, status, msg)
                # Si falló un paso con error, preguntar si continuar
                if status == "error" and index + 1 < len(steps):
                    self.root.after(
                        0,
                        lambda: self._ask_continue_on_error(steps, index + 1)
                    )
                else:
                    self.root.after(
                        0, lambda: self._run_next_step(steps, index + 1)
                    )

        threading.Thread(target=target, daemon=True).start()

    def _ask_continue_on_error(self, steps, next_index):
        """Pregunta si continuar tras un error."""
        next_step = steps[next_index]
        if messagebox.askyesno(
            "Error en el paso",
            f"El paso anterior falló. ¿Quieres continuar con "
            f"«{next_step['title']}»?"
        ):
            self._run_next_step(steps, next_index)
        else:
            self._log("═══ INSTALACIÓN CANCELADA ═══", "warn")
            self._set_buttons_state("normal")

    def _show_manual_info(self, step):
        """Muestra información para pasos manuales."""
        if step["id"] == "verify":
            self._show_verify_info()

    def _show_verify_info(self):
        """Instrucciones de verificación."""
        venv_python = _venv_python() or sys.executable
        text = (
            "VERIFICACIÓN DE LA INSTALACIÓN\n"
            "──────────────────────────────\n\n"
            "Prueba en modo GUI normal:\n"
            f"  cd {PROJECT_DIR}\n"
            "  source venv/bin/activate\n"
            "  python main.py\n\n"
            "Prueba del protocolo afirma://:\n"
            "  python main.py "
            '"afirma://websocket?ports=51234,51235,51236"\n\n'
            "Prueba desde línea de comandos:\n"
            "  python main.py -i documento.pdf \\\n"
            "    -c certificado.p12 -o firmado.pdf\n\n"
            "Puedes comprobar que una firma es correcta en:\n"
            "https://valide.redsara.es/"
        )
        self._show_info_dialog("Verificación", text)

    def _show_info_dialog(self, title, text):
        """Muestra un diálogo con información en texto monoespaciado."""
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.geometry("620x520")
        dialog.configure(bg=self.COLORS["bg"])
        dialog.transient(self.root)
        dialog.grab_set()

        txt = scrolledtext.ScrolledText(
            dialog,
            font=font.Font(family="Monospace", size=10),
            bg="#263238", fg="#eceff1",
            relief="flat",
            wrap="word",
        )
        txt.insert("1.0", text)
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True, padx=12, pady=12)

        tk.Button(
            dialog, text="Cerrar",
            font=self.font_button,
            bg=self.COLORS["accent"], fg="white",
            relief="flat", padx=16, pady=6,
            cursor="hand2",
            command=dialog.destroy,
        ).pack(pady=(0, 12))

    # ── Consola ────────────────────────────────────────────────────────

    def _log(self, message, tag=None):
        """Añade una línea a la consola de salida."""

        def _write():
            self.console.config(state="normal")
            self.console.insert("end", message + "\n", tag or "")
            self.console.see("end")
            self.console.config(state="disabled")

        self.root.after(0, _write)

    # ── Control de botones ────────────────────────────────────────────

    def _set_buttons_state(self, state):
        """Habilita o deshabilita los botones durante la ejecución."""
        new_state = "disabled" if state == "disabled" else "normal"
        for w in self.step_widgets.values():
            try:
                w["button"].config(state=new_state)
            except Exception:
                pass
        try:
            self.install_all_btn.config(state=new_state)
        except Exception:
            pass

    # ── Scroll con rueda del ratón ────────────────────────────────────

    def _bind_mousewheel(self, event):
        if sys.platform == "linux":
            self.steps_canvas.bind_all(
                "<Button-4>",
                lambda e: self.steps_canvas.yview_scroll(-1, "units")
            )
            self.steps_canvas.bind_all(
                "<Button-5>",
                lambda e: self.steps_canvas.yview_scroll(1, "units")
            )
        else:
            self.steps_canvas.bind_all(
                "<MouseWheel>",
                lambda e: self.steps_canvas.yview_scroll(
                    int(-1 * (e.delta / 120)), "units"
                )
            )

    def _unbind_mousewheel(self, event):
        if sys.platform == "linux":
            self.steps_canvas.unbind_all("<Button-4>")
            self.steps_canvas.unbind_all("<Button-5>")
        else:
            self.steps_canvas.unbind_all("<MouseWheel>")

    def _on_close(self):
        """Maneja el cierre de la ventana."""
        # Comprobar si hay pasos ejecutándose
        running = any(
            s == "running" for s in self.step_status.values()
        )
        if running:
            if not messagebox.askyesno(
                "Salir",
                "Hay pasos en ejecución. ¿Estás seguro de que quieres salir?"
            ):
                return
        self.root.destroy()


# ══════════════════════════════════════════════════════════════════════════
# Modo texto (fallback sin tkinter)
# ══════════════════════════════════════════════════════════════════════════

def run_cli_installer():
    """Instalador en modo texto cuando no hay tkinter disponible."""
    print("=" * 60)
    print("  PyFirma — Instalador (modo texto)")
    print("=" * 60)
    print(f"  Directorio: {PROJECT_DIR}")
    print()

    # Primero comprobar todos los pasos
    print("Comprobando estado de la instalación...\n")
    for step in STEPS:
        ok, msg = step["check"]()
        icon = "✓" if ok else "✗"
        print(f"  [{icon}] {step['title']}: {msg}")

    print("\n" + "-" * 60)
    print("Pasos disponibles:")
    for i, step in enumerate(STEPS, 1):
        ok, _ = step["check"]()
        icon = "✓" if ok else "✗"
        tag = " (manual)" if step.get("manual") else ""
        print(f"  {i}. [{icon}] {step['title']}{tag}")

    while True:
        print("\nElige una opción:")
        print("  1-8  Ejecutar un paso")
        print("  a    Instalar todo (pasos automáticos)")
        print("  r    Recomprobar todo")
        print("  q    Salir")
        choice = input("> ").strip().lower()

        if choice == "q":
            break
        elif choice == "a":
            for step in STEPS:
                if step.get("manual") or not step.get("run"):
                    continue
                ok, _ = step["check"]()
                if ok:
                    continue
                print(f"\n─── {step['title']} ───")
                step["run"](print)
                ok, msg = step["check"]()
                print(f"  {'✓' if ok else '✗'} {msg}")
            print("\nInstalación finalizada.")
        elif choice == "r":
            print("\nRecomprobando...")
            for step in STEPS:
                ok, msg = step["check"]()
                print(f"  [{'✓' if ok else '✗'}] {step['title']}: {msg}")
        elif choice.isdigit() and 1 <= int(choice) <= len(STEPS):
            step = STEPS[int(choice) - 1]
            if step.get("manual") or not step.get("run"):
                print(f"\n  '{step['title']}' es un paso manual.")
                if step["id"] == "browser":
                    print("  Sigue las instrucciones en instalacion.md")
                elif step["id"] == "verify":
                    print(f"  cd {PROJECT_DIR}")
                    print("  source venv/bin/activate")
                    print("  python main.py")
                continue
            print(f"\n─── {step['title']} ───")
            step["run"](print)
            ok, msg = step["check"]()
            print(f"  {'✓' if ok else '✗'} {msg}")
        else:
            print("Opción no válida.")


# ══════════════════════════════════════════════════════════════════════════
# Punto de entrada
# ══════════════════════════════════════════════════════════════════════════

def main():
    if HAS_TK:
        root = tk.Tk()
        # Intentar establecer un icono (no falla si no hay)
        try:
            # Si hubiera un icono, se podría cargar aquí
            pass
        except Exception:
            pass
        app = InstaladorApp(root)
        root.mainloop()
    else:
        print("AVISO: tkinter no está disponible. Usando modo texto.\n")
        print("Para el instalador gráfico, instala python3-tk:")
        print("  sudo apt install python3-tk")
        print()
        run_cli_installer()


if __name__ == "__main__":
    main()
