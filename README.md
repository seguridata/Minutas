# Minutas de reuniones

Herramienta interna que genera la minuta de una reunión de **Google Meet, Microsoft Teams o Zoom** y la envía por correo a los asistentes con los acuerdos, responsables y fechas.

## Cómo funciona

```
┌──────────────── PC del usuario ────────────────┐        ┌──────────── Servidor interno ────────────┐
│ App de escritorio (desktop/)                   │        │ API FastAPI (backend/)                   │
│ 1. Login con Google Workspace                  │──JWT──▶│ • Login Google (solo @seguridata.com)    │
│ 2. Elige la reunión del calendario             │◀───────│ • Lee eventos del calendario             │
│ 3. AVISO a participantes + confirmación        │        │ • Guarda transcripción + consentimiento  │
│ 4. Graba audio del sistema + micrófono         │        │ • La IA (Claude u OpenAI) hace la minuta │
│ 5. Transcribe local con Whisper, BORRA audio   │──texto▶│ • Envía por Gmail desde la cuenta propia │
└────────────────────────────────────────────────┘        └───────────────▲──────────────────────────┘
                                                                           │
                                       Portal web (frontend/) ─────────────┘
                                       historial, revisar/editar, enviar, perfil
```

- **Cada persona usa su propio perfil**: inicia sesión con su cuenta de Google de la empresa, solo ve sus reuniones y la minuta sale de su propio Gmail.
- **Consentimiento obligatorio**: antes de grabar, la app muestra un aviso para pegar en el chat de la reunión y leer en voz alta. No se puede iniciar sin marcar "Compartí el aviso" y "Nadie se opuso". Si alguien se opone, se cancela y se borra todo. Hay un botón para volver a copiar el aviso cuando alguien se une tarde. El backend rechaza transcripciones sin consentimiento y guarda quién lo confirmó y cuándo.
- **Privacidad**: el audio nunca sale de la PC; se transcribe localmente y se borra. Al servidor solo llega el texto.
- **Envío**: por defecto la minuta queda "Lista para enviar" y el usuario la revisa en el portal. En Perfil se puede activar el envío automático.

## Requisitos

- **Python 3.12** para la app de escritorio (`faster-whisper` aún no tiene versiones para Python 3.13+ en Windows). El backend funciona con 3.12 o superior.
- Node.js 20+ para el portal web.
- Windows 10/11 para la app de escritorio (usa WASAPI loopback para capturar el audio de la reunión).

## 1. Configurar Google Cloud (una sola vez, administrador de Workspace)

