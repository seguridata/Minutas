import { useCallback, useEffect, useState } from "react";
import { AdminCostos as Historial, AdminResumen, AdminUsuario, api, fmtInt, fmtUsd } from "../api";
import { Kpi, currentMonth, fmtHours, trend } from "./Admin";

const MESES = 6;
const monthLabel = (mes: string) => {
  const [y, m] = mes.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("es-MX", { month: "short", year: "2-digit" });
};

/** Costos de IA por persona: el mes elegido a detalle y la evolución de los últimos meses. */
export default function AdminCostos() {
  const [mes, setMes] = useState(currentMonth());
  const [resumen, setResumen] = useState<AdminResumen | null>(null);
  const [usuarios, setUsuarios] = useState<AdminUsuario[]>([]);
  const [historial, setHistorial] = useState<Historial | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    Promise.all([api.adminResumen(mes), api.adminUsuarios(mes), api.adminCostos(MESES)]).then(
      ([r, u, h]) => {
        setResumen(r);
        setUsuarios(u);
        setHistorial(h);
        setError("");
      },
      (e) => setError((e as Error).message),
    );
  }, [mes]);

  useEffect(() => {
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [load]);

  const total = resumen?.costo.costo_usd ?? 0;
  const conUso = usuarios.filter((u) => u.uso_mes.generaciones > 0);
  const sinUso = usuarios.length - conUso.length;
  const maxCosto = Math.max(0, ...conUso.map((u) => u.uso_mes.costo_usd));
  const historialConUso = historial?.usuarios.filter((u) => u.total > 0) ?? [];

  return (
    <>
      <div className="title-row">
        <h2>Costos por usuario</h2>
        <button className="btn small-btn" onClick={load} style={{ marginLeft: "auto" }}>
          Actualizar
        </button>
        <input type="month" style={{ width: "auto" }} value={mes} max={currentMonth()} onChange={(e) => e.target.value && setMes(e.target.value)} />
      </div>
      {error && <p className="error">{error}</p>}

      {!resumen && !error && <p className="muted">Cargando…</p>}

      {resumen && (
        <div className="kpis">
          <Kpi label="Costo de IA del mes" value={fmtUsd(total)} note={trend(total, resumen.costo_mes_anterior)} />
          <Kpi label="Personas con uso" value={fmtInt(resumen.usuarios_activos)} note={`de ${fmtInt(resumen.usuarios_registrados)} registradas`} />
          <Kpi
            label="Promedio por persona"
            value={resumen.usuarios_activos ? fmtUsd(total / resumen.usuarios_activos) : "—"}
            note="entre quienes usaron la herramienta"
          />
          <Kpi label="Costo por hora de reunión" value={resumen.costo_por_hora === null ? "—" : fmtUsd(resumen.costo_por_hora)} note="incluye regeneraciones" />
        </div>
      )}

      {resumen && (
      <div className="card">
        <h3>Consumo del mes por persona</h3>
        {conUso.length === 0 ? (
          <p className="muted">Nadie ha generado minutas este mes.</p>
        ) : (
          <div className="table-scroll">
            <table className="list small">
              <thead>
                <tr>
                  <th>Persona</th>
                  <th className="num">Minutas generadas</th>
                  <th className="num">Horas grabadas</th>
                  <th className="num">Tokens (entrada / salida)</th>
                  <th className="num">Costo por hora</th>
                  <th className="num">Costo</th>
                  <th style={{ width: 160 }}>% del total</th>
                </tr>
              </thead>
              <tbody>
                {conUso.map((u) => {
                  const c = u.uso_mes.costo_usd;
                  const pct = total ? (c / total) * 100 : 0;
                  return (
                    <tr key={u.email} style={{ cursor: "default" }}>
                      <td>
                        <b>{u.name || u.email}</b>
                        <div className="muted">{u.email}</div>
                      </td>
                      <td className="num">{fmtInt(u.uso_mes.generaciones)}</td>
                      <td className="num">{fmtHours(u.horas_mes)}</td>
                      <td className="num">
                        {fmtInt(u.uso_mes.input_tokens)} / {fmtInt(u.uso_mes.output_tokens)}
                      </td>
                      <td className="num">{u.horas_mes ? fmtUsd(c / u.horas_mes) : "—"}</td>
                      <td className="num">
                        <b>{fmtUsd(c)}</b>
                      </td>
                      <td>
                        <div className="share">
                          <div className="share-bar" style={{ width: `${maxCosto ? (c / maxCosto) * 100 : 0}%` }} />
                          <span>{pct.toFixed(0)}%</span>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {sinUso > 0 && (
          <p className="muted small">
            {sinUso} {sinUso === 1 ? "persona registrada no ha" : "personas registradas no han"} generado minutas este mes.
          </p>
        )}
      </div>
      )}

      {historial && (
        <div className="card">
          <h3>Últimos {MESES} meses</h3>
          {historialConUso.length === 0 ? (
            <p className="muted">Sin consumo en este periodo.</p>
          ) : (
            <div className="table-scroll">
              <table className="list small">
                <thead>
                  <tr>
                    <th>Persona</th>
                    {historial.meses.map((m) => (
                      <th key={m} className={`num${m === mes ? " current" : ""}`}>
                        {monthLabel(m)}
                      </th>
                    ))}
                    <th className="num">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {historialConUso.map((u) => (
                    <tr key={u.email} style={{ cursor: "default" }}>
                      <td>{u.name || u.email}</td>
                      {u.por_mes.map((c, i) => (
                        <td key={i} className={`num${historial.meses[i] === mes ? " current" : ""}`}>
                          {c ? fmtUsd(c) : <span className="muted">—</span>}
                        </td>
                      ))}
                      <td className="num">
                        <b>{fmtUsd(u.total)}</b>
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td>Total</td>
                    {historial.total_por_mes.map((c, i) => (
                      <td key={i} className={`num${historial.meses[i] === mes ? " current" : ""}`}>
                        {fmtUsd(c)}
                      </td>
                    ))}
                    <td className="num">{fmtUsd(historial.total_por_mes.reduce((a, b) => a + b, 0))}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
        </div>
      )}

      <p className="muted small">
        Costos estimados con los precios de lista de Anthropic; el cobro real está en console.anthropic.com. Incluyen las
        regeneraciones y las minutas que después se eliminaron. Los meses se cortan en hora UTC.
      </p>
    </>
  );
}
