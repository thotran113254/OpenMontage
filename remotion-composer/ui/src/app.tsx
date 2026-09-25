import React, { useEffect, useState } from "react";
import { api, onUnauthorized } from "./api/client";
import { JobListPage } from "./pages/job-list";
import { JobDetailPage } from "./pages/job-detail";
import { ProjectDetailPage } from "./pages/project-detail";
import { ProjectListPage } from "./pages/project-list";
import { ProjectNewPage } from "./pages/project-new";
import { HowItWorksPage } from "./pages/how-it-works";
import { CloudQueuePage } from "./pages/cloud-queue";
import { StylesPage } from "./pages/styles";
import { LoginScreen } from "./components/login-screen";

type Route =
  | { name: "jobs" }
  | { name: "job"; id: string }
  | { name: "projects" }
  | { name: "project-new" }
  | { name: "project"; id: string }
  | { name: "how-it-works" }
  | { name: "styles" }
  | { name: "cloud" };

const parseHash = (): Route => {
  const hash = window.location.hash.replace(/^#\/?/, "");
  if (hash.startsWith("job/")) return { name: "job", id: hash.slice(4) };
  if (hash.startsWith("project/")) return { name: "project", id: hash.slice(8) };
  if (hash === "projects/new") return { name: "project-new" };
  if (hash === "projects") return { name: "projects" };
  if (hash === "jobs") return { name: "jobs" };
  if (hash === "how-it-works") return { name: "how-it-works" };
  if (hash === "styles" || hash === "kieu") return { name: "styles" };
  if (hash === "cloud" || hash === "cloud-queue" || hash === "schedule") {
    return { name: "cloud" };
  }
  // Projects are the entry point now: sources belong to a project, and a build
  // is one output of one. Ad-hoc single-file jobs stay reachable at /jobs.
  return { name: "projects" };
};

export const App: React.FC = () => {
  const [route, setRoute] = useState<Route>(parseHash);
  const [queue, setQueue] = useState<{ running: string | null; pending: number }>();
  const [cloudCount, setCloudCount] = useState(0);
  const [cloudReady, setCloudReady] = useState(false);
  const [cloudOp, setCloudOp] = useState<string | null>(null);
  const [cloudEnabled, setCloudEnabled] = useState(false);
  // null = still checking; the server may not implement /api/auth/status yet
  // (rollout in progress), in which case we fail open rather than lock
  // everyone out of a working app.
  const [authRequired, setAuthRequired] = useState<boolean | null>(null);
  const [authenticated, setAuthenticated] = useState(false);

  useEffect(() => {
    const onHashChange = () => setRoute(parseHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    let cancelled = false;
    api.authStatus()
      .then((status) => {
        if (cancelled) return;
        setAuthRequired(status.auth_required);
        setAuthenticated(status.authenticated);
      })
      .catch(() => {
        if (cancelled) return;
        setAuthRequired(false);
        setAuthenticated(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Any 401 anywhere in the app (session expired, cookie cleared server-side,
  // …) bounces back to the login screen, not just the call site that failed.
  useEffect(() => {
    onUnauthorized(() => {
      setAuthRequired(true);
      setAuthenticated(false);
    });
  }, []);

  const locked = authRequired === true && !authenticated;

  useEffect(() => {
    if (authRequired === null || locked) return;
    const tick = () => {
      api.queue().then(setQueue).catch(() => undefined);
      api
        .cloudStatus()
        .then((s) => {
          setCloudCount(s.queue.count);
          setCloudReady(s.ready_to_flush);
          setCloudOp(s.operation?.status === "running" ? "running" : null);
          setCloudEnabled(Boolean(s.config?.enabled));
        })
        .catch(() => undefined);
    };
    tick();
    const timer = setInterval(tick, 4000);
    return () => clearInterval(timer);
  }, [authRequired, locked]);

  const go = (hash: string) => {
    window.location.hash = hash;
  };

  const logout = () => {
    void api.authLogout().finally(() => setAuthenticated(false));
  };

  if (authRequired === null) {
    return (
      <div className="login-screen">
        <p className="muted">Đang tải…</p>
      </div>
    );
  }

  if (locked) {
    return <LoginScreen onLoggedIn={() => setAuthenticated(true)} />;
  }

  const statusBits: string[] = [];
  if (queue?.running) {
    statusBits.push(
      `Local: ${queue.running}${queue.pending ? ` · chờ ${queue.pending}` : ""}`,
    );
  } else if (queue?.pending) {
    statusBits.push(`Local chờ ${queue.pending}`);
  }
  if (cloudOp === "running") statusBits.push("Cloud đang render");
  else if (cloudCount > 0) {
    statusBits.push(
      cloudReady
        ? `Lịch cloud: ${cloudCount} job · sẵn sàng flush`
        : `Lịch cloud: ${cloudCount} job`,
    );
  }

  const showCloudNav = cloudEnabled || cloudCount > 0 || cloudOp === "running";

  return (
    <>
      <header className="app" role="banner">
        <div className="brand">
          <h1>OpenMontage</h1>
        </div>
        {statusBits.length > 0 && (
          <span className="status-pill">{statusBits.join(" · ")}</span>
        )}
        <nav aria-label="Chính">
          <button
            className={`ghost ${route.name === "projects" || route.name === "project" || route.name === "project-new" ? "active" : ""}`}
            aria-current={route.name === "projects" ? "page" : undefined}
            onClick={() => go("/projects")}
          >
            Nhà sáng tạo
          </button>
          <button
            className={`ghost ${route.name === "jobs" || route.name === "job" ? "active" : ""}`}
            onClick={() => go("/jobs")}
            title="Tất cả bản dựng trên máy — xem chéo project"
          >
            Tất cả bản dựng
          </button>
          <button
            className={`ghost ${route.name === "styles" ? "active" : ""}`}
            onClick={() => go("/styles")}
            title="Mẫu dựng dùng lại cho nhiều video"
          >
            Mẫu dựng
          </button>
          {showCloudNav && (
            <button
              className={`ghost ${route.name === "cloud" ? "active" : ""} ${cloudReady ? "nav-pulse" : ""}`}
              onClick={() => go("/cloud")}
              title="Lịch batch cloud — tắt trên máy này trừ khi bật Vast"
            >
              Lịch cloud
              {cloudCount > 0 ? ` (${cloudCount})` : ""}
              {cloudReady ? " ●" : ""}
            </button>
          )}
          <button
            className={`ghost ${route.name === "how-it-works" ? "active" : ""}`}
            onClick={() => go("/how-it-works")}
          >
            Hướng dẫn
          </button>
          {route.name !== "project-new" && route.name !== "job" && (
            <button
              className={route.name === "projects" ? "primary" : "ghost"}
              onClick={() => go("/projects/new")}
              title="Tạo project mới cho một nhà sáng tạo"
            >
              + Thêm nhà sáng tạo
            </button>
          )}
          {authRequired && (
            <button className="ghost" onClick={logout} title="Đăng xuất khỏi phiên hiện tại">
              Đăng xuất
            </button>
          )}
        </nav>
      </header>

      <main>
        {route.name === "projects" && (
          <ProjectListPage
            onOpen={(id) => go(`/project/${id}`)}
            onNew={() => go("/projects/new")}
          />
        )}
        {route.name === "project-new" && (
          <ProjectNewPage
            onCreated={(id) => go(`/project/${id}`)}
            onBack={() => go("/projects")}
          />
        )}
        {route.name === "project" && (
          <ProjectDetailPage
            projectId={route.id}
            onOpenJob={(id) => go(`/job/${id}`)}
            onDeleted={() => go("/projects")}
          />
        )}
        {route.name === "jobs" && <JobListPage onOpen={(id) => go(`/job/${id}`)} />}
        {route.name === "job" && <JobDetailPage jobId={route.id} />}
        {route.name === "cloud" && (
          <CloudQueuePage onOpenJob={(id) => go(`/job/${id}`)} />
        )}
        {route.name === "how-it-works" && <HowItWorksPage />}
        {route.name === "styles" && <StylesPage />}
      </main>
    </>
  );
};
