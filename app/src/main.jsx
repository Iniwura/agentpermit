import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { PermitProvider } from "./state";
import App from "./App";
import "./style.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode><BrowserRouter><PermitProvider><App /></PermitProvider></BrowserRouter></React.StrictMode>,
);