1. Crea un proyecto en [Google Cloud Console](https://console.cloud.google.com/).
2. Habilita **Gmail API** y **Google Calendar API**.
3. *Pantalla de consentimiento de OAuth* → tipo de usuario **Interno** (así no hace falta la verificación de Google y solo entran cuentas de la empresa).
   Ámbitos: `openid`, `email`, `profile`, `gmail.send`, `calendar.readonly`.
4. *Credenciales* → **ID de cliente OAuth** de tipo *Aplicación web*.
   URI de redireccionamiento autorizado: `http://localhost:8000/auth/callback` (y la URL de producción, p. ej. `https://minutas.seguridata.com/auth/callback`).

## 2. Backend

```bash
cd backend
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # y completa los valores
uvicorn app.main:app --reload --port 8000
```

Variables principales de `.env`: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ALLOWED_DOMAIN`, `SECRET_KEY`, `FERNET_KEY` (los comandos para generarlas están en el archivo) y la llave del proveedor de IA.

### Proveedor de IA: Claude u OpenAI (ChatGPT)

La minuta la puede redactar Claude (Anthropic) o un modelo de OpenAI. `AI_PROVIDER` decide cuál; si se deja vacío, se usa Anthropic cuando hay `ANTHROPIC_API_KEY` y, si no, OpenAI cuando hay `OPENAI_API_KEY`.

| Proveedor | Variables | Modelo por defecto |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY`, `CLAUDE_MODEL` | `claude-sonnet-5-5` (`claude-opus-5-5` da más calidad, a cerca del doble de costo) |
| `openai` | `OPENAI_API_KEY`, `OPENAI_MODEL` | `gpt-5.5` (`gpt-5.4` o `gpt-5-mini` son más baratos) |

- La llave de OpenAI se crea en **platform.openai.com → API keys** y se cobra por uso. Una suscripción de ChatGPT Plus o Team no sirve para esto.
- Con OpenAI la llamada se hace con `store=False`, así que OpenAI no guarda la transcripción ni la minuta en su historial de respuestas.
- El cambio aplica a las minutas que se generen o regeneren después de reiniciar el backend. El costo se registra por modelo, así que el panel de administrador muestra lo de cada proveedor por separado. Los precios de OpenAI están en `backend/app/services/minutes.py`; si un modelo no está en la tabla, sus generaciones se cuentan como "sin precio".

Para producción usa PostgreSQL en `DATABASE_URL` (instala `psycopg[binary]`) y sirve la API con HTTPS.

## 3. Portal web

```bash
cd frontend
copy .env.example .env   # VITE_API_URL apunta al backend
npm install
npm run dev
```

## 4. App de escritorio

```bash
cd desktop
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Configuración opcional por variables de entorno:

| Variable | Default | Uso |
|---|---|---|
| `MINUTAS_BACKEND_URL` | `http://localhost:8000` | URL del backend |
| `MINUTAS_WEB_URL` | `http://localhost:5173` | URL del portal |
| `MINUTAS_WHISPER_MODEL` | `small` | `base` es más rápido; `medium` o `large-v3` son más precisos (mejor con GPU) |
| `MINUTAS_WHISPER_DEVICE` | `auto` | `cpu` o `cuda` |

La primera transcripción descarga el modelo de Whisper (~500 MB para `small`).

### Instalador para los empleados

`build.ps1` genera `desktop\dist\Minutas-Setup.exe`: un instalador por usuario (no pide permisos de administrador) que trae la app, el modelo `small` de Whisper y la dirección del servidor ya fija. Cada persona solo lo ejecuta e inicia sesión con Google.

```bash
pip install pyinstaller
winget install JRSoftware.InnoSetup
powershell -ExecutionPolicy Bypass -File build.ps1 -ServerUrl https://minutas.seguridata.com -Version 0.1.0
```

Sin `-ServerUrl` genera una versión de prueba contra el backend local. `-WebUrl` indica el portal cuando no está en la misma dirección que el backend. Para cambiar de servidor o publicar una versión nueva, se vuelve a generar el instalador (sube `-Version`); al instalarlo encima se actualiza la app.

## 5. Producción en Vercel

En Vercel el proyecto es uno solo: el portal se sirve desde su CDN y la API corre como función de Python (`index.py` en la raíz carga `backend/app`). Vercel da HTTPS y una dirección del tipo `https://minutas-seguridata.vercel.app`, que es lo que pide Google para iniciar sesión. Lo que no se sube está en `.vercelignore`: llaves, bases locales y la app de escritorio.

Diferencias con correr en local:

- **Base de datos Postgres.** En Vercel no se conservan archivos, así que `minutas.db` no sirve allá. El backend acepta la URL que da Neon o Vercel Postgres tal cual (`postgres://…` o `postgresql://…`).
- **La minuta se genera dentro de la petición.** Allá no hay trabajo en segundo plano: al subir una reunión, el servidor responde cuando la minuta ya está lista (hasta 300 s, `maxDuration` en `vercel.json`; en el plan Pro se puede subir a 800). Se activa solo con la variable `VERCEL=1`, que Vercel pone automáticamente.

**Pasos (una sola vez):**

1. Sube el proyecto a un repositorio **privado** de GitHub. El `.gitignore` ya deja fuera `.env`, `*.db`, `.venv` y `node_modules`.
2. En Vercel: **Add New → Project**, importa el repositorio y deja la carpeta raíz como está. `vercel.json` ya define cómo compilar el portal.
3. En el proyecto de Vercel: **Storage → Create Database → Neon (Postgres)** y conéctala al proyecto. Eso crea `DATABASE_URL`.
4. En **Settings → Environment Variables** agrega lo mismo que tienes en `backend\.env`, salvo `DATABASE_URL`:
   `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ALLOWED_DOMAIN`, `SECRET_KEY`, `FERNET_KEY` (**la misma** de tu `.env`, o no se podrá leer lo que copies), `AI_PROVIDER`, `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `ADMIN_EMAILS`, `MONDAY_ENABLED`,
   y `BACKEND_URL` y `FRONTEND_URL`, **las dos** con la dirección de Vercel (por ejemplo `https://minutas-seguridata.vercel.app`, sin `/` al final).
5. **Redeploy** para que tome las variables.
6. En Google Cloud > Credenciales, agrega `https://TU-DIRECCION/auth/callback` como URI de redirección autorizada.
7. Copia tus datos actuales a Postgres (usuarios, minutas, conexiones de Monday, consumo). En **Storage → tu base → .env.local** copia la URL de conexión y, desde `backend`:

   ```bash
   .venv\Scripts\python copiar_a_postgres.py "postgresql://…"
   ```

   Crea las tablas, copia todo y se niega a correr si la base destino ya tiene datos.
8. Genera el instalador de escritorio apuntando a la dirección de Vercel (`-ServerUrl https://TU-DIRECCION`) y repártelo. Después de esto, cada `git push` a la rama principal publica la versión nueva.

## 6. Alternativa: servidor propio (Ubuntu)

En el servidor todo corre en **un solo contenedor Docker y un solo puerto**: el backend sirve también el portal ya compilado. La base de datos queda fuera del contenedor, en `~/minutas/data/minutas.db`, así que actualizar no borra nada. La app de escritorio no va al servidor: se queda en las PCs con Windows.

**1. En tu PC con Windows**, en la carpeta del proyecto, arma un solo archivo con todo:

```bash
powershell -ExecutionPolicy Bypass -File deploy\empaquetar.ps1
```

Deja `deploy\minutas-servidor.tgz`. La primera vez **incluye las llaves (`backend\.env`) y la base**, así que pásalo solo por un canal privado (la transferencia de archivos de AnyDesk, SSH o una USB), nunca por Drive ni correo, y bórralo de ambas máquinas al terminar. Para actualizaciones usa `-SinDatos`: solo lleva código y el servidor conserva sus llaves y su base.

**2. Pásalo al Ubuntu.** Con AnyDesk: botón de transferencia de archivos de la barra superior, y lo copias a tu carpeta personal (`~`).

**3. En el Ubuntu**, en una terminal (puerto y dirección son opcionales; `--https` saca el certificado, ver abajo):

```bash
cd ~ && tar -xzf minutas-servidor.tgz && rm minutas-servidor.tgz
bash minutas/deploy/instalar.sh 8000 https://minutas.seguridata.com
```

El instalador pone Docker si falta (pide la contraseña de `sudo`), ajusta `BACKEND_URL`/`FRONTEND_URL`, fija el puerto, mueve la base a `~/minutas/data/` (solo si el servidor aún no tiene una) y levanta todo con `docker compose up -d --build`. Al terminar muestra la dirección para abrirlo. Para actualizar se repiten los mismos pasos con el paquete `-SinDatos`.

**Alternativa por SSH**, si tu PC alcanza al servidor por red, hace los tres pasos de un jalón:

```bash
powershell -ExecutionPolicy Bypass -File deploy\subir-a-ubuntu.ps1 -Servidor usuario@IP-DEL-SERVIDOR -Puerto 8000 -Url https://minutas.seguridata.com -ConDatos
```

| Qué | Dónde se cambia |
|---|---|
| Puerto | primer número de `instalar.sh`, o `PUERTO=` en `~/minutas/.env` y `docker compose up -d` |
| Llaves (IA, Google, Fernet) | `~/minutas/backend/.env` y `docker compose up -d` |
| Ver errores | `cd ~/minutas && docker compose logs -f` |
| Respaldar | copiar `~/minutas/data/minutas.db` **y** guardar `FERNET_KEY` aparte: sin esa llave la base no se puede leer |

**HTTPS.** Google solo permite iniciar sesión por `http://` en `localhost`; en el servidor hace falta `https://`. Hay dos caminos:

- **Dominio visible desde internet** (puertos 80 y 443 abiertos): agrega `--https` a `instalar.sh` (o `-Https` al script por SSH). Levanta Caddy, que saca y renueva solo el certificado de Let's Encrypt para el dominio de la dirección que le diste.
- **Solo red interna**: pon delante el proxy o balanceador de la empresa con su certificado, apuntando a `http://IP-DEL-SERVIDOR:PUERTO`.

Después, en Google Cloud > Credenciales, agrega `https://TU-DOMINIO/auth/callback` como URI de redirección autorizada, y genera el instalador de escritorio con `-ServerUrl https://TU-DOMINIO`.

## Uso

1. Entra a tu reunión de Meet, Teams o Zoom.
2. Abre **Minutas**, elige la reunión de tu calendario (se llenan título y asistentes) o captúrala a mano.
3. Pulsa **Iniciar minuta**, copia el aviso, pégalo en el chat y léelo en voz alta. Si nadie se opone, marca ambas casillas y pulsa **Comenzar grabación**.
4. Al terminar, pulsa **Terminar y generar minuta**. La app transcribe en segundo plano, borra el audio y envía el texto; mientras tanto ya puedes grabar la siguiente reunión. El avance se ve en **Minutas en proceso**.
5. Revisa la minuta en el portal, corrige lo necesario y pulsa **Enviar por correo**.

¿Ya tienes la grabación? Llena título y asistentes, pulsa **Usar una grabación existente…** y elige el archivo (mp3, m4a, wav, mp4, webm, etc.). Debes declarar que los participantes sabían que se grababa; el archivo se transcribe en el equipo y no se modifica ni se borra.

Recomendación: usa audífonos para que el micrófono no capte también el audio de las bocinas.

### Acuerdos en Monday (opcional)

Cada persona conecta **su propia cuenta de Monday** desde su **Perfil** y elige ahí el tablero donde van sus acuerdos. La lista muestra solo los tableros a los que esa persona ya tiene acceso en Monday, y todo lo que se crea aparece a su nombre. En cada minuta puede elegir otro tablero solo para esa reunión.

**Conectar con el token personal (no requiere configuración):**

1. En Monday: foto de perfil → **Desarrolladores** → **Mis tokens de acceso** → **Mostrar** → **Copiar**.
2. En el portal: **Perfil** → pega el token en *Tu token personal de Monday* → **Guardar token**.
3. Elige el tablero.

El backend prueba el token con Monday antes de guardarlo y lo guarda cifrado con `FERNET_KEY`. Nunca se devuelve al portal, así que ni la propia persona lo puede volver a ver. El token da el mismo acceso que la cuenta de Monday de esa persona. Si lo regenera o lo revoca en Monday, el portal le pide conectar de nuevo. `MONDAY_ENABLED=false` en el `.env` apaga Monday para todos.

**Opcional: botón "Conectar Monday" (app OAuth, la registra un administrador de Monday):** evita copiar el token y da permisos limitados a tableros.

1. En Monday: foto de perfil → **Desarrolladores** → **Crear app**.
2. En **OAuth**:
   - Agrega la URL de redirección `{BACKEND_URL}/auth/monday/callback` (en local, `http://localhost:8000/auth/monday/callback`).
   - Marca los permisos `me:read`, `boards:read`, `boards:write`, `users:read` y `notifications:write`.
3. Copia el **Client ID** y el **Client Secret** a `MONDAY_CLIENT_ID` y `MONDAY_CLIENT_SECRET` en el `.env` del backend y reinicia el backend. El perfil muestra entonces el botón además del campo del token.

Cada envío crea en el tablero elegido un grupo con el título y la fecha de la reunión, y un elemento por acuerdo:

- **Responsable** (personas): el responsable de la minuta se busca entre los asistentes y luego en Monday por su correo. Si no se encuentra a nadie, queda sin asignar y el nombre va en **Notas de la minuta**.
- **Fecha límite**: solo si es una fecha exacta; si es texto ("la próxima semana") va en las notas.
- **Estado** empieza en "Pendiente" y **Minuta** enlaza al detalle en el portal.
- **Aviso al responsable**: cada persona asignada recibe una notificación de Monday (campana y, si lo tiene activado, correo): "Fulano te asignó un acuerdo de la minuta …". A quien sube los acuerdos no se le avisa, y tampoco hay aviso si el acuerdo quedó sin responsable. Al terminar, el portal dice a quién se avisó.

Las columnas que falten se crean solas la primera vez en cada tablero, así que la persona necesita permiso para agregar columnas en él. A Monday solo se envían los acuerdos, nunca la transcripción.

**Nada llega a Monday sin autorización del dueño de la minuta**, ni siquiera con el envío automático por correo activado. En la revisión, la columna **Monday** de la tabla de acuerdos marca cuáles se suben (todos por defecto). Al presionar el botón aparece la lista exacta de acuerdos que se crearán y la persona debe autorizarla. Un segundo envío lo advierte para no duplicar acuerdos.

### Panel de administrador

Las personas cuyo correo está en `ADMIN_EMAILS` (`.env` del backend) ven el botón **Panel de administrador** junto a su nombre. El panel es una sección aparte del perfil de usuario, con su propio menú:

- **Costos por usuario**: costo de IA del mes, personas con uso, promedio por persona, el consumo de cada persona (minutas, horas, tokens, costo y % del total) y la evolución de los últimos 6 meses.
- **Monitoreo**: costo por día, alertas (errores y minutas atoradas), estado de cada usuario, reuniones por estado y plataforma, y costo por modelo.

El perfil de cada usuario solo muestra su propio consumo. El panel no da acceso a transcripciones ni minutas de otras personas.

## Seguridad de los datos

- **Audio**: nunca sale del equipo. Mientras dura la reunión se guarda en `%TEMP%\minuta_*` cifrado con AES-256-GCM, con una llave que se genera en cada grabación y solo vive en memoria. Al transcribir se descifra al vuelo, sin copia en claro en disco. Al terminar se borra; si la app se cierra de golpe, lo que quede no se puede leer y se borra la próxima vez que se abre la app (carpetas de más de 6 horas).
- **Transcripción y minuta**: se guardan cifradas en la base con `FERNET_KEY` (igual que los tokens de Google) y solo se descifran al leerlas. Las reuniones guardadas antes del cifrado se cifran solas al arrancar el backend.
- **`FERNET_KEY` es la llave de todo el contenido**: si se pierde, las transcripciones y minutas no se pueden recuperar; si alguien la obtiene junto con la base, puede leerlas. Respáldala fuera del servidor (por ejemplo, en el gestor de secretos de la empresa) y no la cambies sin volver a cifrar.
- Título, asistentes y fechas quedan sin cifrar para poder listar las reuniones y armar el panel de administrador.

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/auth/login?client=web\|desktop&port=N` | Inicio de sesión con Google |
| GET | `/api/me` · PATCH `/api/me` | Perfil y preferencias |
| GET | `/api/calendar/current` | Reuniones en curso y las que faltan hoy |
| POST | `/api/meetings` | Sube transcripción + consentimiento; genera la minuta en segundo plano |
| GET | `/api/meetings` · `/api/meetings/{id}` | Historial y detalle |
| PATCH | `/api/meetings/{id}` | Editar minuta o destinatarios |
| POST | `/api/meetings/{id}/regenerate` · `/send` | Regenerar con IA · enviar por correo |
| POST | `/api/meetings/{id}/monday?de_nuevo=` | Crear en Monday los acuerdos autorizados (`{"acuerdos": [posiciones], "tablero_id": opcional}`) |
| PUT | `/api/monday/token` | Conectar Monday con el token personal (`{"token"}`; se prueba y se guarda cifrado) |
| GET | `/api/monday/conectar` | URL para autorizar la cuenta de Monday de la persona (solo con app OAuth) |
| GET | `/auth/monday/callback` | Regreso de Monday tras autorizar |
| GET | `/api/monday/tableros` | Tableros a los que la persona tiene acceso |
| PUT | `/api/monday/tablero` | Elegir el tablero de la persona (`{"id"}`) |
| DELETE | `/api/monday` | Desconectar la cuenta de Monday |
| DELETE | `/api/meetings/{id}` | Eliminar |
| GET | `/api/usage?mes=` | Consumo de IA propio |
| GET | `/api/admin/resumen` · `/usuarios` · `/costos` | Panel de administrador (solo `ADMIN_EMAILS`) |
