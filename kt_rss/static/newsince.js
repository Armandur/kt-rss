/* Markerar artiklar som publicerats sedan förra besöket med en "Ny"-pill.
   Jämförelsepunkten fryses i sessionStorage vid sessionens första
   sidladdning - alla vyer i samma session markerar då mot samma tidpunkt,
   och localStorage stämplas om till nu inför nästa besök. Vanilla JS. */
(function () {
  "use strict";

  var KEY = "kt-last-visit";
  var baseline = sessionStorage.getItem(KEY);
  if (baseline === null) {
    baseline = localStorage.getItem(KEY) || "";
    sessionStorage.setItem(KEY, baseline);
    localStorage.setItem(KEY, new Date().toISOString());
  }
  if (!baseline) return; // första besöket - inget att jämföra mot

  var cutoff = new Date(baseline).getTime();
  document.querySelectorAll("article[data-published]").forEach(function (el) {
    if (new Date(el.dataset.published).getTime() <= cutoff) return;
    var meta = el.querySelector(".article-meta");
    if (!meta) return;
    var flag = document.createElement("span");
    flag.className = "new-flag";
    flag.textContent = "Ny";
    meta.insertBefore(flag, meta.firstChild);
  });
})();
