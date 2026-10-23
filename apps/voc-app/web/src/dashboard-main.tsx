import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { DashboardPage } from "./DashboardPage";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <DashboardPage />
  </StrictMode>,
);
