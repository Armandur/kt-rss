/* "Upp"-knapp: en flytande knapp som tar tillbaka till sidans topp. Dyker
   upp först när man scrollat en bit, så den inte stör högst upp.
   Vanilla JS, inget ramverk. */
(function () {
  "use strict";

  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "to-top";
  btn.setAttribute("aria-label", "Till toppen");
  btn.textContent = "↑";
  btn.addEventListener("click", function () {
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
  document.body.appendChild(btn);

  function update() {
    btn.classList.toggle("visible", window.scrollY > 600);
  }
  window.addEventListener("scroll", update, { passive: true });
  update();
})();
