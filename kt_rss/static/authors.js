/* Skribentöversikten (/a). Sökrutan filtrerar grupperna - alla skribenter
   renderas direkt och döljs/visas via hidden. En grupp vars alla namn är
   bortfiltrerade döljs i sin helhet. Vanilla JS, inget ramverk. */
(function () {
  "use strict";

  var search = document.querySelector(".author-search");
  var groups = document.querySelectorAll(".author-group");
  if (!search || !groups.length) return;

  search.addEventListener("input", function () {
    var q = search.value.trim().toLowerCase();
    groups.forEach(function (group) {
      var visible = 0;
      group.querySelectorAll("li").forEach(function (item) {
        var name = (item.dataset.name || "").toLowerCase();
        var hit = q === "" || name.indexOf(q) !== -1;
        item.hidden = !hit;
        if (hit) visible += 1;
      });
      group.hidden = visible === 0;
    });
  });
})();
