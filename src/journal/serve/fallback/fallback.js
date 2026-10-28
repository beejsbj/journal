// The placeholder page journal serve shows until the web app is built (WP11). It does one job: pairing.
// `journal serve pair` prints https://<host>/pair#<code>; the code rides in the fragment, which the browser never
// sends, and this script posts it once to exchange it for the session cookie.
(function () {
  var status = document.getElementById("status");
  if (location.pathname !== "/pair") return;
  var code = location.hash.slice(1);
  history.replaceState(null, "", "/pair");
  if (!code) {
    status.textContent = "Open the link `journal serve pair` printed; this page needs its code.";
    return;
  }
  status.textContent = "Pairing…";
  fetch("/api/v1/pair", {
    method: "POST",
    credentials: "same-origin",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({token: code})
  }).then(function (r) {
    status.textContent = r.status === 204
      ? "Paired: this browser can read the journal now."
      : "That link is unknown, used or expired. Run `journal serve pair` again.";
  }, function () {
    status.textContent = "The server could not be reached.";
  });
})();
