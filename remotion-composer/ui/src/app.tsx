import React, { useEffect, useState } from "react";
import { api } from "./api/client";
import { JobListPage } from "./pages/job-list";
import { JobDetailPage } from "./pages/job-detail";
import { NewJobPage } from "./pages/new-job";
import { ProjectDetailPage } from "./pages/project-detail";
import { ProjectListPage } from "./pages/project-list";
import { ProjectNewPage } from "./pages/project-new";
import { HowItWorksPage } from "./pages/how-it-works";
import { CloudQueuePage } from "./pages/cloud-queue";

type Route =
  | { name: "jobs" }
  | { name: "new" }
  | { name: "job"; id: string }
  | { name: "projects" }
  | { name: "project-new" }
  | { name: "project"; id: string }
  | { name: "how-it-works" }
  | { name: "cloud" };

const parseHash = (): Route => {
  const hash = window.location.hash.replace(/^#\/?/, "");
  if (hash.startsWith("job/")) return { name: "job", id: hash.slice(4) };
  if (hash.startsWith("project/")) return { name: "project", id: hash.slice(8) };
  if (hash === "projects/new") return { name: "project-new" };
  if (hash === "projects") return { name: "projects" };
  if (hash === "jobs") return { name: "jobs" };
  if (hash === "new") return { name: "new" };
  if (hash === "how-it-works") return { name: "how-it-works" };
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

  useEffect(() => {
    const onHashChange = () => setRoute(parseHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    const tick = () => {
      api.queue().then(setQueue).catch(() => undefined);
      api
        .cloudStatus()
        .then((s) => {
          setCloudCount(s.queue.count);
          setCloudReady(s.ready_to_flush);
          setCloudOp(s.operation?.status === "running" ? "running" : null);
        })
        .catch(() => undefined);
    };
    tick();
    const timer = setInterval(tick, 4000);
    return () => clearInterval(timer);
  }, []);

  const go = (hash: string) => {
    window.location.hash = hash;
  };

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
  if (statusBits.length === 0) statusBits.push("Không có job nào đang chạy");

  return (
    <>
      <header className="app">
        <h1>🎬 Auto-edit talking head</h1>
        <span className="muted small">{statusBits.join(" · ")}</span>
        <nav>
          <button className="ghost" onClick={() => go("/projects")}>Project</button>
          <button className="ghost" onClick={() => go("/jobs")}>Tất cả bản dựng</button>
          <button
            className={`ghost ${cloudReady ? "nav-pulse" : ""}`}
            onClick={() => go("/cloud")}
            title="Lịch batch cloud — xếp job, flush 1 rental"
          >
            Lịch render
            {cloudCount > 0 ? ` (${cloudCount})` : ""}
            {cloudReady ? " ●" : ""}
          </button>
          <button className="ghost" onClick={() => go("/how-it-works")}>Cơ chế hoạt động</button>
          <button className="primary" onClick={() => go("/projects/new")}>+ Project</button>
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
          <ProjectNewPage onCreated={(id) => go(`/project/${id}`)} />
        )}
        {route.name === "project" && (
          <ProjectDetailPage
            projectId={route.id}
            onOpenJob={(id) => go(`/job/${id}`)}
            onDeleted={() => go("/projects")}
          />
        )}
        {route.name === "jobs" && <JobListPage onOpen={(id) => go(`/job/${id}`)} />}
        {route.name === "new" && <NewJobPage onCreated={(id) => go(`/job/${id}`)} />}
        {route.name === "job" && <JobDetailPage jobId={route.id} />}
        {route.name === "cloud" && (
          <CloudQueuePage onOpenJob={(id) => go(`/job/${id}`)} />
        )}
        {route.name === "how-it-works" && <HowItWorksPage />}
      </main>
    </>
  );
};
