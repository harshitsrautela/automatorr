import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import RequestsPage from "./credit/RequestsPage.jsx";
import LedgerPage from "./credit/LedgerPage.jsx";

// Picks the page from the URL path: / is the credit requests page, /ledger the retailer ledger, /people the original DataDesk app.
function Root() {
  const path = window.location.pathname.replace(/\/+$/, "");
  if (path === "/ledger") return <LedgerPage />;
  if (path === "/people") return <App />;
  return <RequestsPage />;
}

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <Root />
  </StrictMode>
);
