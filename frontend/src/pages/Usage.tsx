import { useEffect, useState } from "react";
import { UsoIA, UsoMensual, api, fmtInt, fmtUsd } from "../api";

const currentMonth = () => new Date().toISOString().slice(0, 7);

function Totals({ uso }: { uso: UsoIA }) {
  return (
    <p>
      <b>{fmtUsd(uso.costo_usd)}</b> · {fmtInt(uso.generaciones)}{" "}
      {uso.generaciones === 1 ? "minuta generada" : "minutas generadas"} · {fmtInt(uso.input_tokens)} tokens de
      entrada / {fmtInt(uso.output_tokens)} de salida
      {uso.sin_precio > 0 && <span className="muted"> ({uso.sin_precio} sin precio registrado)</span>}
    </p>
  );
}

export default function Usage() {
  const [mes, setMes] = useState(currentMonth());
  const [data, setData] = useState<UsoMensual | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setError("");
    api.usage(mes).then(setData, (e) => setError((e as Error).message));
  }, [mes]);

  return (
    <div className="card">
      <div className="title-row">
        <h3 style={{ margin: 0 }}>Mi consumo de IA</h3>
        <input type="month" style={{ width: "auto", marginLeft: "auto" }} value={mes} max={currentMonth()} onChange={(e) => e.target.value && setMes(e.target.value)} />
      </div>
      {error && <p className="error">{error}</p>}
      {data && (
        <>
          <Totals uso={data.propio} />
          <p className="muted small">
            Estimado con los precios de lista de Anthropic; el cobro real se ve en console.anthropic.com. Incluye las
            regeneraciones y las minutas que después se eliminaron.
          </p>
        </>
      )}
    </div>
  );
}
