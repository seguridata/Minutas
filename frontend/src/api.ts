// En el servidor el portal y la API salen del mismo puerto, así que basta con rutas relativas
export const API_URL =
  import.meta.env.VITE_API_URL ?? (import.meta.env.PROD ? "" : "http://localhost:8000");

export type Platform = "meet" | "teams" | "zoom" | "otra";
export type Status = "procesando" | "lista" | "enviada" | "error";

export interface Attendee {
  name: string;
  email: string;
}

export interface Acuerdo {
  descripcion: string;
  responsable: string | null;
  fecha_limite: string | null;
}

export interface Minuta {
  titulo: string;
  resumen: string;
  temas: { tema: string; detalle: string }[];
  decisiones: string[];
  acuerdos: Acuerdo[];
  pendientes: string[];
}

export interface MeetingSummary {
  id: number;
  title: string;
  platform: Platform;
  started_at: string;
  status: Status;
  sent_at: string | null;
}

export interface UsoIA {
  generaciones: number;
  input_tokens: number;
  output_tokens: number;
  costo_usd: number;
  sin_precio: number;
}

export interface UsoMensual {
  mes: string;
  propio: UsoIA;
}

export interface Meeting extends MeetingSummary {
  ended_at: string;
  attendees: Attendee[];
  transcript: string;
  consent: {
    aviso: string;
    confirmado_en: string;
    confirmado_por: string;
    nadie_se_opuso: boolean;
    origen?: "grabacion" | "archivo";
    archivo?: string | null;
  };
  minutes: Minuta | null;
  error: string | null;
  uso: UsoIA;
  monday_exports: { created_at: string; item_ids: string[]; board_url: string | null; notified: string[] | null }[];
}

export interface User {
  email: string;
  name: string;
  picture: string | null;
  auto_send: boolean;
  cc_me: boolean;
  signature: string;
  is_admin: boolean;
  monday_habilitado: boolean;
  monday_oauth: boolean;
  monday_conexion: MondayConexion | null;
}

export interface MondayConexion {
  cuenta: string;
  tablero_id: string | null;
  tablero: string | null;
  tablero_url: string | null;
}

export interface MondayTablero {
  id: string;
  nombre: string;
  espacio: string;
  url: string | null;
}

export interface AdminAlerta {
  meeting_id: number;
  title: string;
  owner_email: string;
  status: Status;
  error: string | null;
  created_at: string;
}

export interface AdminResumen {
  mes: string;
  costo: UsoIA;
  costo_mes_anterior: number;
  por_dia: { dia: string; generaciones: number; costo_usd: number }[];
  por_modelo: { model: string; uso: UsoIA }[];
  usuarios_registrados: number;
  usuarios_activos: number;
  reuniones_por_estado: Partial<Record<Status, number>>;
  reuniones_por_plataforma: Partial<Record<Platform, number>>;
  horas_grabadas: number;
  costo_por_hora: number | null;
  alertas: AdminAlerta[];
}

export interface AdminCostos {
  meses: string[];
  total_por_mes: number[];
  usuarios: { email: string; name: string; por_mes: number[]; total: number }[];
}

export interface AdminUsuario {
  email: string;
  name: string;
  alta: string;
  ultima_actividad: string | null;
  google_conectado: boolean;
  envio_automatico: boolean;
  reuniones_total: number;
  reuniones_mes: number;
  enviadas_mes: number;
  errores_mes: number;
  horas_mes: number;
  uso_mes: UsoIA;
}

const TOKEN_KEY = "minutas_token";

export function captureTokenFromUrl() {
  const m = window.location.hash.match(/token=([^&]+)/);
  if (m) {
    localStorage.setItem(TOKEN_KEY, m[1]);
    window.location.hash = "#/";
  }
}

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const logout = () => {
  localStorage.removeItem(TOKEN_KEY);
  window.location.hash = "#/";
  window.location.reload();
};
export const loginUrl = () => `${API_URL}/auth/login?client=web`;

export class AuthError extends Error {}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${getToken()}`,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) throw new AuthError("Sesión expirada");
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : `Error ${res.status}`);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const api = {
  me: () => request<User>("GET", "/api/me"),
  updateMe: (body: Partial<User>) => request<User>("PATCH", "/api/me", body),
  meetings: () => request<MeetingSummary[]>("GET", "/api/meetings"),
  meeting: (id: number) => request<Meeting>("GET", `/api/meetings/${id}`),
  updateMeeting: (id: number, body: { title?: string; attendees?: Attendee[]; minutes?: Minuta }) =>
    request<Meeting>("PATCH", `/api/meetings/${id}`, body),
  regenerate: (id: number) => request<MeetingSummary>("POST", `/api/meetings/${id}/regenerate`),
  send: (id: number) => request<Meeting>("POST", `/api/meetings/${id}/send`),
  /** Sube a Monday solo los acuerdos autorizados (posiciones en la minuta guardada). */
  sendToMonday: (id: number, acuerdos: number[], tableroId: string, deNuevo = false) =>
    request<Meeting>("POST", `/api/meetings/${id}/monday${deNuevo ? "?de_nuevo=true" : ""}`, {
      acuerdos,
      tablero_id: tableroId,
    }),
  mondayConnectUrl: () => request<{ url: string }>("GET", "/api/monday/conectar"),
  /** Conecta Monday con el token personal; el backend lo prueba y lo guarda cifrado. */
  mondaySaveToken: (token: string) => request<User>("PUT", "/api/monday/token", { token }),
  mondayDisconnect: () => request<User>("DELETE", "/api/monday"),
  mondayBoards: () => request<MondayTablero[]>("GET", "/api/monday/tableros"),
  mondayChooseBoard: (id: string) => request<User>("PUT", "/api/monday/tablero", { id }),
  remove: (id: number) => request<void>("DELETE", `/api/meetings/${id}`),
  usage: (mes?: string) => request<UsoMensual>("GET", `/api/usage${mes ? `?mes=${mes}` : ""}`),
  adminResumen: (mes: string) => request<AdminResumen>("GET", `/api/admin/resumen?mes=${mes}`),
  adminCostos: (meses = 6) => request<AdminCostos>("GET", `/api/admin/costos?meses=${meses}`),
  adminUsuarios: (mes: string) => request<AdminUsuario[]>("GET", `/api/admin/usuarios?mes=${mes}`),
};

export const PLATFORM_LABEL: Record<Platform, string> = {
  meet: "Google Meet",
  teams: "Microsoft Teams",
  zoom: "Zoom",
  otra: "Otra",
};

export const STATUS_LABEL: Record<Status, string> = {
  procesando: "Generando…",
  lista: "Lista para enviar",
  enviada: "Enviada",
  error: "Error",
};

export const fmtDate = (iso: string) =>
  new Date(iso).toLocaleString("es-MX", { dateStyle: "medium", timeStyle: "short" });

export const fmtUsd = (n: number) =>
  `US$${n.toLocaleString("es-MX", { minimumFractionDigits: 2, maximumFractionDigits: n < 1 ? 4 : 2 })}`;

export const fmtInt = (n: number) => n.toLocaleString("es-MX");
