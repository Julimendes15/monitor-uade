#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
monitor_materia.py

Monitorea la disponibilidad (vacantes) de una materia en el portal de
inscripciones de UADE y notifica por ntfy cuando aparece cupo.

Flujo real del portal (relevado sobre inscripciones.uade.edu.ar):
  1. Login OAuth Microsoft -> vuelve a inscripciones.uade.edu.ar
  2. El home tiene botones "¡INSCRIBITE!" con un atributo `data-linkid`
     que apunta al buscador de clases en inscripcionespia.uade.edu.ar
     (ASP.NET WebForms). Ese dominio pide autenticación HTTP -> se pasa
     con http_credentials en el contexto del browser.
  3. En el buscador: se selecciona la materia, el turno y el/los días,
     se hace "Buscar" y se lee la columna "Vacantes" de los resultados.
"""

import base64
import json
import logging
import os
import re
import time
import urllib.parse

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ─────────────────────────────────────────────────────────────────────────────
# CARGA DE .env  (parser mínimo, sin dependencias externas)
# ─────────────────────────────────────────────────────────────────────────────

def _cargar_env(ruta=".env") -> None:
    """Carga variables KEY=VALUE del archivo .env al entorno (si existe)."""
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), ruta)
    if not os.path.exists(ruta):
        return
    with open(ruta, "r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, _, valor = linea.partition("=")
            os.environ.setdefault(clave.strip(), valor.strip())


_cargar_env()


# ─────────────────────────────────────────────────────────────────────────────
# CARGA DE config.json  (qué materias monitorear — editable sin tocar código)
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_CFG = {
    "cuatrimestre": "597",
    "materias": [
        {"codigo": "3.4.210", "turno": "NOCHE", "dias": ["Lunes"], "clase": "1941"},
    ],
}


def _cargar_config(ruta="config.json") -> dict:
    """Lee config.json (materias + cuatrimestre). Usa defaults si no existe."""
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), ruta)
    cfg = {"cuatrimestre": _DEFAULT_CFG["cuatrimestre"],
           "materias": list(_DEFAULT_CFG["materias"])}
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            if data.get("cuatrimestre"):
                cfg["cuatrimestre"] = str(data["cuatrimestre"]).strip()
            norm = []
            for m in (data.get("materias") or []):
                if not m.get("codigo"):
                    continue
                norm.append({
                    "codigo": str(m["codigo"]).strip(),
                    "turno":  str(m.get("turno", "")).strip(),
                    "dias":   [str(d).strip() for d in (m.get("dias") or [])],
                    "clase":  str(m["clase"]).strip() if m.get("clase") else None,
                })
            if norm:
                cfg["materias"] = norm
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001
        print(f"[config] No pude leer config.json ({e}); uso valores por defecto.")
    return cfg


_CFG = _cargar_config()


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

# UADE
UADE_LOGIN_URL = "https://inscripciones.uade.edu.ar/Account/Login"
INSCRIPCION_URL = "https://inscripciones.uade.edu.ar/"   # link para la notificación

# Materias a monitorear — se cargan desde config.json (ver _cargar_config).
# Cada una: {codigo, turno, dias:[...], clase (opcional)}. "clase" fija la
# comisión exacta: el filtro turno+día del portal no es estricto y puede traer
# otras comisiones; si se especifica, solo esa clase cuenta para "hay cupo".
MATERIAS = _CFG["materias"]

# Ofrecimiento a usar (hay varios: MRI 1er cuatri, Asignaturas 2do cuatri, etc.)
# Se elige el link cuyo parámetro tenga este cuatrimestre. Si cambia el período
# de inscripción, actualizá este valor (o dejá que use el índice de fallback).
CUATRIMESTRE_OBJETIVO      = _CFG["cuatrimestre"]   # viene de config.json
OFRECIMIENTO_INDEX_FALLBACK = 1      # si no matchea el cuatrimestre, usa este link

# Credenciales (se leen desde .env — NO escribirlas acá)
EMAIL    = os.environ.get("UADE_EMAIL", "COMPLETAR")      # usuario@uade.edu.ar
USUARIO  = os.environ.get("UADE_USUARIO", "")             # usuario (login UADE / HTTP auth)
PASSWORD = os.environ.get("UADE_PASSWORD", "COMPLETAR")

# ntfy
NTFY_TOPIC  = os.environ.get("NTFY_TOPIC", "COMPLETAR")   # topic suscripto en la app ntfy
NTFY_SERVER = "https://ntfy.sh"

# Timing
INTERVALO_SEGUNDOS = 5 * 60   # 5 minutos
HEADLESS = True               # cambiar a False para ver el browser (debug)
TIMEOUT  = 30_000             # ms por página

# Heartbeat: si está activo, notifica también cuando NO hay cupo (para verificar
# que el monitor sigue corriendo). Se controla con la env var HEARTBEAT=1.
HEARTBEAT = os.environ.get("HEARTBEAT", "0") == "1"

# Mapa día -> id del checkbox en el buscador
_DIAS_CHECKBOX = {
    "Lunes":     "#ContentPlaceHolder1_chkLunes",
    "Martes":    "#ContentPlaceHolder1_chkMartes",
    "Miercoles": "#ContentPlaceHolder1_chkMiercoles",
    "Miércoles": "#ContentPlaceHolder1_chkMiercoles",
    "Jueves":    "#ContentPlaceHolder1_chkJueves",
    "Viernes":   "#ContentPlaceHolder1_chkViernes",
    "Sabado":    "#ContentPlaceHolder1_chkSabado",
    "Sábado":    "#ContentPlaceHolder1_chkSabado",
}


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

# Rutas absolutas (para que funcione lanzado por launchd/cron, con CWD "/")
_DIR = os.path.dirname(os.path.abspath(__file__))
_LOG_PATH  = os.path.join(_DIR, "monitor.log")


def _flag_path(codigo: str) -> str:
    """Ruta del marcador 'cupo encontrado' para una materia."""
    return os.path.join(_DIR, f"cupo_encontrado_{codigo}.flag")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_LOG_PATH, encoding="utf-8"),
    ],
)
log = logging.getLogger("monitor")


# ─────────────────────────────────────────────────────────────────────────────
# LOGIN MICROSOFT (OAuth)
# ─────────────────────────────────────────────────────────────────────────────

def login_microsoft(page) -> None:
    """
    Maneja el flujo OAuth de Microsoft para entrar a inscripciones UADE.
    Lanza excepción si el login falla (queda en microsoftonline).
    """
    log.info("Iniciando login Microsoft...")

    # 1. Ir a la página de login de UADE
    page.goto(UADE_LOGIN_URL, timeout=TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)

    # 2. Botón de inicio de sesión (redirige a Microsoft)
    try:
        page.locator("a[href*='SignIn']").first.click(timeout=TIMEOUT)
    except PlaywrightTimeoutError:
        page.get_by_text("Iniciar sesión").first.click(timeout=TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)

    # 3. Email en Microsoft
    page.wait_for_url("**/login.microsoftonline.com/**", timeout=TIMEOUT)
    page.wait_for_selector("input[name='loginfmt']", timeout=TIMEOUT)
    page.wait_for_timeout(1000)
    page.fill("input[name='loginfmt']", EMAIL)

    # 4. Siguiente
    page.click("input[type='submit']#idSIButton9", timeout=TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)

    # 4.b Posible campo "usuario" si UADE redirige a su propia página federada
    if USUARIO:
        for sel in ("input[name='username']", "input[name='UserName']",
                    "input[name='user']", "input#username", "input#userNameInput"):
            campo = page.locator(sel)
            try:
                if campo.count() > 0 and campo.first.is_visible(timeout=2_000):
                    page.wait_for_timeout(1000)
                    campo.first.fill(USUARIO)
                    log.info("Campo usuario completado")
                    break
            except PlaywrightTimeoutError:
                continue

    # 5. Contraseña (Microsoft o página federada)
    campo_pass = None
    for sel in ("input[name='passwd']", "input[name='Password']",
                "input[name='password']", "input#passwordInput",
                "input[type='password']"):
        campo = page.locator(sel)
        try:
            if campo.count() > 0 and campo.first.is_visible(timeout=3_000):
                campo_pass = campo.first
                break
        except PlaywrightTimeoutError:
            continue
    if campo_pass is None:
        raise Exception("No se encontró el campo de contraseña — el flujo de login cambió")
    page.wait_for_timeout(1000)
    campo_pass.fill(PASSWORD)

    # 6. Iniciar sesión (Microsoft o página federada)
    for sel in ("input[type='submit']#idSIButton9", "span#submitButton",
                "input#submitButton", "button[type='submit']",
                "input[type='submit']"):
        boton_login = page.locator(sel)
        try:
            if boton_login.count() > 0 and boton_login.first.is_visible(timeout=2_000):
                boton_login.first.click(timeout=TIMEOUT)
                break
        except PlaywrightTimeoutError:
            continue
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)

    # 7. "¿Mantener sesión iniciada?" -> No
    try:
        page.wait_for_selector("#idBtn_Back", timeout=5_000)
        page.wait_for_timeout(1000)
        page.click("#idBtn_Back", timeout=TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=TIMEOUT)
    except PlaywrightTimeoutError:
        log.info("Pantalla '¿Mantener sesión?' no apareció, continuando...")

    # 8. Redirección final a inscripciones UADE
    try:
        page.wait_for_url("**/inscripciones.uade.edu.ar/**", timeout=TIMEOUT)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)

    # 9. Verificar login
    if "microsoftonline" in page.url:
        raise Exception("Login fallido — verificar credenciales o si hay MFA activado")

    log.info("Login exitoso")


# ─────────────────────────────────────────────────────────────────────────────
# NAVEGACIÓN AL BUSCADOR DE CLASES
# ─────────────────────────────────────────────────────────────────────────────

def _cuatrimestre_de_link(url: str):
    """Decodifica el parámetro del data-linkid y devuelve el cuatrimestre."""
    try:
        query = urllib.parse.urlparse(url).query
        param = urllib.parse.parse_qs(query).get("param", [""])[0]
        if "-" not in param:
            return None
        b64 = param.split("-", 1)[1]
        dec = base64.b64decode(b64 + "=" * (-len(b64) % 4)).decode("latin-1", "ignore")
        m = re.search(r"paramCuatrimestre=(\d+)", dec)
        return m.group(1) if m else None
    except Exception:
        return None


def ir_a_buscador(page) -> None:
    """
    Desde el home logueado, obtiene el link del ofrecimiento correcto y
    navega al buscador de clases (inscripcionespia.uade.edu.ar).
    """
    links = page.locator("a[data-tipolink='InscripcionAsignatura']")
    n = links.count()
    if n == 0:
        raise Exception("No hay ofrecimientos de inscripción disponibles "
                        "(¿fuera del período de inscripción?)")

    ids = [links.nth(i).get_attribute("data-linkid") for i in range(n)]

    cuatris = [_cuatrimestre_de_link(lid) for lid in ids]
    log.info("Ofrecimientos disponibles (cuatrimestres): %s", cuatris)

    destino = None
    for lid, cu in zip(ids, cuatris):
        if lid and cu == CUATRIMESTRE_OBJETIVO:
            destino = lid
            log.info("Usando ofrecimiento cuatrimestre %s", cu)
            break
    if destino is None:
        idx = min(OFRECIMIENTO_INDEX_FALLBACK, n - 1)
        destino = ids[idx]
        log.info("Cuatrimestre %s no matcheó; usando ofrecimiento índice %d",
                 CUATRIMESTRE_OBJETIVO, idx)

    page.goto(destino, timeout=TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)
    page.wait_for_selector("#ContentPlaceHolder1_btnSeleccionarMaterias", timeout=TIMEOUT)


def configurar_busqueda(page, materia) -> bool:
    """
    Selecciona la materia, el turno y los días en el buscador, y ejecuta
    la búsqueda. Devuelve False si la materia no está en el listado.
    """
    codigo = materia["codigo"]
    turno  = materia["turno"]
    dias   = materia["dias"]

    # Abrir el selector de materias
    page.click("#ContentPlaceHolder1_btnSeleccionarMaterias")
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)

    # Esperar explícitamente a que la grilla de materias termine de cargar
    # (se abre por postback; en entornos lentos tarda en poblarse).
    celda_codigo = f"xpath=//td[normalize-space(.)='{codigo}']"
    try:
        page.wait_for_selector(celda_codigo, timeout=15_000)
    except PlaywrightTimeoutError:
        # Diagnóstico: ¿se abrió el modal con materias o está vacío?
        n_chk = page.locator("input[id*='chkSeleccionar']").count()
        log.warning("Materia %s no aparece en el listado de este ofrecimiento "
                    "(%d materias visibles en el selector)", codigo, n_chk)
        return False

    # Tildar el checkbox de la fila cuyo <td> es exactamente el código
    chk = page.locator(
        f"xpath=//td[normalize-space(.)='{codigo}']/ancestor::tr[1]//input[@type='checkbox']"
    ).first
    chk.check()
    page.wait_for_timeout(500)

    # Cerrar el modal (confirma la selección)
    try:
        page.get_by_role("button", name="Cerrar").first.click(timeout=TIMEOUT)
    except PlaywrightTimeoutError:
        page.locator("text=Cerrar").last.click(timeout=TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)
    page.wait_for_timeout(800)

    # Turno (por texto de la opción, más durable que el value numérico)
    try:
        page.select_option("#ContentPlaceHolder1_cboTurno", label=turno)
        page.wait_for_timeout(400)
    except Exception as e:
        log.warning("No pude seleccionar turno '%s': %s", turno, e)

    # Días
    for dia in dias:
        sel = _DIAS_CHECKBOX.get(dia)
        if sel and page.locator(sel).count():
            page.check(sel)
            page.wait_for_timeout(200)

    # Buscar
    page.click("#ContentPlaceHolder1_btnBuscar")
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)
    page.wait_for_timeout(1200)
    return True


def leer_disponibilidad(page, materia) -> bool:
    """
    Parsea la grilla de resultados y determina si hay vacantes (> 0) para
    la materia objetivo. Loggea cada clase encontrada con su estado.
    """
    codigo = materia["codigo"]
    turno  = materia["turno"]
    dias   = materia["dias"]

    grids = page.locator("[id*='grdResultados']")
    total = grids.count()

    clases = []   # (nro_clase, vacantes:int, turno)
    for i in range(total):
        g = grids.nth(i)
        filas = g.locator("tr")
        nf = filas.count()
        if nf < 2:
            continue  # grilla vacía (plantilla del repeater)

        # Encabezados
        header = filas.nth(0).locator("th, td")
        cols = [(header.nth(c).inner_text() or "").strip() for c in range(header.count())]
        try:
            idx_vac = next(k for k, t in enumerate(cols) if "Vacante" in t)
        except StopIteration:
            continue
        idx_clase = next((k for k, t in enumerate(cols) if "Clase" in t), 1)
        idx_turno = next((k for k, t in enumerate(cols) if "Turno" in t), None)

        for r in range(1, nf):
            celdas = filas.nth(r).locator("td")
            if celdas.count() <= idx_vac:
                continue
            vac_txt = (celdas.nth(idx_vac).inner_text() or "").strip()
            m = re.search(r"\d+", vac_txt)
            if not m:
                continue
            vac = int(m.group())
            nro = (celdas.nth(idx_clase).inner_text() or "").strip()
            turno_c = (celdas.nth(idx_turno).inner_text() or "").strip() if idx_turno is not None else ""
            clases.append((nro, vac, turno_c))

    if not clases:
        log.warning("Sin resultados para %s (%s / %s) — no ofertada o filtro sin coincidencias",
                    codigo, turno, ", ".join(dias))
        return False

    clase_objetivo = materia.get("clase")  # comisión exacta a vigilar (o None)

    hay_cupo = False
    objetivo_visto = False
    for nro, vac, turno_c in clases:
        estado = f"{vac} vacantes" if vac > 0 else "Sin cupo (0 vacantes)"
        # ¿esta fila es la comisión que nos interesa?
        es_objetivo = (clase_objetivo is None) or (nro == clase_objetivo)
        marca = " ◄ objetivo" if (clase_objetivo and nro == clase_objetivo) else ""
        log.info('[%s] Estado encontrado: clase %s (%s) -> "%s"%s',
                 codigo, nro, turno_c or turno, estado, marca)
        if es_objetivo:
            objetivo_visto = objetivo_visto or (clase_objetivo is not None)
            if vac > 0:
                hay_cupo = True

    if clase_objetivo and not objetivo_visto:
        log.warning("[%s] La clase objetivo %s no apareció en los resultados", codigo, clase_objetivo)

    if hay_cupo:
        log.info("✅ CUPO DISPONIBLE en materia %s (clase %s)", codigo, clase_objetivo or "cualquiera")
    else:
        log.info("[%s] ❌ Sin cupo", codigo)
    return hay_cupo


def encontrar_estado_materia(page, materia, reintentos=2) -> bool:
    """
    Orquesta la búsqueda de UNA materia dentro del portal ya logueado.
    Reintenta ante errores transitorios de navegación (cortes de red, etc.),
    para que un fallo puntual no arruine la corrida ni contagie a la siguiente.
    """
    codigo = materia["codigo"]
    log.info("Buscando materia %s (%s / %s)...",
             codigo, materia["turno"], ", ".join(materia["dias"]))

    ultimo_error = None
    for intento in range(1, reintentos + 1):
        try:
            # Volver al home para leer el link del ofrecimiento (cambia por sesión)
            page.goto(INSCRIPCION_URL, timeout=TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=TIMEOUT)
            ir_a_buscador(page)
            if not configurar_busqueda(page, materia):
                return False
            return leer_disponibilidad(page, materia)
        except Exception as e:  # noqa: BLE001
            ultimo_error = e
            if intento < reintentos:
                log.warning("[%s] Error de navegación (intento %d/%d): %s — reintento",
                            codigo, intento, reintentos, str(e).splitlines()[0])
                page.wait_for_timeout(3000)

    # Se agotaron los reintentos
    raise ultimo_error


# ─────────────────────────────────────────────────────────────────────────────
# ORQUESTACIÓN DEL CHEQUEO
# ─────────────────────────────────────────────────────────────────────────────

def chequear_disponibilidad(materias=None) -> dict:
    """
    Abre el browser (con credenciales HTTP para inscripcionespia), hace login
    una sola vez y chequea cada materia. Cierra el browser siempre.
    Devuelve {codigo: True/False} con la disponibilidad de cada una.
    """
    if materias is None:
        materias = MATERIAS

    resultados = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        try:
            context = browser.new_context(http_credentials={
                "username": USUARIO,
                "password": PASSWORD,
            })
            page = context.new_page()
            page.set_default_timeout(TIMEOUT)
            login_microsoft(page)
            for materia in materias:
                try:
                    resultados[materia["codigo"]] = encontrar_estado_materia(page, materia)
                except Exception as e:  # noqa: BLE001
                    log.error("[%s] Error en la búsqueda: %s", materia["codigo"], e)
                    resultados[materia["codigo"]] = False
        finally:
            browser.close()
    return resultados


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICACIÓN NTFY
# ─────────────────────────────────────────────────────────────────────────────

def notificar(titulo, mensaje, urgencia="high", link=INSCRIPCION_URL) -> None:
    """
    Envía una notificación push vía ntfy con un link de acceso rápido.
    Nunca rompe el loop ante error.
    - Click:   al tocar la notificación abre el link de inscripciones.
    - Actions: agrega un botón "Inscribirme" que abre el mismo link.
    """
    url = f"{NTFY_SERVER}/{NTFY_TOPIC}"
    try:
        requests.post(
            url,
            data=mensaje.encode("utf-8"),
            headers={
                "Title": titulo.encode("utf-8"),
                "Priority": urgencia,
                "Tags": "mortar_board",
                "Click": link,
                "Actions": f"view, Inscribirme, {link}, clear=true",
            },
            timeout=10,
        )
        log.info("Notificación enviada (link: %s)", link)
    except Exception as e:  # noqa: BLE001
        log.error("Error enviando notificación ntfy: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# LOOP PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def _notificar_cupo(materia) -> None:
    """Notifica cupo disponible para una materia puntual."""
    codigo = materia["codigo"]
    notificar(
        titulo=f"Cupo disponible — {codigo}",
        mensaje=f"¡Hay cupo en la materia {codigo} "
                f"({materia['turno']}, {', '.join(materia['dias'])})! "
                f"Tocá para inscribirte.",
        urgencia="high",
    )


def _resumen_materias() -> str:
    return " | ".join(f"{m['codigo']} ({m['turno']}, {', '.join(m['dias'])})"
                      for m in MATERIAS)


def loop_monitoreo() -> None:
    """Loop continuo de monitoreo. Nunca se rompe por un error de chequeo."""
    log.info("══════════════════════════════════════")
    log.info("Monitor UADE — %d materia(s)", len(MATERIAS))
    log.info("Objetivos: %s", _resumen_materias())
    log.info("Intervalo: %d minutos | ntfy: %s", INTERVALO_SEGUNDOS // 60, NTFY_TOPIC)
    log.info("══════════════════════════════════════")

    pendientes = list(MATERIAS)
    intento = 1
    while pendientes:
        log.info("Chequeo #%d", intento)
        try:
            resultados = chequear_disponibilidad(pendientes)
        except Exception as e:  # noqa: BLE001
            mensaje = str(e)
            if "MFA" in mensaje or "verificación" in mensaje.lower():
                log.error("MFA detectado — no se puede continuar automáticamente")
            else:
                log.error("Error durante el chequeo: %s", mensaje)
            log.info("Reintentando en %d minutos...", INTERVALO_SEGUNDOS // 60)
            intento += 1
            time.sleep(INTERVALO_SEGUNDOS)
            continue

        # Notificar y sacar de pendientes las que ya tienen cupo
        for materia in list(pendientes):
            if resultados.get(materia["codigo"]):
                _notificar_cupo(materia)
                pendientes.remove(materia)

        if not pendientes:
            log.info("Todas las materias notificadas. Deteniendo monitor.")
            break

        intento += 1
        time.sleep(INTERVALO_SEGUNDOS)


def ejecutar_una_vez() -> None:
    """
    Hace UN solo chequeo de todas las materias y termina (para launchd/cron).
    Cada materia con cupo ya avisado deja un marcador y se omite en adelante.
    """
    # Filtrar materias que ya tienen su marcador (cupo ya avisado)
    pendientes = [m for m in MATERIAS if not os.path.exists(_flag_path(m["codigo"]))]
    if not pendientes:
        log.info("Todas las materias ya fueron avisadas — nada que chequear. "
                 "Borrá los archivos cupo_encontrado_*.flag para reactivar.")
        return

    log.info("Chequeo puntual — %s", " | ".join(
        f"{m['codigo']} ({m['turno']}, {', '.join(m['dias'])})" for m in pendientes))
    try:
        resultados = chequear_disponibilidad(pendientes)
    except Exception as e:  # noqa: BLE001
        mensaje = str(e)
        if "MFA" in mensaje or "verificación" in mensaje.lower():
            log.error("MFA detectado — no se puede continuar automáticamente")
        else:
            log.error("Error durante el chequeo: %s", mensaje)
        return

    alguna_con_cupo = False
    for materia in pendientes:
        codigo = materia["codigo"]
        if resultados.get(codigo):
            alguna_con_cupo = True
            _notificar_cupo(materia)
            # Marcador para no volver a avisar esta materia en cada corrida
            try:
                with open(_flag_path(codigo), "w", encoding="utf-8") as f:
                    f.write(f"Cupo encontrado el {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            except Exception as e:  # noqa: BLE001
                log.error("No pude escribir el marcador de %s: %s", codigo, e)
            log.info("[%s] Marcador creado — no se volverá a chequear hasta borrarlo", codigo)

    if not alguna_con_cupo and HEARTBEAT:
        # Aviso de "sigo vivo" (baja prioridad) para verificar que el loop corre
        codigos = ", ".join(m["codigo"] for m in pendientes)
        notificar(
            titulo="Monitor activo — sin cupo",
            mensaje=f"Chequeo OK a las {time.strftime('%H:%M')}. "
                    f"Sin vacantes en: {codigos}. El monitor está corriendo.",
            urgencia="low",
        )


if __name__ == "__main__":
    import sys
    try:
        if "--once" in sys.argv:
            ejecutar_una_vez()      # un chequeo y termina (modo launchd/cron)
        else:
            loop_monitoreo()        # loop continuo (modo manual)
    except KeyboardInterrupt:
        log.info("Monitor detenido por el usuario.")


# ─────────────────────────────────────────────────────────────────────────────
# INSTALACIÓN:
#   pip install playwright requests
#   playwright install chromium
#
# USO:
#   python3 monitor_materia.py           # loop continuo (chequea cada 5 min)
#   python3 monitor_materia.py --once    # un solo chequeo y termina
#
# DEBUG (ver browser en acción):
#   Cambiar HEADLESS = False
#
# SEGUNDO PLANO SIN PROCESO CONSTANTE (macOS, recomendado):
#   Usa un LaunchAgent que corre `--once` cada 5 minutos.
#   Cargar:    launchctl load  ~/Library/LaunchAgents/com.julian.monitoruade.plist
#   Descargar: launchctl unload ~/Library/LaunchAgents/com.julian.monitoruade.plist
#   Al encontrar cupo de una materia se crea 'cupo_encontrado_<codigo>.flag'
#   y esa materia deja de chequearse. Para reactivar: borrar ese archivo.
# ─────────────────────────────────────────────────────────────────────────────
