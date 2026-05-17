/* Sökrutans autocomplete: visar matchande taggar och skribenter medan man
   skriver, hämtade från /suggest. Ett klick navigerar till tagg- eller
   skribentvyn; Enter i rutan gör fortfarande en vanlig artikelsök.
   Vanilla JS, inget ramverk. */
(function () {
  "use strict";

  var form = document.querySelector(".searchbox");
  if (!form) return;
  var input = form.querySelector("input[name='q']");
  if (!input) return;

  var box = document.createElement("div");
  box.className = "suggest-box";
  box.style.display = "none";
  form.appendChild(box);

  function hide() {
    box.style.display = "none";
    box.textContent = "";
  }

  function render(data) {
    box.textContent = "";
    var rows = [];
    (data.tags || []).forEach(function (t) {
      rows.push(["/t/" + encodeURIComponent(t), t, "tagg"]);
    });
    (data.authors || []).forEach(function (a) {
      rows.push(["/a/" + encodeURIComponent(a), a, "skribent"]);
    });
    if (!rows.length) {
      hide();
      return;
    }
    rows.forEach(function (r) {
      var item = document.createElement("a");
      item.className = "suggest-item";
      item.href = r[0];
      var name = document.createElement("span");
      name.textContent = r[1];
      var kind = document.createElement("span");
      kind.className = "suggest-kind";
      kind.textContent = r[2];
      item.appendChild(name);
      item.appendChild(kind);
      box.appendChild(item);
    });
    box.style.display = "block";
  }

  var timer = null;
  input.addEventListener("input", function () {
    clearTimeout(timer);
    var q = input.value.trim();
    if (q.length < 2) {
      hide();
      return;
    }
    timer = setTimeout(function () {
      fetch("/suggest?q=" + encodeURIComponent(q))
        .then(function (resp) { return resp.json(); })
        .then(render)
        .catch(hide);
    }, 180);
  });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Escape") hide();
  });
  document.addEventListener("click", function (e) {
    if (!form.contains(e.target)) hide();
  });
})();
