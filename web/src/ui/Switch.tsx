/**
 * On or off, at once (a practice on its schedule, the today block). A real `role="switch"` button: Space and Enter
 * toggle it, and it is named by `label` or by the row's own label (`labelledBy`). His lamp when on. A setting that only
 * takes effect on Save is not a switch's job alone: the row says so (the save bar).
 */
import "./Switch.css";

export function Switch({ checked, onChange, label, labelledBy, disabled, id }: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label?: string;
  labelledBy?: string;
  disabled?: boolean;
  id?: string;
}) {
  return (
    <button type="button" role="switch" id={id} class="switch" aria-checked={checked} aria-label={labelledBy ? undefined : label}
      aria-labelledby={labelledBy} disabled={disabled} onClick={() => onChange(!checked)} />
  );
}
