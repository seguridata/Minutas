import { useEffect, useState } from "react";
import { AuthError, User, api, getToken, loginUrl, logout } from "./api";
import MeetingDetail from "./pages/MeetingDetail";
import AdminMonitoreo from "./pages/Admin";
import AdminCostos from "./pages/AdminCostos";
import MeetingList from "./pages/MeetingList";
import Profile from "./pages/Profile";

function useHashRoute() {
  const [hash, setHash] = useState(window.location.hash || "#/");
  useEffect(() => {
    const onChange = () => setHash(window.location.hash || "#/");
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash.slice(1);
}

export default function App() {
  const route = useHashRoute();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(!!getToken());

  useEffect(() => {
    if (!getToken()) return;
    api
      .me()
      .then(setUser)
      .catch((e) => {
        if (e instanceof AuthError) localStorage.removeItem("minutas_token");
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="center">Cargando…</div>;

  if (!user) {
    return (
      <div className="center login">
        <div className="login-card">
          <img src="/logo-seguridata.png" alt="SeguriData" />
          <h1>Minutas de reuniones</h1>
          <p>Genera minutas con los acuerdos de tus reuniones de Google Meet, Teams o Zoom.</p>
          <a className="btn primary" href={loginUrl()}>
            Iniciar sesión con Google
          </a>
          <p className="note">Uso interno. Inicia sesión con tu cuenta @seguridata.com.</p>
        </div>
      </div>
    );
  }

  // Perfil de administrador: una sección aparte, con su propio encabezado y menú
  if (route.startsWith("/admin") && user.is_admin) {
    const monitoreo = route.startsWith("/admin/monitoreo");
    return (
      <>
        <header className="admin-header">
          <a href="#/admin" className="brand">
            <img src="/logo-seguridata.png" alt="SeguriData" />
            <span>Minutas</span>
            <span className="admin-tag">Administrador</span>
          </a>
          <nav>
            <a href="#/admin" className={!monitoreo ? "active" : ""}>
              Costos por usuario
            </a>
            <a href="#/admin/monitoreo" className={monitoreo ? "active" : ""}>
              Monitoreo
            </a>
            <span className="who">{user.name || user.email}</span>
            <a href="#/" className="switch-profile">
              Ir a mis reuniones
            </a>
            <button className="link" onClick={logout}>
              Salir
            </button>
          </nav>
        </header>
        <main className="wide">{monitoreo ? <AdminMonitoreo /> : <AdminCostos />}</main>
        <footer>SeguriData · Information Security · Panel de administración</footer>
      </>
    );
  }

  const detail = route.match(/^\/reuniones\/(\d+)/);
  let page;
  if (detail) page = <MeetingDetail id={Number(detail[1])} monday={user.monday_habilitado} conexion={user.monday_conexion} />;
  else if (route.startsWith("/perfil")) page = <Profile user={user} onSaved={setUser} />;
  else page = <MeetingList />;
  const section = route.startsWith("/perfil") ? "perfil" : "reuniones";

  return (
    <>
      <header>
        <a href="#/" className="brand">
          <img src="/logo-seguridata.png" alt="SeguriData" />
          <span>Minutas</span>
        </a>
        <nav>
          <a href="#/" className={section === "reuniones" ? "active" : ""}>
            Mis reuniones
          </a>
          <a href="#/perfil" className={section === "perfil" ? "active" : ""}>
            Perfil
          </a>
          <span className="who">{user.name || user.email}</span>
          {user.is_admin && (
            <a href="#/admin" className="switch-profile">
              Panel de administrador
            </a>
          )}
          <button className="link" onClick={logout}>
            Salir
          </button>
        </nav>
      </header>
      <main>{page}</main>
      <footer>SeguriData · Information Security · Herramienta de uso interno</footer>
    </>
  );
}
