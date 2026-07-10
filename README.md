# 🎓 Monitor de materias UADE

Bot que vigila la disponibilidad (vacantes) de una o varias materias en el portal
de inscripciones de UADE y te **avisa por notificación push** (vía [ntfy](https://ntfy.sh))
apenas aparece cupo. Corre solo en **GitHub Actions** cada 5 minutos — no necesita
tu computadora prendida.

## ¿Cómo funciona?

1. Cada 5 min, un disparador ([cron-job.org](https://cron-job.org)) ejecuta el workflow en GitHub.
2. El bot hace login en UADE (OAuth Microsoft + auth del portal de inscripciones).
3. Busca tu materia con el turno y día que configuraste y lee la columna **Vacantes**.
4. Si tu comisión tiene cupo → te manda un push con un link para inscribirte, y deja de avisar por esa materia.

## 🚀 Puesta en marcha (usá el configurador)

La forma más fácil es abrir **`configurador.html`** (doble clic, se abre en tu navegador):
completás tus datos y te genera todo listo para copiar y pegar. Todo se genera
**localmente en tu navegador**; nada se envía a ningún lado.

Pasos:

1. **Copiá este repo:** botón **“Use this template” → Create a new repository**.
2. **Abrí `configurador.html`** y completá: materias (código, turno, día, y opcionalmente la clase/comisión), tu email y usuario de UADE, y un topic de ntfy.
3. **Pegá el `config.json`** que genera en el archivo `config.json` de tu repo (se edita desde la web de GitHub).
4. **Cargá los Secrets** en tu repo (*Settings → Secrets and variables → Actions*):
   | Secret | Valor |
   |---|---|
   | `UADE_EMAIL` | tu email `@uade.edu.ar` |
   | `UADE_USUARIO` | tu usuario de login |
   | `UADE_PASSWORD` | tu contraseña de UADE |
   | `NTFY_TOPIC` | el topic que elegiste |
5. **Suscribite al topic** en la app **ntfy** (iOS/Android) para recibir los avisos.
6. **Creá un token** fine-grained en GitHub (*Settings → Developer settings → Personal access tokens → Fine-grained*), con acceso **solo a tu repo** y permiso **Actions: Read and write**.
7. **Configurá cron-job.org** con la URL, headers (con tu token) y body que te da el configurador, cada 5 minutos.

¡Listo! En la pestaña **Actions** de tu repo vas a ver las corridas cada ~5 min.

## 🔒 Seguridad

- Tu contraseña va **solo** a GitHub Secrets (encriptada). Nunca al código ni al `config.json`.
- El token de GitHub va **solo** a cron-job.org y conviene limitarlo a Actions de tu repo.
- Si tu cuenta UADE tiene **MFA/verificación en dos pasos**, el login automático puede fallar (el runner no puede resolverlo).

## 🧩 `config.json`

```json
{
  "cuatrimestre": "597",
  "materias": [
    { "codigo": "3.4.210", "turno": "NOCHE", "dias": ["Lunes"], "clase": "1941" }
  ]
}
```

- **codigo**: código de la materia (ej. `3.4.210`).
- **turno**: `MAÑANA`, `TARDE`, `NOCHE`, `INTENSIVO` u `ONLINE`.
- **dias**: lista de días (`Lunes`…`Sábado`).
- **clase** *(opcional pero recomendado)*: número exacto de tu comisión. El filtro turno+día
  del portal no es estricto y puede traer otras comisiones; si ponés la clase, solo esa cuenta
  para “hay cupo”. Si no la sabés, corré el bot una vez y mirá el log: lista todas las comisiones
  con su número y vacantes.
- **cuatrimestre**: código interno del período (`597` = 2do cuatri 2026). Si cambia el período
  y no encuentra la materia, el log muestra los cuatrimestres disponibles.

## 🖥️ Uso local (opcional, para probar)

```bash
pip install -r requirements.txt
playwright install chromium
# crear un archivo .env con UADE_EMAIL, UADE_USUARIO, UADE_PASSWORD, NTFY_TOPIC
python3 monitor_materia.py --once   # un chequeo
python3 monitor_materia.py          # loop continuo cada 5 min
```

## Créditos

Hecho con 🤖 [Claude Code](https://claude.com/claude-code).
