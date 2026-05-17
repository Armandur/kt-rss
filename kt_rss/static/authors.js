/* Skribentöversikten (/a). Sökrutan filtrerar listan - alla skribenter
   renderas direkt och döljs/visas via hidden. Vanilla JS, inget ramverk. */
(function () {
  "use strict";

  var search = document.querySelector(".author-search");
  var list = document.querySelector(".author-list");
  if (!search || !list) return;

  search.addEventListener("input", function () {
    var q = search.value.trim().toLowerCase();
    list.querySelectorAll("li").forEach(function (item) {
      var name = (item.dataset.name || "").toLowerCase();
      item.hidden = q !== "" && name.indexOf(q) === -1;
    });
  });
})();
