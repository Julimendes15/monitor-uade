# 🎓 Monitor de materias UADE

Bot que vigila la disponibilidad (vacantes) de una o varias materias en el portal
de inscripciones de UADE y te **avisa por notificación push** (vía [ntfy](https://ntfy.sh))
apenas aparece cupo. Corre solo en **GitHub Actions** cada 5 minutos — **no necesita
tu computadora prendida**.

---

## 📌 ¿Cómo funciona? (resumen)

1. Cada 5 min, un disparador ([cron-job.org](https://cron-job.org)) ejecuta el workflow en GitHub.
2. El bot hace login en UADE (OAuth Microsoft + auth del portal de inscripciones).
3. Busca tu materia con el turno y día que configuraste y lee la columna **Vacantes**.
4. Si tu comisión tiene cupo → te manda un push con un link para inscribirte, y deja de avisar por esa materia.

---

## ✅ Requisitos previos (crealos antes de empezar)

- Una cuenta de **GitHub** (gratis) → https://github.com
- La app **ntfy** en tu celular (gratis, iOS/Android) → buscá "ntfy" en la tienda
- Una cuenta gratis en **cron-job.org** → https://cron-job.org

---

## 🚀 Guía paso a paso

### Paso 1 — Copiá el repo a tu cuenta

1. Entrá a **https://github.com/Julimendes15/monitor-uade**
2. Tocá el botón verde **“Use this template” → “Create a new repository”**.
3. Ponele un nombre (ej. `monitor-uade`), dejalo **Public** o **Private** (cualquiera sirve; *Public* no gasta minutos de Actions) y creá el repo.

### Paso 2 — Abrí el configurador

El archivo **`configurador.html`** es un formulario que te genera toda la config. Para abrirlo:

1. En tu repo nuevo, tocá el botón verde **“Code” → “Download ZIP”**.
2. Descomprimí el ZIP y hacé **doble clic en `configurador.html`** → se abre en tu navegador.
3. Completá tus datos (materia, turno, día, email/usuario UADE, topic de ntfy) y tocá **“Generar configuración”**.

> Te va a dar 3 bloques para copiar: el **`config.json`**, la **tabla de Secrets** y la **config de cron-job.org**. Dejá esa pestaña abierta, la vas a usar en los pasos que siguen.

### Paso 3 — Pegá tu `config.json`

1. En tu repo (web de GitHub), entrá al archivo **`config.json`**.
2. Tocá el ✏️ (editar), **borrá todo** y **pegá** el `config.json` que te generó el configurador.
3. Abajo, **“Commit changes”**.

Ejemplo de cómo queda:
```json
{
  "cuatrimestre": "597",
  "materias": [
    { "codigo": "3.4.210", "turno": "NOCHE", "dias": ["Lunes"], "clase": "1941" }
  ]
}
```

### Paso 4 — Cargá los Secrets (tus credenciales)

En tu repo: **Settings → Secrets and variables → Actions → “New repository secret”**. Creá estos **4**:

| Secret | Valor |
|---|---|
| `UADE_EMAIL` | tu email `@uade.edu.ar` |
| `UADE_USUARIO` | tu usuario de login (el que usás antes de la contraseña) |
| `UADE_PASSWORD` | tu contraseña de UADE |
| `NTFY_TOPIC` | el topic que inventaste (ej. `uade-juan-9f3k2`) |

> 🔒 La contraseña se escribe **directo acá**, nunca en el código ni en `config.json`.

### Paso 5 — Suscribite en ntfy (para recibir los avisos)

1. Abrí la app **ntfy** en tu celular.
2. Tocá **“+” / “Subscribe to topic”** y escribí **exactamente** el mismo topic que pusiste en `NTFY_TOPIC` (ej. `uade-juan-9f3k2`).
3. Listo — ahí van a llegar los avisos.

> Tip: mandate una prueba desde la web de ntfy o esperá el primer aviso. Las notificaciones de "sin cupo" (si activás el modo prueba) llegan con prioridad baja y pueden ser silenciosas; las de **cupo disponible** llegan con sonido.

### Paso 6 — Creá el token de GitHub

1. Andá a **https://github.com/settings/personal-access-tokens/new** (Fine-grained token).
2. **Token name:** `pinger-monitor` · **Expiration:** 90 días.
3. **Repository access:** *“Only select repositories”* → elegí **tu repo** (`monitor-uade`).
4. **Permissions → Repository permissions** (bajá con scroll) → buscá **“Actions”** → ponelo en **“Read and write”**.
5. **Generate token** y **copialo** (empieza con `github_pat_…`, se muestra una sola vez).

> ⚠️ Ese token es una clave privada: va **solo** en cron-job.org (paso 7). No lo compartas.

### Paso 7 — Configurá cron-job.org (el disparador cada 5 min)

1. Entrá a **https://cron-job.org**, creá cuenta y tocá **“Create cronjob”**.
2. **Título:** `Monitor UADE`
3. **URL:** la que te dio el configurador (tiene tu usuario/repo):
   ```
   https://api.github.com/repos/TU_USUARIO/TU_REPO/actions/workflows/monitor.yml/dispatches
   ```
4. **Programación / Schedule:** cada **5 minutos** (`*/5 * * * *`).
5. En **“Avanzado”**:
   - **Método:** `POST`
   - **Headers / Cabeceras** (3, con tu token en la segunda):
     ```
     Accept: application/vnd.github+json
     Authorization: Bearer TU_TOKEN
     X-GitHub-Api-Version: 2022-11-28
     ```
   - **Body / Cuerpo:** `{"ref":"main"}`
6. **Guardar**.

### Paso 8 — Verificá que ande

- En cron-job.org, la última ejecución debe dar **`204 No Content`** = ✅ OK.
- En tu repo, pestaña **Actions**, deberías ver corridas entrando cada ~5 min.
- Tocá una corrida → paso **“Ejecutar chequeo”** para ver el log (te dice si encontró la materia y cuántas vacantes).

¡Listo! Cuando tu comisión tenga cupo, te llega el push. 🎉

---

## 🧩 Detalle del `config.json`

- **codigo**: código de la materia (ej. `3.4.210`).
- **turno**: `MAÑANA`, `TARDE`, `NOCHE`, `INTENSIVO` u `ONLINE`.
- **dias**: lista de días (`Lunes`…`Sábado`).
- **clase** *(opcional pero recomendado)*: número exacto de tu comisión. El filtro turno+día
  del portal **no es estricto** y puede traer otras comisiones; si ponés la clase, **solo esa**
  cuenta para “hay cupo”. Si no la sabés, corré el bot una vez y mirá el log: lista todas las
  comisiones con su número y vacantes.
- **cuatrimestre**: código interno del período (`597` = 2do cuatri 2026).

---

## 🆘 Problemas comunes

| Síntoma | Causa / Solución |
|---|---|
| cron-job.org marca **`401`** | Token mal copiado o vencido → regeneralo (paso 6) y actualizá el header. |
| cron-job.org marca **`403`** | Al token le falta el permiso **Actions: Read and write** sobre tu repo. |
| cron-job.org marca **`404`** | La URL está mal (revisá usuario/repo). El workflow se llama `monitor.yml`. |
| Log dice **“Login fallido / MFA”** | Tu cuenta UADE pide verificación en dos pasos. El bot no puede resolverla solo. |
| Log dice **“Materia no aparece en el listado”** | Revisá el `codigo`, o el `cuatrimestre` (el log lista los disponibles). |
| **No llega la notificación** | Verificá que estés suscrito al **mismo** topic exacto en la app ntfy. |
| Avisa cupo de una comisión que no es la tuya | Poné el número de **clase** exacto en `config.json` (evita falsos positivos). |
| Ya conseguiste el cupo | Sacá esa materia del `config.json` (o borrala toda) y commiteá. Para frenar todo: borrá el cronjob en cron-job.org. |

---

## 🔒 Seguridad

- Tu contraseña va **solo** a GitHub Secrets (encriptada). Nunca al código ni al `config.json`.
- El token de GitHub va **solo** a cron-job.org, limitado a Actions de tu repo.
- Cada persona corre en **su propio** GitHub: nadie ve las credenciales de otro.
- Si tu cuenta UADE tiene **MFA**, el login automático puede fallar (el runner no puede resolverlo).

---

## 🖥️ Uso local (opcional, para probar sin la nube)

```bash
pip install -r requirements.txt
playwright install chromium
# crear un archivo .env con UADE_EMAIL, UADE_USUARIO, UADE_PASSWORD, NTFY_TOPIC
python3 monitor_materia.py --once   # un chequeo y termina
python3 monitor_materia.py          # loop continuo cada 5 min
```

---

Hecho con 🤖 [Claude Code](https://claude.com/claude-code).
