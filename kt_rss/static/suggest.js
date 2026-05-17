/* Sökrutans autocomplete: visar matchande taggar och skribenter medan man
   skriver, hämtade från /suggest. Pil upp/ner markerar ett förslag och
   Enter väljer det; ett klick gör samma. Enter utan markering gör en
   vanlig artikelsök. Vanilla JS, inget ramverk. */
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

  var active = -1; // index för markerat förslag, -1 = inget

  function items() {
    return box.querySelectorAll(".suggest-item");
  }

  function hide() {
    box.style.display = "none";
    box.textContent = "";
    active = -1;
  }

  function setActive(idx) {
    var els = items();
    if (!els.length) return;
    if (idx < 0) idx = els.length - 1;
    if (idx >= els.length) idx = 0;
    els.forEach(function (el, i) {
      el.classList.toggle("active", i === idx);
    });
    active = idx;
    els[idx].scrollIntoView({ block: "nearest" });
  }

  function render(data) {
    box.textContent = "";
    active = -1;
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
    if (e.key === "Escape") {
      hide();
      return;
    }
    if (box.style.display === "none") return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive(active + 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive(active - 1);
    } else if (e.key === "Enter") {
      var els = items();
      if (active >= 0 && active < els.length) {
        e.preventDefault();
        window.location.href = els[active].href;
      }
    }
  });

  box.addEventListener("mouseover", function (e) {
    var el = e.target.closest(".suggest-item");
    if (!el) return;
    items().forEach(function (item, i) {
      if (item === el) setActive(i);
    });
  });

  document.addEventListener("click", function (e) {
    if (!form.contains(e.target)) hide();
  });
})();
