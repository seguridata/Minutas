import { useCallback, useEffect, useState } from "react";
import { Acuerdo, Attendee, Meeting, Minuta, MondayConexion, PLATFORM_LABEL, STATUS_LABEL, api, fmtDate, fmtInt, fmtUsd } from "../api";
import MondayBoardPicker from "./MondayBoardPicker";

const linesToList = (s: string) => s.split("\n").map((l) => l.trim()).filter(Boolean);

export default function MeetingDetail({
  id,
  monday: mondayOn,
  conexion,
}: {
  id: number;
  monday: boolean;
  conexion: MondayConexion | null;
}) {
  // Las casillas y el botón solo aparecen si la persona ya conectó su cuenta de Monday
  const monday = mondayOn && conexion !== null;
  const [board, setBoard] = useState({ id: conexion?.tablero_id ?? "", nombre: conexion?.tablero ?? "" });
  const [meeting, setMeeting] = useState<Meeting | null>(null);
  const [draft, setDraft] = useState<Minuta | null>(null);
  const [attendees, setAttendees] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [showTranscript, setShowTranscript] = useState(false);
  // Acuerdos que el dueño autoriza subir a Monday (alineado con draft.acuerdos)
  const [toBoard, setToBoard] = useState<boolean[]>([]);

  const load = useCallback(async () => {
    const m = await api.meeting(id);
    setMeeting(m);
    setDraft(m.minutes);
    setToBoard((m.minutes?.acuerdos ?? []).map(() => true));
    setAttendees(m.attendees.map((a) => (a.name ? `${a.name} <${a.email}>` : a.email)).join("\n"));
    return m;
  }, [id]);

  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [load]);

  // Mientras la IA genera la minuta, se consulta el estado cada 3 segundos
  useEffect(() => {
    if (meeting?.status !== "procesando") return;
    const t = setInterval(() => load().catch(() => {}), 3000);
    return () => clearInterval(t);
  }, [meeting?.status, load]);

  const parseAttendees = (): Attendee[] =>
    linesToList(attendees).map((line) => {
      const m = line.match(/^(.*?)\s*<\s*(\S+@\S+)\s*>$/);
      return m ? { name: m[1].trim(), email: m[2] } : { name: "", email: line };
    });

  const run = async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
      if (ok) setNotice(ok);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const save = () =>
    run(async () => {
      const m = await api.updateMeeting(id, { minutes: draft ?? undefined, attendees: parseAttendees() });
      setMeeting(m);
    }, "Cambios guardados");

  const send = () =>
    run(async () => {
      await api.updateMeeting(id, { minutes: draft ?? undefined, attendees: parseAttendees() });
      setMeeting(await api.send(id));
    }, "Minuta enviada por correo");

  const chosen = (draft?.acuerdos ?? []).map((_, i) => i).filter((i) => toBoard[i]);

  const toMonday = () => {
    if (!draft || chosen.length === 0 || !board.id) return;
    const prev = meeting?.monday_exports[meeting.monday_exports.length - 1];
    const deNuevo = Boolean(prev);
    const lista = chosen
      .map((i) => {
        const a = draft.acuerdos[i];
        const extra = [a.responsable, a.fecha_limite].filter(Boolean).join(", ");
        return `• ${a.descripcion || "(sin descripción)"}${extra ? ` (${extra})` : ""}`;
      })
      .join("\n");
    const omitidos = draft.acuerdos.length - chosen.length;
    const aviso =
      (deNuevo ? `Ya se subieron acuerdos de esta minuta (${fmtDate(prev!.created_at)}).\n\n` : "") +
      `Se crearán ${chosen.length} ${chosen.length === 1 ? "acuerdo" : "acuerdos"} a tu nombre en el tablero "${board.nombre}" de Monday:\n\n${lista}` +
      (omitidos ? `\n\n${omitidos} no se ${omitidos === 1 ? "subirá" : "subirán"}.` : "") +
      "\n\nCada responsable que esté en Monday recibirá un aviso de que tiene un acuerdo asignado." +
      "\n\n¿Autorizas subirlos?";
    if (!confirm(aviso)) return;
    run(async () => {
      // Se guardan antes los cambios para que Monday reciba los acuerdos ya revisados
      await api.updateMeeting(id, { minutes: draft, attendees: parseAttendees() });
      const m = await api.sendToMonday(id, chosen, board.id, deNuevo);
      setMeeting(m);
      const avisados = m.monday_exports[m.monday_exports.length - 1]?.notified ?? [];
      setNotice(
        `${chosen.length === 1 ? "Acuerdo subido" : `${chosen.length} acuerdos subidos`} a Monday. ` +
          (avisados.length
            ? `Se avisó en Monday a ${avisados.join(", ")}.`
            : "No se avisó a nadie: no hay responsables en Monday distintos de ti.")
      );
    });
  };

  const regenerate = () => {
    if (!confirm("Se descartarán los cambios hechos a la minuta. ¿Regenerar?")) return;
    run(async () => {
      await api.regenerate(id);
      await load();
    });
  };

  const remove = () => {
    if (!confirm("¿Eliminar esta reunión, su transcripción y su minuta?")) return;
    run(async () => {
      await api.remove(id);
      window.location.hash = "#/";
    });
  };

  if (error && !meeting) return <p className="error">{error}</p>;
  if (!meeting) return <p>Cargando…</p>;

  const set = <K extends keyof Minuta>(k: K, v: Minuta[K]) => setDraft((d) => (d ? { ...d, [k]: v } : d));
  const setAcuerdo = (i: number, patch: Partial<Acuerdo>) =>
    set("acuerdos", draft!.acuerdos.map((a, j) => (j === i ? { ...a, ...patch } : a)));

  return (
    <>
      <a href="#/" className="muted">
        ← Mis reuniones
      </a>
      <div className="title-row">
        <h2>{meeting.title}</h2>
        <span className={`badge ${meeting.status}`}>{STATUS_LABEL[meeting.status]}</span>
      </div>
      <p className="muted">
        {fmtDate(meeting.started_at)} · {PLATFORM_LABEL[meeting.platform]}
        {meeting.sent_at && <> · Enviada el {fmtDate(meeting.sent_at)}</>}
        {meeting.monday_exports.length > 0 && (() => {
          const last = meeting.monday_exports[meeting.monday_exports.length - 1];
          return (
            <>
              {" "}· Acuerdos en Monday desde el {fmtDate(last.created_at)}
              {last.board_url && (
                <>
                  {" "}(<a href={last.board_url} target="_blank" rel="noreferrer">ver tablero</a>)
                </>
              )}
            </>
          );
        })()}
      </p>

      {error && <p className="error">{error}</p>}
      {meeting.error && <p className="error">{meeting.error}</p>}
      {notice && <p className="ok">{notice}</p>}

      {meeting.status === "procesando" && <div className="card">Generando la minuta con IA…</div>}

      {draft && (
        <div className="card">
          <label>Resumen</label>
          <textarea rows={4} value={draft.resumen} onChange={(e) => set("resumen", e.target.value)} />

          <label>Acuerdos</label>
          <table className="edit">
            <thead>
              <tr>
                <th>Acuerdo</th>
                <th>Responsable</th>
                <th>Fecha límite</th>
                {monday && <th className="check" title="Marca los acuerdos que autorizas subir a Monday">Monday</th>}
                <th />
              </tr>
            </thead>
            <tbody>
              {draft.acuerdos.map((a, i) => (
                <tr key={i}>
                  <td>
                    <input value={a.descripcion} onChange={(e) => setAcuerdo(i, { descripcion: e.target.value })} />
                  </td>
                  <td>
                    <input
                      value={a.responsable ?? ""}
                      onChange={(e) => setAcuerdo(i, { responsable: e.target.value || null })}
                    />
                  </td>
                  <td>
                    <input
                      value={a.fecha_limite ?? ""}
                      onChange={(e) => setAcuerdo(i, { fecha_limite: e.target.value || null })}
                    />
                  </td>
                  {monday && (
                    <td className="check">
                      <input
                        type="checkbox"
                        aria-label="Subir este acuerdo a Monday"
                        checked={Boolean(toBoard[i])}
                        onChange={(e) => setToBoard((t) => draft.acuerdos.map((_, j) => (j === i ? e.target.checked : Boolean(t[j]))))}
                      />
                    </td>
                  )}
                  <td>
                    <button
                      className="link remove"
                      title="Quitar"
                      onClick={() => {
                        set("acuerdos", draft.acuerdos.filter((_, j) => j !== i));
                        setToBoard((t) => t.filter((_, j) => j !== i));
                      }}
                    >
                      ✕
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button
            className="link"
            onClick={() => {
              set("acuerdos", [...draft.acuerdos, { descripcion: "", responsable: null, fecha_limite: null }]);
              setToBoard((t) => [...draft.acuerdos.map((_, j) => Boolean(t[j])), true]);
            }}
          >
            + Agregar acuerdo
          </button>

          <label>Decisiones (una por línea)</label>
          <textarea
            rows={3}
            value={draft.decisiones.join("\n")}
            onChange={(e) => set("decisiones", e.target.value.split("\n"))}
            onBlur={(e) => set("decisiones", linesToList(e.target.value))}
          />

          <label>Temas tratados</label>
          {draft.temas.map((t, i) => (
            <p key={i}>
              <b>{t.tema}.</b> {t.detalle}
            </p>
          ))}

          <label>Pendientes (uno por línea)</label>
          <textarea
            rows={3}
            value={draft.pendientes.join("\n")}
            onChange={(e) => set("pendientes", e.target.value.split("\n"))}
            onBlur={(e) => set("pendientes", linesToList(e.target.value))}
          />

          <label>Destinatarios (uno por línea: Nombre &lt;correo&gt;)</label>
          <textarea rows={4} value={attendees} onChange={(e) => setAttendees(e.target.value)} />

          {monday && (
            <div className="monday-board">
              <label>Tablero de Monday para estos acuerdos</label>
              <MondayBoardPicker value={board.id} onChange={(id, nombre) => setBoard({ id, nombre })} disabled={busy} />
            </div>
          )}
          {mondayOn && !conexion && (
            <p className="muted small">
              Para subir estos acuerdos a Monday, <a href="#/perfil">conecta tu cuenta de Monday en tu perfil</a>.
            </p>
          )}

          <div className="actions">
            <button className="btn primary" disabled={busy} onClick={send}>
              {meeting.status === "enviada" ? "Reenviar por correo" : "Enviar por correo"}
            </button>
            {monday && (
              <button
                className="btn"
                disabled={busy || chosen.length === 0 || !board.id}
                title={
                  !board.id
                    ? "Elige el tablero de Monday"
                    : chosen.length === 0
                      ? "Marca en la columna Monday los acuerdos que quieres subir"
                      : undefined
                }
                onClick={toMonday}
              >
                {chosen.length === 0
                  ? "Subir acuerdos a Monday"
                  : `Subir ${chosen.length === draft.acuerdos.length ? "" : `${chosen.length} `}${chosen.length === 1 ? "acuerdo" : "acuerdos"} a Monday${meeting.monday_exports.length ? " de nuevo" : ""}`}
              </button>
            )}
            <button className="btn" disabled={busy} onClick={save}>
              Guardar cambios
            </button>
            <button className="btn" disabled={busy} onClick={regenerate}>
              Regenerar con IA
            </button>
            <button className="btn danger" disabled={busy} onClick={remove}>
              Eliminar
            </button>
          </div>
        </div>
      )}

      {meeting.status === "error" && !draft && (
        <div className="actions">
          <button className="btn primary" disabled={busy} onClick={regenerate}>
            Reintentar
          </button>
          <button className="btn danger" disabled={busy} onClick={remove}>
            Eliminar
          </button>
        </div>
      )}

      {meeting.uso.generaciones > 0 && (
        <div className="card muted small">
          <b>Costo de IA:</b> {fmtUsd(meeting.uso.costo_usd)}
          {meeting.uso.sin_precio > 0 && " (incompleto: modelo sin precio registrado)"} ·{" "}
          {meeting.uso.generaciones} {meeting.uso.generaciones === 1 ? "generación" : "generaciones"} ·{" "}
          {fmtInt(meeting.uso.input_tokens)} tokens de entrada / {fmtInt(meeting.uso.output_tokens)} de salida
        </div>
      )}

      <div className="card muted small">
        <b>Consentimiento:</b> confirmado por {meeting.consent.confirmado_por} el {fmtDate(meeting.consent.confirmado_en)}.
        <br />
        {meeting.consent.origen === "archivo" ? (
          <>Generada desde el archivo {meeting.consent.archivo}. Declaración: “{meeting.consent.aviso}”</>
        ) : (
          <>Aviso compartido: “{meeting.consent.aviso}”</>
        )}
      </div>

      <button className="link" onClick={() => setShowTranscript((v) => !v)}>
        {showTranscript ? "Ocultar transcripción" : "Ver transcripción"}
      </button>
      {showTranscript && <pre className="transcript">{meeting.transcript}</pre>}
    </>
  );
}
