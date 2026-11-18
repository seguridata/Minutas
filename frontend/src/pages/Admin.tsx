import { useCallback, useEffect, useState } from "react";
import {
  AdminResumen,
  AdminUsuario,
  PLATFORM_LABEL,
  Platform,
  STATUS_LABEL,
  Status,
  api,
  fmtDate,
  fmtInt,
  fmtUsd,
} from "../api";

export const currentMonth = () => new Date().toISOString().slice(0, 7);
const REFRESH_MS = 60_000;

export const fmtHours = (h: number) => (h < 1 ? `${Math.round(h * 60)} min` : `${h.toLocaleString("es-MX", { maximumFractionDigits: 1 })} h`);

export function Kpi({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {note && <div className="kpi-note">{note}</div>}
    </div>
  );
}

export function trend(actual: number, anterior: number) {
  if (anterior === 0) return actual === 0 ? "Sin consumo" : "Sin consumo el mes anterior";
  const pct = ((actual - anterior) / anterior) * 100;
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(0)}% vs. mes anterior (${fmtUsd(anterior)})`;
}

/** Costo por día del mes: barras para cada día, en gris salvo el día de mayor gasto. */
function DailyCost({ data }: { data: AdminResumen }) {
  const [y, m] = data.mes.split("-").map(Number);
  const days = new Date(y, m, 0).getDate();
  const byDay = new Map(data.por_dia.map((d) => [Number(d.dia.slice(8, 10)), d]));
  const peak = Math.max(0, ...data.por_dia.map((d) => d.costo_usd));
  const W = 720, H = 160, left = 8, base = 130, band = (W - left * 2) / days;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="daily-chart" role="img" aria-label="Costo de IA por día">
      <line x1={left} x2={W - left} y1={base} y2={base} stroke="var(--border)" />
      {Array.from({ length: days }, (_, i) => {
        const d = byDay.get(i + 1);
        const h = d && peak ? Math.max((d.costo_usd / peak) * 100, 2) : 0;
        const x = left + i * band + band * 0.15;
        return (
          <g key={i}>
            {d && (
              <rect x={x} y={base - h} width={band * 0.7} height={h} rx="2" fill={d.costo_usd === peak ? "var(--primary)" : "#b9c4ad"}>
                <title>{`${i + 1}/${m}: ${fmtUsd(d.costo_usd)} · ${d.generaciones} ${d.generaciones === 1 ? "generación" : "generaciones"}`}</title>
              </rect>
            )}
            {(i === 0 || (i + 1) % 5 === 0) && (
              <text x={x + band * 0.35} y={base + 18} textAnchor="middle" fontSize="11" fill="var(--muted)">
                {i + 1}
              </text>
            )}
          </g>
        );
      })}
      {peak > 0 && (
        <text x={left} y={14} fontSize="12" fill="var(--muted)">
          Día de mayor gasto: {fmtUsd(peak)}
        </text>
      )}
    </svg>
  );
}

export default function AdminMonitoreo() {
  const [mes, setMes] = useState(currentMonth());
  const [resumen, setResumen] = useState<AdminResumen | null>(null);
  const [usuarios, setUsuarios] = useState<AdminUsuario[]>([]);
  const [error, setError] = useState("");
  const [updated, setUpdated] = useState<Date | null>(null);

  const load = useCallback(() => {
    Promise.all([api.adminResumen(mes), api.adminUsuarios(mes)]).then(
      ([r, u]) => {
        setResumen(r);
        setUsuarios(u);
        setError("");
        setUpdated(new Date());
      },
      (e) => setError((e as Error).message),
    );
  }, [mes]);

  useEffect(() => {
    load();
    // Monitoreo: se actualiza solo mientras la página está abierta
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  const r = resumen;
  return (
    <>
      <div className="title-row">
        <h2>Monitoreo</h2>
        <span className="muted small">{updated && `Actualizado ${updated.toLocaleTimeString("es-MX", { timeStyle: "short" })}`}</span>
        <button className="btn small-btn" onClick={load} style={{ marginLeft: "auto" }}>
          Actualizar
        </button>
        <input type="month" style={{ width: "auto" }} value={mes} max={currentMonth()} onChange={(e) => e.target.value && setMes(e.target.value)} />
      </div>
      {error && <p className="error">{error}</p>}

      {r && (
        <>
          <div className="kpis">
            <Kpi label="Costo de IA del mes" value={fmtUsd(r.costo.costo_usd)} note={trend(r.costo.costo_usd, r.costo_mes_anterior)} />
            <Kpi
              label="Minutas generadas"
              value={fmtInt(r.costo.generaciones)}
              note={`${fmtInt(r.costo.input_tokens)} tokens de entrada / ${fmtInt(r.costo.output_tokens)} de salida`}
            />
            <Kpi label="Personas activas" value={fmtInt(r.usuarios_activos)} note={`de ${fmtInt(r.usuarios_registrados)} registradas`} />
            <Kpi label="Horas grabadas" value={fmtHours(r.horas_grabadas)} note="de las reuniones que siguen en el sistema" />
            <Kpi label="Costo por hora de reunión" value={r.costo_por_hora === null ? "—" : fmtUsd(r.costo_por_hora)} note="incluye regeneraciones" />
          </div>

          <div className="card">
            <h3>Costo por día</h3>
            {r.por_dia.length ? <DailyCost data={r} /> : <p className="muted">Sin consumo este mes.</p>}
            {r.costo.sin_precio > 0 && (
              <p className="small error">{r.costo.sin_precio} generaciones usan un modelo sin precio registrado y no suman al costo.</p>
            )}
          </div>

          <div className="card">
            <h3>Alertas</h3>
            {r.alertas.length === 0 ? (
              <p className="ok">Sin errores ni minutas atoradas este mes.</p>
            ) : (
              <table className="list small">
                <thead>
                  <tr>
                    <th>Fecha</th>
                    <th>Persona</th>
                    <th>Reunión</th>
                    <th>Estado</th>
                    <th>Detalle</th>
                  </tr>
                </thead>
                <tbody>
                  {r.alertas.map((a) => (
                    <tr key={a.meeting_id} style={{ cursor: "default" }}>
                      <td>{fmtDate(a.created_at)}</td>
                      <td>{a.owner_email}</td>
                      <td>{a.title}</td>
                      <td>
                        <span className={`badge ${a.status}`}>{a.status === "procesando" ? "Atorada" : STATUS_LABEL[a.status]}</span>
                      </td>
                      <td>{a.status === "procesando" ? "Lleva más de 15 min generándose" : a.error}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="card">
            <h3>Estado de los usuarios</h3>
            <div className="table-scroll">
              <table className="list small">
                <thead>
                  <tr>
                    <th>Persona</th>
                    <th>Última actividad</th>
                    <th>Reuniones (mes / total)</th>
                    <th>Enviadas</th>
                    <th>Errores</th>
                    <th>Horas</th>
                    <th>Google</th>
                    <th>Envío automático</th>
                  </tr>
                </thead>
                <tbody>
                  {usuarios.map((u) => (
                    <tr key={u.email} style={{ cursor: "default" }}>
                      <td>
                        <b>{u.name || u.email}</b>
                        <div className="muted">
                          {u.email} · alta {new Date(u.alta).toLocaleDateString("es-MX", { dateStyle: "medium" })}
                        </div>
                      </td>
                      <td>{u.ultima_actividad ? fmtDate(u.ultima_actividad) : <span className="muted">Nunca</span>}</td>
                      <td>
                        {fmtInt(u.reuniones_mes)} / {fmtInt(u.reuniones_total)}
                      </td>
                      <td>{fmtInt(u.enviadas_mes)}</td>
                      <td className={u.errores_mes ? "error" : ""}>{fmtInt(u.errores_mes)}</td>
                      <td>{fmtHours(u.horas_mes)}</td>
                      <td>
                        <span className={`badge ${u.google_conectado ? "enviada" : "error"}`}>
                          {u.google_conectado ? "Conectado" : "Sin permisos"}
                        </span>
                      </td>
                      <td>{u.envio_automatico ? "Sí" : "No"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card">
            <h3>Reuniones del mes</h3>
            <p className="small">
              {Object.entries(r.reuniones_por_estado).map(([s, n]) => (
                <span key={s} className={`badge ${s}`} style={{ marginRight: 8 }}>
                  {STATUS_LABEL[s as Status] ?? s}: {n}
                </span>
              ))}
              {Object.keys(r.reuniones_por_estado).length === 0 && <span className="muted">Sin reuniones este mes.</span>}
            </p>
            <p className="small muted">
              {Object.entries(r.reuniones_por_plataforma)
                .map(([p, n]) => `${PLATFORM_LABEL[p as Platform] ?? p}: ${n}`)
                .join(" · ")}
            </p>
            {r.por_modelo.length > 0 && (
              <table className="list small">
                <thead>
                  <tr>
                    <th>Modelo</th>
                    <th>Generaciones</th>
                    <th>Tokens (entrada / salida)</th>
                    <th>Costo</th>
                  </tr>
                </thead>
                <tbody>
                  {r.por_modelo.map((m) => (
                    <tr key={m.model} style={{ cursor: "default" }}>
                      <td>{m.model}</td>
                      <td>{fmtInt(m.uso.generaciones)}</td>
                      <td>
                        {fmtInt(m.uso.input_tokens)} / {fmtInt(m.uso.output_tokens)}
                      </td>
                      <td>{fmtUsd(m.uso.costo_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <p className="muted small">
            Este panel muestra solo datos de uso: no da acceso a las transcripciones ni a las minutas de otras personas. Costos
            estimados con los precios de lista; el cobro real está en console.anthropic.com. Las reuniones eliminadas no
            cuentan en reuniones ni horas, pero su costo sí. Los meses se cortan en hora UTC.
          </p>
        </>
      )}
    </>
  );
}
