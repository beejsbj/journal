// the styles first, in this order: the tokens, the base, then the old sheet the unconverted routes still read; every
// component's own sheet comes after them (imported by its TSX), so the design system wins a tie with the old sheet
import "./tokens.css";
import "./styles/base.css";
import "./styles/legacy.css";
import { render } from "preact";
import { App } from "./app";
import { applyInk, savedInk } from "./ink";
import { registerWorker } from "./pwa";
import { applyTheme, savedTheme } from "./theme";

applyInk(savedInk());   // before the first render: the page never shows another ink first
applyTheme(savedTheme());   // nor another theme
render(<App />, document.getElementById("app") as HTMLElement);
registerWorker();
