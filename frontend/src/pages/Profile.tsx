import { useEffect, useState } from "react";
import { User, api } from "../api";
import MondayBoardPicker from "./MondayBoardPicker";
import Usage from "./Usage";

export default function Profile({ user, onSaved }: { user: User; onSaved: (u: User) => void }) {
  const [autoSend, setAutoSend] = useState(user.auto_send);
  const [ccMe, setCcMe] = useState(user.cc_me);
  const [signature, setSignature] = useState(user.signature);
  const [msg, setMsg] = useState("");

  const save = async () => {
    try {
      onSaved(await api.updateMe({ auto_send: autoSend, cc_me: ccMe, signature }));
      setMsg("Preferencias guardadas");
    } catch (e) {
      setMsg((e as Error).message);
    }
  };

  return (
    <>
      <h2>Perfil</h2>
      <div className="card">
        <p>
          <b>{user.name}</b> · {user.email}
        </p>
        <label className="check">
          <input type="checkbox" checked={autoSend} onChange={(e) => setAutoSend(e.target.checked)} />
          Enviar la minuta automáticamente a los asistentes en cuanto esté lista (sin revisarla antes)
        </label>
        <label className="check">
          <input type="checkbox" checked={ccMe} onChange={(e) => setCcMe(e.target.checked)} />
          Recibir copia de cada minuta que envío
        </label>
        <label>Firma para los correos</label>
        <textarea rows={3} value={signature} onChange={(e) => setSignature(e.target.value)} />
        <div className="actions">
          <button className="btn primary" onClick={save}>
            Guardar
          </button>
          {msg && <span className="muted">{msg}</span>}
        </div>
      </div>
      {user.monday_habilitado && <MondayCard user={user} onSaved={onSaved} />}
      <Usage />
    </>
  );
}

const RESULTADO_CONEXION: Record<string, string> = {
  conectado: "Cuenta de Monday conectada.",
  cancelado: "No se conectó Monday: se canceló la autorización.",
  expirado: "La autorización tardó demasiado. Intenta conectar de nuevo.",
  error: "Monday no aceptó la conexión. Intenta de nuevo.",
};

/** Cada persona conecta su propia cuenta de Monday y elige el tablero donde van sus acuerdos. */
function MondayCard({ user, onSaved }: { user: User; onSaved: (u: User) => void }) {
  const conn = user.monday_conexion;
  const [busy, setBusy] = useState(false);
  const [token, setToken] = useState("");
  const [msg, setMsg] = useState(() => {
    const r = window.location.hash.match(/[?&]monday=(\w+)/)?.[1];
    return r ? RESULTADO_CONEXION[r] ?? "" : "";
  });

  useEffect(() => {
    if (window.location.hash.includes("monday=")) history.replaceState(null, "", "#/perfil");
  }, []);

  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    setMsg("");
    try {
      await fn();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const connect = () =>
    act(async () => {
      window.location.href = (await api.mondayConnectUrl()).url;
    });

  const saveToken = (e: React.FormEvent) => {
    e.preventDefault();
    act(async () => {
      onSaved(await api.mondaySaveToken(token.trim()));
      setToken("");
      setMsg("Cuenta de Monday conectada. Ahora elige tu tablero.");
    });
  };

  const disconnect = () => {
    if (!confirm("¿Desconectar tu cuenta de Monday? Ya no podrás subir acuerdos hasta que la conectes de nuevo.")) return;
    act(async () => onSaved(await api.mondayDisconnect()));
  };

  const choose = (id: string) =>
    act(async () => {
      onSaved(await api.mondayChooseBoard(id));
      setMsg("Tablero guardado");
    });

  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>Monday</h3>
      {!conn ? (
        <>
          <p>
            Conecta tu cuenta de Monday para subir los acuerdos de tus minutas a tu propio tablero. Solo verás los
            tableros a los que ya tienes acceso, y los acuerdos aparecerán en Monday a tu nombre.
          </p>
          {user.monday_oauth && (
            <div className="actions">
              <button className="btn primary" disabled={busy} onClick={connect}>
                Conectar Monday
              </button>
              <span className="muted small">o pega tu token personal:</span>
            </div>
          )}
          <form onSubmit={saveToken}>
            <label htmlFor="monday-token">Tu token personal de Monday</label>
            <input
              id="monday-token"
              type="password"
              autoComplete="off"
              spellCheck={false}
              placeholder="Pega aquí tu token"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              disabled={busy}
            />
            <p className="muted small">
              En Monday: tu foto de perfil → <b>Desarrolladores</b> → <b>Mis tokens de acceso</b> → <b>Mostrar</b> →{" "}
              <b>Copiar</b>. El token se prueba con Monday, se guarda cifrado y nadie lo puede volver a ver, ni tú. Da
              acceso a lo mismo que tu cuenta de Monday, así que no lo compartas; si lo regeneras en Monday, pega el
              nuevo aquí.
            </p>
            <div className="actions">
              <button className="btn primary" type="submit" disabled={busy || token.trim().length < 20}>
                {busy ? "Probando con Monday…" : "Guardar token"}
              </button>
              {msg && <span className="muted">{msg}</span>}
            </div>
          </form>
        </>
      ) : (
        <>
          <p>
            Conectada como <b>{conn.cuenta || "tu cuenta de Monday"}</b>.
          </p>
          <label>Tablero donde se suben tus acuerdos</label>
          <MondayBoardPicker value={conn.tablero_id ?? ""} onChange={choose} disabled={busy} />
          {conn.tablero_url && (
            <p className="small">
              <a href={conn.tablero_url} target="_blank" rel="noreferrer">
                Abrir {conn.tablero} en Monday
              </a>
            </p>
          )}
          <p className="muted small">
            Al subir acuerdos por primera vez, se agregan al tablero las columnas que falten (Responsable, Fecha
            límite, Estado, Minuta y Notas de la minuta). Puedes cambiar de tablero en cada minuta.
          </p>
          <div className="actions">
            <button className="btn" disabled={busy} onClick={disconnect}>
              Desconectar Monday
            </button>
            {msg && <span className="muted">{msg}</span>}
          </div>
        </>
      )}
    </div>
  );
}
