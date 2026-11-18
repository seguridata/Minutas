import { useEffect, useState } from "react";
import { MeetingSummary, PLATFORM_LABEL, STATUS_LABEL, api, fmtDate } from "../api";

export default function MeetingList() {
  const [items, setItems] = useState<MeetingSummary[] | null>(null);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState<number | null>(null);

  useEffect(() => {
    api.meetings().then(setItems).catch((e) => setError(e.message));
  }, []);

  const remove = async (m: MeetingSummary) => {
    if (!confirm(`¿Eliminar "${m.title}"? Se borrarán su transcripción y su minuta. Esta acción no se puede deshacer.`))
      return;
    setDeleting(m.id);
    try {
      await api.remove(m.id);
      setItems((prev) => prev?.filter((x) => x.id !== m.id) ?? null);
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setDeleting(null);
    }
  };

  if (error) return <p className="error">{error}</p>;
  if (!items) return <p>Cargando…</p>;

  return (
    <>
      <h2>Mis reuniones</h2>
      {items.length === 0 ? (
        <div className="card">
          <p>Aún no tienes minutas.</p>
          <p className="muted">
            Abre la app de escritorio <b>Minutas</b> al iniciar tu reunión de Meet, Teams o Zoom, avisa a los
            participantes y comienza la grabación. Al terminar, la minuta aparecerá aquí.
          </p>
        </div>
      ) : (
        <table className="list">
          <thead>
            <tr>
              <th>Reunión</th>
              <th>Fecha</th>
              <th>Plataforma</th>
              <th>Estado</th>
              <th aria-label="Acciones" />
            </tr>
          </thead>
          <tbody>
            {items.map((m) => (
              <tr key={m.id} onClick={() => (window.location.hash = `#/reuniones/${m.id}`)}>
                <td>{m.title}</td>
                <td>{fmtDate(m.started_at)}</td>
                <td>{PLATFORM_LABEL[m.platform]}</td>
                <td>
                  <span className={`badge ${m.status}`}>{STATUS_LABEL[m.status]}</span>
                </td>
                <td className="row-actions">
                  <button
                    className="btn danger small-btn"
                    title="Eliminar reunión"
                    disabled={deleting === m.id}
                    onClick={(e) => {
                      e.stopPropagation(); // que no abra la reunión
                      remove(m);
                    }}
                  >
                    {deleting === m.id ? "Eliminando…" : "Eliminar"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
