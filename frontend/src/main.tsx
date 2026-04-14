import "bootstrap/dist/css/bootstrap.min.css";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import "./styles/theme.css";

import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import { CreditFlowProvider } from "./context/CreditFlowContext";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <CreditFlowProvider>
      <App />
    </CreditFlowProvider>
  </React.StrictMode>
);
