import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate, NavLink, useLocation, useNavigate } from "react-router-dom";
import { Activity, CircleHelp, ListOrdered, LockKeyhole, RefreshCw, Target, Wallet } from "lucide-react";
import { IntelligenceView, useIntelligence } from "./IntelligenceView";
import { InvestmentGuide, InvestmentRecordingPage, InvestmentView } from "./InvestmentView";
import type { ValueChangeSelection } from "./InvestmentView";
import { MonitoringWorkflowPage } from "./MonitoringWorkflow";
import { InvestmentSetup } from "./InvestmentSetup";
import { EToroPortfolioPanel } from "./EToroPortfolio";
import { PlanningProfilePage } from "./PlanningProfile";
import { AccountUpdatePage } from "./AccountUpdate";
import { timestamp } from "./api";

export function App() {
  const client = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const workspace = useQuery({
    queryKey: ["workspace"],
    queryFn: async ({ signal }) => {
      const response = await fetch("/api/v1/settings", {
        signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]), cache: "no-store",
      });
      if (!response.ok) throw new Error("Workspace settings are unavailable.");
      const settings: unknown = await response.json();
      if (typeof settings !== "object" || settings === null || !("workspace" in settings)
        || (settings.workspace !== "demo" && settings.workspace !== "investment" && settings.workspace !== "setup")) {
        throw new Error("Workspace settings failed validation.");
      }
      return settings.workspace;
    },
  });
  const intelligencePage = location.pathname === "/intelligence";
  const intelligence = useIntelligence(intelligencePage && workspace.isSuccess && workspace.data !== "setup");
  const [now, setNow] = useState(Date.now());
  const [valueChangeSelection, setValueChangeSelection] = useState<ValueChangeSelection | null>(null);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 60000);
    return () => clearInterval(timer);
  }, []);

  if (workspace.isError) return <main className="setup-shell">
    <div className="error-state" role="alert">
      <p>Workspace settings are unavailable; portfolio source is unknown.</p>
      <button onClick={() => void workspace.refetch()}>Retry</button>
    </div>
    <details className="help-disclosure" id="workspace-etoro">
      <summary>Optional: eToro status and revocation</summary>
      <EToroPortfolioPanel revokeOnly />
    </details>
  </main>;
  if (workspace.isPending) return <main className="loading" role="status">Loading portfolio workspace</main>;
  if (location.pathname === "/etoro-portfolio") return <Navigate
    to={workspace.data === "setup" ? "/setup#setup-etoro" : "/investment#investment-etoro"} replace />;
  if (workspace.data === "setup" || location.pathname === "/setup") return <div className="setup-shell">
    <main><InvestmentSetup onComplete={() => {
      void Promise.all([
        client.invalidateQueries({ queryKey: ["workspace"] }),
        client.invalidateQueries({ queryKey: ["investment"] }),
      ]).then(() => navigate("/investment", { replace: true }));
    }} /></main>
  </div>;
  if (location.pathname === "/") return <Navigate to="/investment" replace />;

  const pages: Record<string, string> = {
    "/investment": "Your portfolio",
    "/investment/record": "Add a record",
    "/investment/update": "Update an account",
    "/investment/reviews": "Review checks",
    "/investment-guide": "Help",
    "/planning": "Goals",
    "/intelligence": "Market context",
  };
  const pageName = pages[location.pathname];
  const intelligenceMode = intelligence.isError ? undefined : intelligence.data?.source_mode;
  const intelligenceStatus = intelligence.isError ? "Source unavailable"
    : intelligence.isFetching ? intelligence.data ? "Refreshing intelligence" : "Checking source"
    : intelligence.data ? `Snapshot ${timestamp(intelligence.data.generated_at)}` : "Source unknown";
  return <div className="shell">
    <aside className="sidebar">
      <NavLink to="/investment" className="brand" aria-label="GoldScout Portfolio Manager">
        <span className="brand-mark"><Activity size={24} /></span>
        <span className="brand-name">GoldScout<small>Portfolio Manager</small></span>
      </NavLink>
      <div className="nav-groups">
        <div className="nav-section"><span className="nav-section-label">PORTFOLIO</span>
          <nav aria-label="Portfolio navigation">
            <NavLink to="/investment" end><Wallet size={18} /><span>Your portfolio</span></NavLink>
            {workspace.data === "investment" && <NavLink to="/planning"><Target size={18} /><span>Goals</span></NavLink>}
            <NavLink to="/investment-guide"><CircleHelp size={18} /><span>Help</span></NavLink>
          </nav>
        </div>
        <div className="nav-section advisory-nav-section"><span className="nav-section-label">OPTIONAL</span>
          <nav aria-label="Optional tools navigation">
            <NavLink to="/investment/reviews"><ListOrdered size={18} /><span>Review checks</span></NavLink>
            <NavLink to="/intelligence"><Activity size={18} /><span>Market context</span></NavLink>
          </nav>
        </div>
      </div>
    </aside>
    <div className="workspace">
      <header className="topbar"><div className="breadcrumb">Portfolio <span>/</span> <strong>{pageName || "Page not found"}</strong></div>
        {intelligencePage && <div className="topbar-status">
          <span className={`mode ${intelligenceMode === "demo" ? "demo" : ""}`}>{intelligenceMode === "demo" ? "SYNTHETIC DEMO" : intelligenceMode === "recorded" ? "RECORDED INTELLIGENCE" : intelligence.isFetching ? "Checking source" : "Source unknown"}</span>
          <span className="connection" aria-live="polite">{intelligenceStatus}</span>
        </div>}
      </header>
      <main>
        <span className="sr-only" id="table-scroll-instructions">Scroll horizontally to see all columns.</span>
        <div className="page-heading"><div>
          <h1>{pageName || "Page not found"}</h1>
        </div>{intelligencePage && <button className="icon-button refresh" aria-label="Refresh data" title="Refresh data"
          disabled={intelligence.isFetching} onClick={() => {
            void client.invalidateQueries({ queryKey: ["intelligence"] });
            void client.invalidateQueries({ queryKey: ["analysis-session"] });
          }}><RefreshCw size={19} className={intelligence.isFetching ? "spin" : ""} /></button>}</div>
        {location.pathname === "/investment" ? <InvestmentView now={now}
          valueChangeSelection={valueChangeSelection} setValueChangeSelection={setValueChangeSelection} />
          : location.pathname === "/investment/record" ? <InvestmentRecordingPage />
          : location.pathname === "/investment/update" ? <AccountUpdatePage now={now} />
          : location.pathname === "/investment/reviews" ? <MonitoringWorkflowPage />
          : location.pathname === "/investment-guide" ? <InvestmentGuide />
          : location.pathname === "/planning" ? <PlanningProfilePage />
          : intelligencePage ? <IntelligenceView data={intelligence} />
          : <div className="error-state" role="alert"><p>This page is not part of GoldScout.</p><NavLink to="/investment">Open investment records</NavLink></div>}
        <footer><span>GoldScout <span className="footer-separator">/</span> <NavLink to="/investment-guide">Help</NavLink></span>
          <span><LockKeyhole size={12} />Local records · no trades or money movement</span></footer>
      </main>
    </div>
  </div>;
}
