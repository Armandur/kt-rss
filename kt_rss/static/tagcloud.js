/* Tagg-översikten (/t). Sökrutan filtrerar molnet - alla taggar renderas
   direkt och döljs/visas via hidden. Vanilla JS, inget ramverk. */
(function () {
  "use strict";

  var search = document.querySelector(".tag-search");
  var cloud = document.querySelector(".tag-cloud");
  if (!search || !cloud) return;

  search.addEventListener("input", function () {
    var q = search.value.trim().toLowerCase();
    cloud.querySelectorAll(".tag-cloud-item").forEach(function (item) {
      var tag = (item.dataset.tag || "").toLowerCase();
      item.hidden = q !== "" && tag.indexOf(q) === -1;
    });
  });
})();
