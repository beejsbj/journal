// the styles first: the tokens, then the base; every component's and route's own sheet comes after them (imported by
// its TSX), so a component's rule wins a tie with the base
import "./tokens.css";
import "./styles/base.css";
import { render } from "preact";
import { App } from "./app";
import { applyInk, savedInk } from "./ink";
import { registerWorker } from "./pwa";
import { applyTheme, savedTheme } from "./theme";

applyInk(savedInk());   // before the first render: the page never shows another ink first
applyTheme(savedTheme());   // nor another theme
render(<App />, document.getElementById("app") as HTMLElement);
registerWorker();
