/* Mörk/ljus-temaväxling. Utan ett sparat val följer sidan OS:et via
   prefers-color-scheme (ren CSS). Knappen sätter ett explicit val som
   sparas i localStorage och appliceras redan i <head> - FOUC-fritt. */
(function () {
  "use strict";

  var btn = document.querySelector(".theme-toggle");
  if (!btn) return;

  btn.addEventListener("click", function () {
    var root = document.documentElement;
    /* Effektivt tema just nu: explicit val, annars OS-inställningen. */
    var current = root.dataset.theme ||
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    var next = current === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    localStorage.setItem("theme", next);
  });
})();
