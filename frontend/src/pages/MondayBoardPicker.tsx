import { useEffect, useState } from "react";
import { MondayTablero, api } from "../api";

/** Lista de los tableros de Monday a los que la persona tiene acceso, agrupados por espacio de trabajo. */
export default function MondayBoardPicker({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (id: string, nombre: string) => void;
  disabled?: boolean;
}) {
  const [boards, setBoards] = useState<MondayTablero[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.mondayBoards().then(setBoards, (e) => setError((e as Error).message));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (!boards) return <p className="muted">Cargando tus tableros de Monday…</p>;
  if (boards.length === 0) return <p className="muted">Tu cuenta de Monday no tiene acceso a ningún tablero.</p>;

  const espacios = [...new Set(boards.map((b) => b.espacio))];
  return (
    <select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value, boards.find((b) => b.id === e.target.value)?.nombre ?? "")}>
      {!value && <option value="">Elige un tablero…</option>}
      {value && !boards.some((b) => b.id === value) && <option value={value}>(tablero sin acceso)</option>}
      {espacios.map((esp) => (
        <optgroup key={esp} label={esp}>
          {boards
            .filter((b) => b.espacio === esp)
            .map((b) => (
              <option key={b.id} value={b.id}>
                {b.nombre}
              </option>
            ))}
        </optgroup>
      ))}
    </select>
  );
}
