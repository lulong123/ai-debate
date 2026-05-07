import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { App } from "./App";
import { Home } from "./pages/Home";
import { Positions } from "./pages/Positions";
import { Discussion } from "./pages/Discussion";
import { Minutes } from "./pages/Minutes";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<App />}>
          <Route index element={<Home />} />
          <Route path="positions/:sessionId" element={<Positions />} />
          <Route path="discussion/:sessionId" element={<Discussion />} />
          <Route path="minutes/:sessionId" element={<Minutes />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>
);
