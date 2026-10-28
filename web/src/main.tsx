import { render } from "preact";
import { App } from "./app";
import { applyInk, savedInk } from "./ink";
import { registerWorker } from "./pwa";
import "./tokens.css";
import "./app.css";

applyInk(savedInk());   // before the first render: the page never shows another ink first
render(<App />, document.getElementById("app") as HTMLElement);
registerWorker();
